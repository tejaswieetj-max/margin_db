"""
compute_event_windows.py — Compute EVENT_WINDOW_MONTH rows for game-launch events.

Reads:
  - BUSINESS_EVENT (game_launch events with non-NULL steam_app_id)
  - STEAM_PLAYER   (monthly avg_players by app_id)
  - CONFIG         (EVENT_PRE_MONTHS, EVENT_POST_MONTHS, BASELINE_MONTHS,
                    MIN_BASELINE_MONTHS)

For each qualifying event:
  - Computes month offsets  −EVENT_PRE_MONTHS .. +EVENT_POST_MONTHS
  - Offset 0 = the calendar month (year+month) of event.start_date
  - Baseline = mean of avg_players over the BASELINE_MONTHS months immediately
    before offset 0  (offsets −BASELINE_MONTHS .. −1)
  - lift_ratio = avg_players_at_offset / baseline_mean
    lift_ratio is NULL when:
      • Fewer than MIN_BASELINE_MONTHS baseline months exist in STEAM_PLAYER
        for this app_id, OR
      • Any required baseline month is absent in STEAM_PLAYER
    Missing months are NEVER treated as zero or interpolated.
  - INSERT IGNORE into EVENT_WINDOW_MONTH (idempotent; PK is (event_id, month_offset))
  - Log missing months to stdout (via logging) AND to
    data/clean/lift_warnings_<event_id>.log

Requirements: 4.2
"""

import logging
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import mysql.connector
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"

# ---------------------------------------------------------------------------
# Logging — UTC timestamps
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
logger = logging.getLogger(__name__)


def _make_event_file_logger(event_id: int) -> tuple[logging.Logger, logging.FileHandler]:
    """Create a per-event file logger writing to data/clean/lift_warnings_<event_id>.log."""
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    log_path = CLEAN_DIR / f"lift_warnings_{event_id}.log"

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    fmt = logging.Formatter(
        "%(asctime)s UTC %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fmt.converter = lambda *args: datetime.now(timezone.utc).timetuple()
    file_handler.setFormatter(fmt)

    event_logger = logging.getLogger(f"event_{event_id}")
    event_logger.setLevel(logging.WARNING)
    # Avoid adding duplicate handlers on repeated calls (e.g. in tests)
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path)
               for h in event_logger.handlers):
        event_logger.addHandler(file_handler)

    return event_logger, file_handler


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------


def get_connection(env: dict) -> mysql.connector.MySQLConnection:
    """Open a mysql-connector-python connection using credentials from env."""
    return mysql.connector.connect(
        host=env["DB_HOST"],
        port=int(env.get("DB_PORT", 3306)),
        database=env["DB_NAME"],
        user=env["ADMIN_USER"],
        password=env["ADMIN_PASSWORD"],
        charset="utf8mb4",
        autocommit=False,
    )


# ---------------------------------------------------------------------------
# CONFIG helpers
# ---------------------------------------------------------------------------


def load_config(cursor) -> dict[str, int]:
    """Read required CONFIG keys from the database.

    Returns a dict of {config_key: int_value}.
    Exits with error if any required key is missing.
    """
    required_keys = (
        "EVENT_PRE_MONTHS",
        "EVENT_POST_MONTHS",
        "BASELINE_MONTHS",
        "MIN_BASELINE_MONTHS",
    )
    cursor.execute(
        "SELECT config_key, config_value FROM CONFIG WHERE config_key IN (%s, %s, %s, %s)",
        required_keys,
    )
    rows = cursor.fetchall()
    config: dict[str, int] = {}
    for key, value in rows:
        try:
            config[key] = int(value)
        except (ValueError, TypeError) as exc:
            logger.error(
                "CONFIG key %s has non-integer value %r: %s", key, value, exc
            )
            sys.exit(1)

    missing = [k for k in required_keys if k not in config]
    if missing:
        logger.error("Missing required CONFIG keys: %s", missing)
        sys.exit(1)

    logger.info(
        "CONFIG loaded: EVENT_PRE_MONTHS=%d, EVENT_POST_MONTHS=%d, "
        "BASELINE_MONTHS=%d, MIN_BASELINE_MONTHS=%d",
        config["EVENT_PRE_MONTHS"],
        config["EVENT_POST_MONTHS"],
        config["BASELINE_MONTHS"],
        config["MIN_BASELINE_MONTHS"],
    )
    return config


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def load_qualifying_events(cursor) -> list[dict]:
    """Load game_launch events that have a non-NULL steam_app_id.

    Returns list of dicts with keys: event_id, name, start_date, steam_app_id.
    """
    cursor.execute(
        """
        SELECT event_id, name, start_date, steam_app_id
        FROM BUSINESS_EVENT
        WHERE event_type = 'game_launch'
          AND steam_app_id IS NOT NULL
        ORDER BY event_id
        """
    )
    events = [
        {
            "event_id": row[0],
            "name": row[1],
            "start_date": row[2],      # datetime.date object from mysql-connector
            "steam_app_id": row[3],
        }
        for row in cursor.fetchall()
    ]
    logger.info(
        "Found %d qualifying game_launch event(s) with non-NULL steam_app_id.",
        len(events),
    )
    return events


def load_steam_players_for_app(cursor, app_id: int) -> dict[tuple[int, int], Decimal]:
    """Load all STEAM_PLAYER rows for a given app_id.

    Returns a dict mapping (year, month) → avg_players (Decimal).
    Only rows with non-NULL avg_players are included.
    """
    cursor.execute(
        """
        SELECT obs_date, avg_players
        FROM STEAM_PLAYER
        WHERE app_id = %s
          AND avg_players IS NOT NULL
        ORDER BY obs_date
        """,
        (app_id,),
    )
    result: dict[tuple[int, int], Decimal] = {}
    for obs_date, avg_players in cursor.fetchall():
        # obs_date is always the 1st of the month; key by (year, month)
        if isinstance(obs_date, str):
            obs_date = date.fromisoformat(obs_date)
        result[(obs_date.year, obs_date.month)] = Decimal(str(avg_players))
    return result


# ---------------------------------------------------------------------------
# Month offset arithmetic
# ---------------------------------------------------------------------------


def calendar_month_for_offset(reference_date: date, offset: int) -> tuple[int, int]:
    """Return the (year, month) for offset N months from reference_date.

    Offset 0 = the calendar month of reference_date.
    Uses dateutil.relativedelta for correct month arithmetic (no day overflow).
    """
    target = reference_date + relativedelta(months=offset)
    return target.year, target.month


# ---------------------------------------------------------------------------
# Window computation
# ---------------------------------------------------------------------------


def compute_window_for_event(
    event: dict,
    steam_data: dict[tuple[int, int], Decimal],
    config: dict[str, int],
    event_logger: logging.Logger,
) -> list[dict]:
    """Compute EVENT_WINDOW_MONTH rows for a single event.

    Returns a list of dicts, one per month offset, with keys:
      event_id, month_offset, avg_players (Decimal or None), lift_ratio (Decimal or None)

    Lift ratio logic:
      1. Collect baseline months: offsets −BASELINE_MONTHS .. −1
      2. If any baseline month is missing from steam_data → lift_ratio = NULL for ALL offsets
      3. If present baseline months < MIN_BASELINE_MONTHS → lift_ratio = NULL for ALL offsets
      4. Otherwise baseline_mean = mean of avg_players over baseline months
         lift_ratio for each offset = avg_players_at_offset / baseline_mean
         If baseline_mean is 0 or NULL → lift_ratio = NULL
      5. If a non-baseline offset month is missing, avg_players = NULL, lift_ratio = NULL
    """
    event_id = event["event_id"]
    start_date = event["start_date"]
    # Ensure start_date is a date object (mysql-connector may return datetime.date)
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)

    pre = config["EVENT_PRE_MONTHS"]
    post = config["EVENT_POST_MONTHS"]
    baseline_months = config["BASELINE_MONTHS"]
    min_baseline = config["MIN_BASELINE_MONTHS"]

    # ------------------------------------------------------------------
    # Step 1: collect baseline month keys (offsets −BASELINE_MONTHS .. −1)
    # ------------------------------------------------------------------
    baseline_offsets = list(range(-baseline_months, 0))  # e.g. [-3, -2, -1]
    baseline_ym_keys = [
        calendar_month_for_offset(start_date, off) for off in baseline_offsets
    ]

    # ------------------------------------------------------------------
    # Step 2: check for missing baseline months
    # ------------------------------------------------------------------
    missing_baseline = [ym for ym in baseline_ym_keys if ym not in steam_data]

    if missing_baseline:
        for ym in missing_baseline:
            msg = (
                f"event_id={event_id} ({event['name']!r}): "
                f"baseline month {ym[0]}-{ym[1]:02d} missing from STEAM_PLAYER "
                f"for app_id={event['steam_app_id']} — lift_ratio will be NULL"
            )
            logger.warning(msg)
            event_logger.warning(msg)

    # ------------------------------------------------------------------
    # Step 3: count available baseline months
    # ------------------------------------------------------------------
    available_baseline = [ym for ym in baseline_ym_keys if ym in steam_data]
    n_available = len(available_baseline)

    # ------------------------------------------------------------------
    # Step 4: decide whether baseline is usable
    # ------------------------------------------------------------------
    compute_lift = True

    if missing_baseline:
        # Any required baseline month absent → NULL lift for all offsets
        msg = (
            f"event_id={event_id} ({event['name']!r}): "
            f"{len(missing_baseline)} baseline month(s) absent; "
            f"lift_ratio = NULL for all offsets (missing months never interpolated)"
        )
        logger.warning(msg)
        event_logger.warning(msg)
        compute_lift = False

    elif n_available < min_baseline:
        # Fewer than MIN_BASELINE_MONTHS available → NULL lift
        msg = (
            f"event_id={event_id} ({event['name']!r}): "
            f"only {n_available} baseline month(s) available "
            f"(MIN_BASELINE_MONTHS={min_baseline}); "
            f"lift_ratio = NULL for all offsets"
        )
        logger.warning(msg)
        event_logger.warning(msg)
        compute_lift = False

    # ------------------------------------------------------------------
    # Step 5: compute baseline mean if eligible
    # ------------------------------------------------------------------
    baseline_mean: Optional[Decimal] = None
    if compute_lift:
        total = sum(steam_data[ym] for ym in available_baseline)
        baseline_mean = total / Decimal(n_available)
        if baseline_mean == 0:
            msg = (
                f"event_id={event_id} ({event['name']!r}): "
                f"baseline_mean is 0; lift_ratio = NULL for all offsets"
            )
            logger.warning(msg)
            event_logger.warning(msg)
            compute_lift = False
            baseline_mean = None

    # ------------------------------------------------------------------
    # Step 6: build window rows for all offsets −pre .. +post
    # ------------------------------------------------------------------
    rows: list[dict] = []

    for offset in range(-pre, post + 1):
        ym = calendar_month_for_offset(start_date, offset)
        avg_players = steam_data.get(ym)  # None if month absent

        if avg_players is None and offset not in baseline_offsets:
            # Missing non-baseline offset month — log it
            msg = (
                f"event_id={event_id} ({event['name']!r}): "
                f"offset {offset:+d} month {ym[0]}-{ym[1]:02d} missing from "
                f"STEAM_PLAYER for app_id={event['steam_app_id']}"
            )
            logger.warning(msg)
            event_logger.warning(msg)

        # Compute lift_ratio for this offset
        if compute_lift and avg_players is not None and baseline_mean is not None:
            lift_ratio = avg_players / baseline_mean
        else:
            lift_ratio = None

        rows.append({
            "event_id": event_id,
            "month_offset": offset,
            "avg_players": avg_players,
            "lift_ratio": lift_ratio,
        })

    return rows


# ---------------------------------------------------------------------------
# Database write
# ---------------------------------------------------------------------------

INSERT_SQL = """
INSERT IGNORE INTO EVENT_WINDOW_MONTH
    (event_id, month_offset, avg_players, lift_ratio)
VALUES
    (%s, %s, %s, %s)
"""


def insert_window_rows(
    cursor,
    conn: mysql.connector.MySQLConnection,
    rows: list[dict],
    event_id: int,
) -> int:
    """INSERT IGNORE window rows for one event. Returns count of rows inserted."""
    if not rows:
        return 0

    params = [
        (
            r["event_id"],
            r["month_offset"],
            float(r["avg_players"]) if r["avg_players"] is not None else None,
            float(r["lift_ratio"]) if r["lift_ratio"] is not None else None,
        )
        for r in rows
    ]
    cursor.executemany(INSERT_SQL, params)
    conn.commit()
    inserted = cursor.rowcount
    logger.info(
        "event_id=%d: %d row(s) attempted, %d row(s) inserted "
        "(INSERT IGNORE; existing rows skipped)",
        event_id,
        len(rows),
        inserted,
    )
    return inserted


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def process_all_events(conn: mysql.connector.MySQLConnection) -> dict:
    """Main logic: load config + events, compute windows, insert rows.

    Returns a summary dict with keys:
      events_processed, total_rows_attempted, total_rows_inserted
    """
    cursor = conn.cursor()
    try:
        # 1. Read CONFIG (fail-fast if any key missing)
        config = load_config(cursor)

        # 2. Load qualifying events
        events = load_qualifying_events(cursor)

        if not events:
            logger.warning(
                "No qualifying game_launch events with non-NULL steam_app_id found. "
                "EVENT_WINDOW_MONTH will not be populated."
            )
            return {
                "events_processed": 0,
                "total_rows_attempted": 0,
                "total_rows_inserted": 0,
            }

        total_rows_attempted = 0
        total_rows_inserted = 0

        for event in events:
            event_id = event["event_id"]
            app_id = event["steam_app_id"]

            logger.info(
                "Processing event_id=%d %r (app_id=%d, start_date=%s)",
                event_id,
                event["name"],
                app_id,
                event["start_date"],
            )

            # Set up per-event file logger
            event_logger, file_handler = _make_event_file_logger(event_id)

            try:
                # 3. Load STEAM_PLAYER data for this app
                steam_data = load_steam_players_for_app(cursor, app_id)
                logger.info(
                    "  Loaded %d STEAM_PLAYER month(s) for app_id=%d",
                    len(steam_data),
                    app_id,
                )

                # 4. Compute window rows
                window_rows = compute_window_for_event(
                    event, steam_data, config, event_logger
                )

                # 5. Insert with INSERT IGNORE
                total_rows_attempted += len(window_rows)
                inserted = insert_window_rows(cursor, conn, window_rows, event_id)
                total_rows_inserted += inserted

            finally:
                # Remove and close the per-event file handler
                event_logger.removeHandler(file_handler)
                file_handler.close()

        return {
            "events_processed": len(events),
            "total_rows_attempted": total_rows_attempted,
            "total_rows_inserted": total_rows_inserted,
        }

    except mysql.connector.Error as exc:
        logger.error("Database error: %s", exc)
        conn.rollback()
        raise
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    load_dotenv()

    # Collect required environment variables
    required_env = ["DB_HOST", "DB_NAME", "ADMIN_USER", "ADMIN_PASSWORD"]
    env: dict = {}
    missing_env: list[str] = []
    for key in required_env:
        val = os.environ.get(key, "").strip()
        if not val:
            missing_env.append(key)
        env[key] = val
    env["DB_PORT"] = os.environ.get("DB_PORT", "3306").strip()

    if missing_env:
        logger.error("Missing required environment variables: %s", missing_env)
        sys.exit(1)

    logger.info("compute_event_windows.py starting")

    # Connect to database
    try:
        conn = get_connection(env)
    except mysql.connector.Error as exc:
        logger.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    try:
        summary = process_all_events(conn)
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    # --- Final summary ---
    print("\nEvent window computation complete:")
    print(f"  Events processed      : {summary['events_processed']}")
    print(f"  Window rows attempted : {summary['total_rows_attempted']}")
    print(f"  Window rows inserted  : {summary['total_rows_inserted']}")
    print(f"  (INSERT IGNORE: existing rows are silently skipped for idempotency)")

    logger.info(
        "Done. events_processed=%d total_rows_attempted=%d total_rows_inserted=%d",
        summary["events_processed"],
        summary["total_rows_attempted"],
        summary["total_rows_inserted"],
    )


if __name__ == "__main__":
    main()
