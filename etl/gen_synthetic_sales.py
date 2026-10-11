"""
gen_synthetic_sales.py — Generate synthetic FACT_SALES rows for FY23Q1–FY26Q4.

Overview
--------
Generates 2M–5M FACT_SALES rows at Daily_Aggregate grain for the period
2022-07-01 through 2026-06-30 (FY23Q1–FY26Q4).  All rows have is_synthetic=TRUE
(the column DEFAULT; not included in the CSV header).

Reconciliation
--------------
Three independent reconciliation groups:
  1. Gaming         — segment=MPC AND product_line='Gaming'
  2. MPC non-Gaming — segment=MPC AND product_line IS NOT 'Gaming'
  3. PBP+IC         — all remaining segments

SUM(gross_amount) per (fiscal_year, fiscal_quarter, segment_id/group) must
match QUARTERLY_FINANCIALS.revenue within 0.1% for every quarter.
The script aborts (exit 1) if any combination exceeds 0.1%.

Seasonality
-----------
  • Black Friday days   × 3.0
  • December (non-BF)   × 1.5

Event lifts
-----------
For each calendar month in the date range, if that month appears in
EVENT_WINDOW_MONTH with a non-NULL lift_ratio for a game_launch event,
apply lift_ratio as a multiplier to Gaming product sales amounts for that month.
Multiple events in the same month → multiplicative (product of all lift_ratios).

Device mix (by row count)
-----------
  Xbox 40%, Windows PC 35%, Mobile 15%, Cloud 5%, Mac 5%
  oem is non-null only for Windows PC rows (matched from pre-loaded DIM_DEVICE).

Cost amounts
------------
  PBP/IC/MPC non-Gaming products: cost_amount = unit_price × unit_cost_ratio
    (synthetic approximation — not reported data; see docs/MARGIN_DB.md)
  Gaming products: cost_amount = NULL (no Gaming cost data disclosed; do not fabricate)

RNG seeding
-----------
  SYNTHETIC_SEED env var (default 42) seeds the numpy RNG.

Output files
------------
  data/clean/sales_chunk_001.csv, sales_chunk_002.csv, …
    Columns: sale_date, product_id, segment_id, device_id, region_id,
             channel_id, qty, gross_amount, discount_amount, cost_amount
    (sale_id and is_synthetic are omitted; sale_id is AUTO_INCREMENT,
     is_synthetic uses the DEFAULT TRUE from the schema)

  data/clean/orders_sample.csv      — 100k synthetic ORDERS rows
  data/clean/order_items_sample.csv — matching ORDER_ITEM rows (1–5 lines each)

DB_ALLOCATED_MB update
----------------------
This script also exposes --update-config to compute and INSERT the
DB_ALLOCATED_MB CONFIG value (post-load size formula).  load_sales.sh should
call `python gen_synthetic_sales.py --update-config` after LOAD DATA LOCAL INFILE
to reflect the post-load database size.

  slope          = 0.01 × current_mb        (1% daily growth — labelled simulation)
  DB_ALLOCATED_MB = ROUND(current_mb × 1.25 / 0.9, 2)

Requirements: 4.6, 21.1
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import sys
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

import mysql.connector
import numpy as np
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNTHETIC_START = date(2022, 7, 1)   # FY23Q1 start
SYNTHETIC_END   = date(2026, 6, 30)  # FY26Q4 end

TARGET_MIN_ROWS = 2_000_000
TARGET_MAX_ROWS = 5_000_000
CHUNK_SIZE      = 500_000            # rows per CSV chunk

# Seasonality multipliers
SEASONALITY_BLACK_FRIDAY = 3.0
SEASONALITY_DECEMBER     = 1.5

# Device mix: platform → target fraction of rows
DEVICE_MIX = {
    "Xbox":       0.40,
    "Windows PC": 0.35,
    "Mobile":     0.15,
    "Cloud":      0.05,
    "Mac":        0.05,
}

# Reconciliation tolerance (0.1%)
RECON_TOLERANCE = 0.001

# CSV column order for FACT_SALES (no sale_id, no is_synthetic)
SALES_CSV_COLS = [
    "sale_date", "product_id", "segment_id", "device_id",
    "region_id", "channel_id", "qty", "gross_amount",
    "discount_amount", "cost_amount",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

def get_connection(env: dict) -> mysql.connector.MySQLConnection:
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
# Load dimension data from DB
# ---------------------------------------------------------------------------

def load_dimensions(conn: mysql.connector.MySQLConnection) -> dict:
    """Load all FK dimension IDs and metadata from the database.

    Returns a dict with keys:
      products       — list of dicts: product_id, segment_id, product_line, unit_cost_ratio
      devices        — list of dicts: device_id, platform, oem
      regions        — list of int region_id
      channels       — list of int channel_id
      segments       — dict: name → segment_id  (for COMPANY_SEGMENT rows)
    """
    cursor = conn.cursor()
    dims: dict = {}

    # Products
    cursor.execute(
        "SELECT product_id, segment_id, product_line, unit_cost_ratio FROM PRODUCT"
    )
    dims["products"] = [
        {
            "product_id":      row[0],
            "segment_id":      row[1],
            "product_line":    row[2],
            "unit_cost_ratio": float(row[3]) if row[3] is not None else None,
        }
        for row in cursor.fetchall()
    ]

    # Devices (all; we'll split by platform at generation time)
    cursor.execute("SELECT device_id, platform, oem FROM DIM_DEVICE")
    dims["devices"] = [
        {"device_id": row[0], "platform": row[1], "oem": row[2]}
        for row in cursor.fetchall()
    ]

    # Regions
    cursor.execute("SELECT region_id FROM DIM_REGION")
    dims["regions"] = [row[0] for row in cursor.fetchall()]

    # Channels
    cursor.execute("SELECT channel_id FROM DIM_CHANNEL")
    dims["channels"] = [row[0] for row in cursor.fetchall()]

    # Company segments: name → id
    cursor.execute("SELECT segment_id, name FROM COMPANY_SEGMENT")
    dims["segments"] = {row[1]: row[0] for row in cursor.fetchall()}

    cursor.close()
    logger.info(
        "Dimensions loaded: %d products, %d devices, %d regions, %d channels, %d segments",
        len(dims["products"]), len(dims["devices"]),
        len(dims["regions"]), len(dims["channels"]),
        len(dims["segments"]),
    )
    return dims


def load_quarterly_financials(conn: mysql.connector.MySQLConnection) -> dict:
    """Load QUARTERLY_FINANCIALS revenue for FY23Q1–FY26Q4.

    Returns a dict keyed by (fiscal_year, fiscal_quarter, segment_id) → revenue (float, millions USD).
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT fiscal_year, fiscal_quarter, segment_id, revenue
        FROM QUARTERLY_FINANCIALS
        WHERE fiscal_year BETWEEN 2023 AND 2026
        ORDER BY fiscal_year, fiscal_quarter, segment_id
        """
    )
    financials: dict = {}
    for fy, fq, seg_id, rev in cursor.fetchall():
        financials[(int(fy), int(fq), int(seg_id))] = float(rev)
    cursor.close()
    logger.info("Loaded %d QUARTERLY_FINANCIALS rows (FY23–FY26)", len(financials))
    return financials


def load_gaming_revenue_series(conn: mysql.connector.MySQLConnection) -> dict[tuple[int, int], float]:
    """Load GAMING_REVENUE_SERIES revenue for FY23Q1–FY26Q4.

    Returns a dict keyed by (fiscal_year, fiscal_quarter) → revenue_m (float, millions USD).
    Used by get_group_revenue to find the Gaming reconciliation target without any
    hardcoded split ratio.
    Aborts with sys.exit(1) if GAMING_REVENUE_SERIES has no rows in the synthetic
    range — gen_synthetic_sales cannot reconcile Gaming rows without real targets.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT fiscal_year, fiscal_quarter, revenue_m
        FROM GAMING_REVENUE_SERIES
        WHERE fiscal_year BETWEEN 2023 AND 2026
        ORDER BY fiscal_year, fiscal_quarter
        """
    )
    gaming_rev: dict[tuple[int, int], float] = {}
    for fy, fq, rev_m in cursor.fetchall():
        gaming_rev[(int(fy), int(fq))] = float(rev_m)
    cursor.close()
    if not gaming_rev:
        logger.error(
            "GAMING_REVENUE_SERIES has no rows for FY23–FY26. "
            "Run load_segments_gaming.py before gen_synthetic_sales.py. "
            "Cannot reconcile Gaming synthetic revenue without real targets."
        )
        sys.exit(1)
    logger.info("Loaded %d GAMING_REVENUE_SERIES rows (FY23–FY26)", len(gaming_rev))
    return gaming_rev


def load_dim_dates(conn: mysql.connector.MySQLConnection) -> dict:
    """Load DIM_DATE for the synthetic range.

    Returns a dict keyed by calendar_date (date) →
      {fiscal_year, fiscal_quarter, is_black_friday, is_holiday}
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT calendar_date, fiscal_year, fiscal_quarter,
               is_black_friday, is_holiday
        FROM DIM_DATE
        WHERE calendar_date BETWEEN %s AND %s
        ORDER BY calendar_date
        """,
        (SYNTHETIC_START.isoformat(), SYNTHETIC_END.isoformat()),
    )
    dim_dates: dict = {}
    for cal_date, fy, fq, is_bf, is_hol in cursor.fetchall():
        if isinstance(cal_date, str):
            cal_date = date.fromisoformat(cal_date)
        dim_dates[cal_date] = {
            "fiscal_year":     int(fy),
            "fiscal_quarter":  int(fq),
            "is_black_friday": bool(is_bf),
            "is_holiday":      bool(is_hol) if is_hol is not None else False,
        }
    cursor.close()
    logger.info("Loaded %d DIM_DATE rows for synthetic range", len(dim_dates))
    return dim_dates


def load_event_lifts(conn: mysql.connector.MySQLConnection) -> dict[tuple[int, int], float]:
    """Load month-level lift ratios from EVENT_WINDOW_MONTH for game_launch events.

    Returns a dict keyed by (year, month) → multiplicative lift ratio.
    Multiple events in the same month are combined multiplicatively.
    Only non-NULL lift_ratio rows for game_launch events are included.
    """
    cursor = conn.cursor()
    # Join EVENT_WINDOW_MONTH with BUSINESS_EVENT to get start_date and event_type
    cursor.execute(
        """
        SELECT be.start_date, ewm.month_offset, ewm.lift_ratio
        FROM EVENT_WINDOW_MONTH ewm
        JOIN BUSINESS_EVENT be ON be.event_id = ewm.event_id
        WHERE be.event_type = 'game_launch'
          AND ewm.lift_ratio IS NOT NULL
        """
    )
    lifts: dict[tuple[int, int], float] = {}
    for start_date, month_offset, lift_ratio in cursor.fetchall():
        if isinstance(start_date, str):
            start_date = date.fromisoformat(start_date)
        # Compute the actual calendar month for this offset
        # offset 0 = month of start_date; use integer arithmetic
        total_months = start_date.year * 12 + (start_date.month - 1) + int(month_offset)
        ym = (total_months // 12, total_months % 12 + 1)
        lr = float(lift_ratio)
        if ym in lifts:
            lifts[ym] *= lr   # multiplicative for overlapping events
        else:
            lifts[ym] = lr
    cursor.close()
    logger.info("Loaded %d monthly event lift entries", len(lifts))
    return lifts


# ---------------------------------------------------------------------------
# Reconciliation groups
# ---------------------------------------------------------------------------

def classify_product(product: dict, segments: dict[str, int]) -> str:
    """Return reconciliation group for a product.

    Groups:
      'gaming'    — segment=MPC, product_line='Gaming'
      'mpc'       — segment=MPC, product_line != 'Gaming' (or NULL)
      'pbp_ic'    — all other segments (PBP, IC)
    """
    mpc_id = segments.get("More Personal Computing")
    if product["segment_id"] == mpc_id and product["product_line"] == "Gaming":
        return "gaming"
    elif product["segment_id"] == mpc_id:
        return "mpc"
    else:
        return "pbp_ic"


# ---------------------------------------------------------------------------
# Weight computation for reconciliation-driven generation
# ---------------------------------------------------------------------------

def compute_date_weight(day: date, dim_dates: dict, event_lifts: dict, product_group: str) -> float:
    """Compute a base weight for distributing revenue across dates.

    Applies:
      - Seasonality: Black Friday × 3.0, December × 1.5
      - Event lifts: only for Gaming group
    """
    meta = dim_dates.get(day)
    if meta is None:
        return 1.0

    weight = 1.0

    # Seasonality
    if meta["is_black_friday"]:
        weight *= SEASONALITY_BLACK_FRIDAY
    elif day.month == 12:
        weight *= SEASONALITY_DECEMBER

    # Event lift (Gaming only)
    if product_group == "gaming":
        ym = (day.year, day.month)
        if ym in event_lifts:
            weight *= event_lifts[ym]

    return weight


# ---------------------------------------------------------------------------
# Device assignment helpers
# ---------------------------------------------------------------------------

def build_device_pools(devices: list[dict]) -> dict[str, list[dict]]:
    """Group devices by platform for weighted device assignment."""
    pools: dict[str, list[dict]] = {}
    for d in devices:
        p = d["platform"] or "Unknown"
        pools.setdefault(p, []).append(d)
    return pools


def assign_devices(
    n_rows: int,
    device_pools: dict[str, list[dict]],
    rng: np.random.Generator,
) -> np.ndarray:
    """Return an array of device_id values (shape: n_rows) following DEVICE_MIX.

    oem is implicitly non-null for Windows PC and null for others because we
    select the actual device_id from the platform-specific pool; the DIM_DEVICE
    rows themselves carry the correct oem value.
    """
    device_ids = np.empty(n_rows, dtype=np.int32)
    idx = 0
    for platform, fraction in DEVICE_MIX.items():
        pool = device_pools.get(platform, [])
        if not pool:
            logger.warning("No devices found for platform '%s'; skipping that slice", platform)
            continue
        count = round(fraction * n_rows)
        if idx + count > n_rows:
            count = n_rows - idx
        pool_ids = np.array([d["device_id"] for d in pool], dtype=np.int32)
        chosen = rng.choice(pool_ids, size=count, replace=True)
        device_ids[idx : idx + count] = chosen
        idx += count
    # Fill any remainder (due to rounding) with the first available device
    if idx < n_rows:
        first_pool = next(iter(device_pools.values()))
        fallback_id = first_pool[0]["device_id"] if first_pool else 1
        device_ids[idx:] = fallback_id
    # Shuffle so platform blocks are interleaved
    rng.shuffle(device_ids)
    return device_ids


# ---------------------------------------------------------------------------
# Core generation engine
# ---------------------------------------------------------------------------

def _d2(v: float) -> str:
    """Format a float to 2dp string for CSV; empty string for None/NaN."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return f"{v:.2f}"


def _iter_dates(dim_dates: dict) -> list[date]:
    """Return sorted list of dates in the synthetic range present in DIM_DATE."""
    return sorted(dim_dates.keys())


def generate_sales(
    dims: dict,
    financials: dict,
    dim_dates: dict,
    event_lifts: dict,
    gaming_rev: dict[tuple[int, int], float],
    rng: np.random.Generator,
    chunk_size: int = CHUNK_SIZE,
) -> tuple[list[Path], dict]:
    """Main generation function.

    Returns (chunk_paths, reconciliation_data).

    reconciliation_data structure:
      {group: {(fy, fq): total_gross_amount}}
    where group ∈ {'gaming', 'mpc', 'pbp_ic'}
    """
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    segments = dims["segments"]
    products = dims["products"]
    regions  = dims["regions"]
    channels = dims["channels"]
    devices  = dims["devices"]

    if not products:
        logger.error("No products found in PRODUCT table. Cannot generate sales.")
        sys.exit(1)
    if not regions:
        logger.error("No regions found in DIM_REGION table.")
        sys.exit(1)
    if not channels:
        logger.error("No channels found in DIM_CHANNEL table.")
        sys.exit(1)
    if not dim_dates:
        logger.error("No DIM_DATE rows found for synthetic range.")
        sys.exit(1)

    mpc_id = segments.get("More Personal Computing")
    pbp_id = segments.get("Productivity and Business Processes")
    ic_id  = segments.get("Intelligent Cloud")

    # ------------------------------------------------------------------
    # Separate products by reconciliation group
    # ------------------------------------------------------------------
    gaming_products  = [p for p in products if p["segment_id"] == mpc_id and p["product_line"] == "Gaming"]
    mpc_products     = [p for p in products if p["segment_id"] == mpc_id and p["product_line"] != "Gaming"]
    pbp_ic_products  = [p for p in products if p["segment_id"] in (pbp_id, ic_id)]

    for label, lst in [("Gaming", gaming_products), ("MPC non-Gaming", mpc_products), ("PBP+IC", pbp_ic_products)]:
        if not lst:
            logger.warning("No products found for group '%s'. Revenue target cannot be met.", label)

    # ------------------------------------------------------------------
    # Aggregate QUARTERLY_FINANCIALS revenue by group
    # ------------------------------------------------------------------
    # Determine all (fy, fq) periods in the synthetic range
    all_periods: set[tuple[int, int]] = set()
    for meta in dim_dates.values():
        all_periods.add((meta["fiscal_year"], meta["fiscal_quarter"]))
    all_periods = sorted(all_periods)

    def get_group_revenue(group: str, fy: int, fq: int) -> float:
        """Return target revenue (millions USD) for a reconciliation group and quarter.

        Gaming: sourced directly from GAMING_REVENUE_SERIES (real reported data).
          Aborts loudly if a quarter has no Gaming row — no guessing allowed.
        MPC non-Gaming: MPC reported total (QUARTERLY_FINANCIALS) minus Gaming
          (GAMING_REVENUE_SERIES). Aborts if either component is missing.
        PBP+IC: sum of PBP and IC rows from QUARTERLY_FINANCIALS.
        """
        if group == "gaming":
            v = gaming_rev.get((fy, fq))
            if v is None:
                logger.error(
                    "GAMING_REVENUE_SERIES has no row for FY%dQ%d. "
                    "Cannot reconcile Gaming synthetic revenue. "
                    "Ensure load_segments_gaming.py has been run.",
                    fy, fq,
                )
                sys.exit(1)
            return v

        elif group == "mpc":
            mpc_total = financials.get((fy, fq, mpc_id))
            gaming_target = gaming_rev.get((fy, fq))
            if mpc_total is None:
                logger.error(
                    "QUARTERLY_FINANCIALS has no MPC row for FY%dQ%d. "
                    "Run load_financials.py first.",
                    fy, fq,
                )
                sys.exit(1)
            if gaming_target is None:
                logger.error(
                    "GAMING_REVENUE_SERIES has no row for FY%dQ%d. "
                    "Cannot derive MPC non-Gaming revenue. "
                    "Ensure load_segments_gaming.py has been run.",
                    fy, fq,
                )
                sys.exit(1)
            derived = mpc_total - gaming_target
            if derived < 0:
                logger.error(
                    "FY%dQ%d: Gaming revenue (%.2f M) exceeds MPC total (%.2f M). "
                    "Check source data for inconsistency.",
                    fy, fq, gaming_target, mpc_total,
                )
                sys.exit(1)
            return derived

        else:  # pbp_ic
            total = 0.0
            for seg_id in (pbp_id, ic_id):
                if seg_id is None:
                    continue
                v = financials.get((fy, fq, seg_id))
                if v is not None:
                    total += v
            return total

    # ------------------------------------------------------------------
    # Build per-group date weight maps for revenue distribution
    # ------------------------------------------------------------------
    def build_weights_for_group(group: str) -> dict[date, float]:
        weights: dict[date, float] = {}
        for d, meta in dim_dates.items():
            w = compute_date_weight(d, dim_dates, event_lifts, group)
            key = (meta["fiscal_year"], meta["fiscal_quarter"])
            weights[d] = w
        return weights

    weights_gaming = build_weights_for_group("gaming")
    weights_mpc    = build_weights_for_group("mpc")
    weights_pbpic  = build_weights_for_group("pbp_ic")

    def quarter_weight_sum(weights: dict[date, float], fy: int, fq: int) -> float:
        total = 0.0
        for d, meta in dim_dates.items():
            if meta["fiscal_year"] == fy and meta["fiscal_quarter"] == fq:
                total += weights[d]
        return max(total, 1e-9)

    # ------------------------------------------------------------------
    # Decide target row counts per group
    # ------------------------------------------------------------------
    # Distribute rows roughly proportionally to revenue and 60/30/10 weight
    n_gaming_total  = int(TARGET_MIN_ROWS * 0.50)   # 1M Gaming rows (50%)
    n_mpc_total     = int(TARGET_MIN_ROWS * 0.20)   # 0.4M MPC non-gaming (20%)
    n_pbpic_total   = int(TARGET_MIN_ROWS * 0.30)   # 0.6M PBP+IC (30%)
    n_total         = n_gaming_total + n_mpc_total + n_pbpic_total
    logger.info(
        "Target rows: Gaming=%d MPC=%d PBP+IC=%d total=%d",
        n_gaming_total, n_mpc_total, n_pbpic_total, n_total,
    )

    # ------------------------------------------------------------------
    # Device pools
    # ------------------------------------------------------------------
    device_pools = build_device_pools(devices)

    # ------------------------------------------------------------------
    # Accumulators for reconciliation
    # ------------------------------------------------------------------
    recon: dict[str, dict[tuple[int, int], float]] = {
        "gaming": {p: 0.0 for p in all_periods},
        "mpc":    {p: 0.0 for p in all_periods},
        "pbp_ic": {p: 0.0 for p in all_periods},
    }

    # ------------------------------------------------------------------
    # Generation helpers
    # ------------------------------------------------------------------
    region_arr  = np.array(regions, dtype=np.int32)
    channel_arr = np.array(channels, dtype=np.int32)

    def _pick_arr(arr: np.ndarray, n: int) -> np.ndarray:
        return rng.choice(arr, size=n, replace=True)

    sorted_dates = _iter_dates(dim_dates)

    def gen_group_rows(
        group: str,
        group_products: list[dict],
        n_rows: int,
        weight_map: dict[date, float],
    ) -> list[list]:
        """Generate n_rows for a given product group.

        Revenue per row is driven by reconciliation target:
          per-day-target = quarterly_revenue_target × (day_weight / quarter_total_weight) / n_rows_for_day
        We pre-allocate rows to days proportionally, then assign revenue.
        """
        if not group_products:
            return []

        prod_ids   = np.array([p["product_id"]   for p in group_products], dtype=np.int32)
        seg_ids    = np.array([p["segment_id"]    for p in group_products], dtype=np.int32)
        ucr_vals   = np.array(
            [p["unit_cost_ratio"] if p["unit_cost_ratio"] is not None else float("nan")
             for p in group_products], dtype=np.float64
        )
        is_gaming_arr = np.array(
            [p["product_line"] == "Gaming" for p in group_products], dtype=bool
        )

        # Pre-assign row counts per (fy, fq) proportionally to n_rows
        period_rows: dict[tuple[int, int], int] = {}
        for fy, fq in all_periods:
            target_rev = get_group_revenue(group, fy, fq)
            if target_rev > 0:
                period_rows[(fy, fq)] = max(1, round(n_rows * (target_rev / sum(
                    get_group_revenue(group, fy2, fq2) for fy2, fq2 in all_periods
                    if get_group_revenue(group, fy2, fq2) > 0
                ))))
            else:
                period_rows[(fy, fq)] = 0

        # Ensure total rows matches n_rows (adjust last period)
        total_assigned = sum(period_rows.values())
        delta = n_rows - total_assigned
        if delta != 0:
            for k in reversed(all_periods):
                if period_rows[k] > 0:
                    period_rows[k] = max(0, period_rows[k] + delta)
                    break

        result_rows: list[list] = []

        for fy, fq in all_periods:
            n_period = period_rows.get((fy, fq), 0)
            if n_period == 0:
                continue

            target_rev = get_group_revenue(group, fy, fq)
            if target_rev <= 0:
                continue

            # Dates in this quarter
            quarter_dates = [d for d in sorted_dates
                             if dim_dates[d]["fiscal_year"] == fy
                             and dim_dates[d]["fiscal_quarter"] == fq]
            if not quarter_dates:
                continue

            qd_arr     = np.array(quarter_dates)
            qw_arr     = np.array([weight_map[d] for d in quarter_dates], dtype=np.float64)
            qw_sum     = qw_arr.sum()
            qw_norm    = qw_arr / qw_sum   # normalized weights

            # Assign rows to dates
            date_row_counts = rng.multinomial(n_period, qw_norm)

            # Revenue per row ∝ date weight; total must sum to target_rev
            # We generate a gross_amount for each row proportional to the date weight,
            # then scale the whole quarter to hit target_rev exactly.

            local_rows: list[list] = []
            local_gross_total = 0.0

            for di, (d, n_day) in enumerate(zip(quarter_dates, date_row_counts)):
                if n_day == 0:
                    continue
                day_weight = qw_arr[di]
                # Base unit_price drawn from lognormal; mean ≈ 50, wide spread
                # The actual scale will be adjusted after the quarter is generated.
                unit_prices = rng.lognormal(mean=3.5, sigma=0.8, size=n_day)
                unit_prices = np.clip(unit_prices, 1.0, 10_000.0)

                # Random qty (1–3 typically)
                qtys = rng.integers(1, 4, size=n_day)

                # Discount fraction (0–25%)
                discount_fracs = rng.uniform(0.0, 0.25, size=n_day)

                # Gross before scaling
                gross_vals = unit_prices * qtys

                # Discount amount
                discount_vals = gross_vals * discount_fracs

                # Product choice
                prod_idx = rng.integers(0, len(group_products), size=n_day)
                chosen_prod_ids = prod_ids[prod_idx]
                chosen_seg_ids  = seg_ids[prod_idx]
                chosen_ucr      = ucr_vals[prod_idx]
                chosen_is_gaming = is_gaming_arr[prod_idx]

                # Devices
                dev_ids = assign_devices(n_day, device_pools, rng)

                # Regions & channels
                reg_ids = _pick_arr(region_arr, n_day)
                chan_ids = _pick_arr(channel_arr, n_day)

                date_str = d.isoformat()

                for i in range(n_day):
                    ga = gross_vals[i]
                    da = discount_vals[i]
                    # cost_amount: NULL for Gaming; unit_price × ucr otherwise
                    if chosen_is_gaming[i]:
                        ca = float("nan")  # written as "" → NULL
                    else:
                        ucr = chosen_ucr[i]
                        if not math.isnan(ucr):
                            ca = unit_prices[i] * ucr
                        else:
                            ca = float("nan")

                    local_rows.append([
                        date_str,
                        int(chosen_prod_ids[i]),
                        int(chosen_seg_ids[i]),
                        int(dev_ids[i]),
                        int(reg_ids[i]),
                        int(chan_ids[i]),
                        int(qtys[i]),
                        ga,         # unscaled; will be scaled below
                        da,         # proportional; will be scaled below
                        ca,         # cost (unscaled), NaN=NULL
                    ])
                    local_gross_total += ga

            # --- Scale gross_amount and discount_amount so total = target_rev ---
            if local_gross_total > 0 and local_rows:
                scale = target_rev / local_gross_total
                for row in local_rows:
                    row[7] = round(row[7] * scale, 2)   # gross_amount
                    row[8] = round(row[8] * scale, 2)   # discount_amount
                    # cost_amount: scale proportionally for non-gaming
                    if not math.isnan(row[9]):
                        row[9] = round(row[9] * scale, 2)

            # Track recon
            period_gross = sum(r[7] for r in local_rows)
            recon[group][(fy, fq)] = recon[group].get((fy, fq), 0.0) + period_gross

            result_rows.extend(local_rows)

        return result_rows

    # ------------------------------------------------------------------
    # Generate all three groups
    # ------------------------------------------------------------------
    logger.info("Generating Gaming rows…")
    gaming_rows = gen_group_rows("gaming", gaming_products, n_gaming_total, weights_gaming)
    logger.info("Generated %d Gaming rows", len(gaming_rows))

    logger.info("Generating MPC non-Gaming rows…")
    mpc_rows = gen_group_rows("mpc", mpc_products, n_mpc_total, weights_mpc)
    logger.info("Generated %d MPC non-Gaming rows", len(mpc_rows))

    logger.info("Generating PBP+IC rows…")
    pbpic_rows = gen_group_rows("pbp_ic", pbp_ic_products, n_pbpic_total, weights_pbpic)
    logger.info("Generated %d PBP+IC rows", len(pbpic_rows))

    all_rows = gaming_rows + mpc_rows + pbpic_rows
    logger.info("Total rows generated: %d", len(all_rows))

    # Shuffle
    indices = rng.permutation(len(all_rows))
    all_rows = [all_rows[i] for i in indices]

    # ------------------------------------------------------------------
    # Write CSV chunks
    # ------------------------------------------------------------------
    chunk_paths: list[Path] = []
    n_total_generated = len(all_rows)
    n_chunks = math.ceil(n_total_generated / chunk_size)

    for chunk_idx in range(n_chunks):
        start = chunk_idx * chunk_size
        end   = min(start + chunk_size, n_total_generated)
        chunk = all_rows[start:end]

        chunk_path = CLEAN_DIR / f"sales_chunk_{chunk_idx + 1:03d}.csv"
        with open(chunk_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(SALES_CSV_COLS)
            for row in chunk:
                writer.writerow([
                    row[0],          # sale_date
                    row[1],          # product_id
                    row[2],          # segment_id
                    row[3],          # device_id
                    row[4],          # region_id
                    row[5],          # channel_id
                    row[6],          # qty
                    _d2(row[7]),     # gross_amount
                    _d2(row[8]),     # discount_amount
                    _d2(row[9]) if not math.isnan(row[9]) else "",  # cost_amount (NULL if gaming)
                ])
        chunk_paths.append(chunk_path)
        logger.info("Wrote %s (%d rows)", chunk_path.name, len(chunk))

    return chunk_paths, recon


# ---------------------------------------------------------------------------
# Reconciliation check
# ---------------------------------------------------------------------------

def run_reconciliation(
    recon: dict,
    financials: dict,
    dims: dict,
    all_periods: list[tuple[int, int]],
    gaming_rev: dict[tuple[int, int], float],
) -> bool:
    """Compare generated totals against QUARTERLY_FINANCIALS targets.

    Prints a full reconciliation report.
    Returns True if all groups pass within RECON_TOLERANCE (0.1%).
    Returns False if any group/quarter combination exceeds tolerance.
    """
    segments = dims["segments"]
    mpc_id = segments.get("More Personal Computing")
    pbp_id = segments.get("Productivity and Business Processes")
    ic_id  = segments.get("Intelligent Cloud")

    def get_target(group: str, fy: int, fq: int) -> Optional[float]:
        """Mirror of get_group_revenue for reconciliation reporting.

        Returns None (SKIP) rather than aborting — reconciliation is a report,
        not a gate at this point. Missing rows are flagged as SKIP in the table.
        """
        if group == "gaming":
            return gaming_rev.get((fy, fq))  # None → SKIP row in report

        elif group == "mpc":
            mpc_total = financials.get((fy, fq, mpc_id))
            gaming_target = gaming_rev.get((fy, fq))
            if mpc_total is None or gaming_target is None:
                return None  # SKIP
            derived = mpc_total - gaming_target
            return derived if derived >= 0 else None

        else:  # pbp_ic
            total = 0.0
            found = False
            for seg_id in (pbp_id, ic_id):
                if seg_id is None:
                    continue
                v = financials.get((fy, fq, seg_id))
                if v is not None:
                    total += v
                    found = True
            return total if found else None

    group_labels = {
        "gaming":  "Gaming (MPC segment, product_line='Gaming')",
        "mpc":     "MPC non-Gaming",
        "pbp_ic":  "Productivity+Cloud (PBP+IC)",
    }

    print("\n" + "=" * 72)
    print("RECONCILIATION REPORT")
    print("=" * 72)
    print(f"{'Group':<12} {'FY':>4} {'Q':>2} {'Target (USD)':>18} {'Generated':>18} {'Δ%':>8} {'Status':>6}")
    print("-" * 72)

    all_passed = True
    failed_combos: list[str] = []

    for group in ("gaming", "mpc", "pbp_ic"):
        for fy, fq in all_periods:
            generated = recon[group].get((fy, fq), 0.0)
            target = get_target(group, fy, fq)

            if target is None or target == 0.0:
                status = "SKIP"
                delta_pct = float("nan")
            else:
                delta_pct = abs(generated - target) / target
                if delta_pct <= RECON_TOLERANCE:
                    status = "PASS"
                else:
                    status = "FAIL"
                    all_passed = False
                    failed_combos.append(
                        f"{group} FY{fy}Q{fq}: target={target:,.2f} "
                        f"generated={generated:,.2f} delta={delta_pct*100:.4f}%"
                    )

            if math.isnan(delta_pct):
                pct_str = "  N/A"
            else:
                pct_str = f"{delta_pct*100:7.4f}%"

            label = group_labels[group][:11]
            print(
                f"{label:<12} {fy:>4} {fq:>2} "
                f"{target or 0:>18,.2f} {generated:>18,.2f} "
                f"{pct_str:>8} {status:>6}"
            )

    print("=" * 72)
    if all_passed:
        print("RECONCILIATION PASSED — all groups within 0.1% tolerance.\n")
    else:
        print(f"RECONCILIATION FAILED — {len(failed_combos)} combination(s) exceeded 0.1%:")
        for fc in failed_combos:
            print(f"  {fc}")
        print()

    return all_passed


# ---------------------------------------------------------------------------
# Orders/Order_items sample
# ---------------------------------------------------------------------------

def write_orders_sample(
    all_rows: list[list],
    dims: dict,
    rng: np.random.Generator,
    n_orders: int = 100_000,
) -> tuple[Path, Path]:
    """Write 100k ORDERS rows and matching ORDER_ITEM rows.

    ORDERS columns: order_id, order_date, device_id, region_id, channel_id, total_amount
    ORDER_ITEM columns: order_id, line_no, product_id, segment_id, qty, unit_price, discount_amount
    """
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    orders_path = CLEAN_DIR / "orders_sample.csv"
    items_path  = CLEAN_DIR / "order_items_sample.csv"

    # Sample rows from all_rows (wrap around if fewer than n_orders)
    n_available = len(all_rows)
    if n_available == 0:
        logger.warning("No rows available for orders sample; writing empty files.")
        orders_path.write_text("order_id,order_date,device_id,region_id,channel_id,total_amount\n")
        items_path.write_text("order_id,line_no,product_id,segment_id,qty,unit_price,discount_amount\n")
        return orders_path, items_path

    sample_indices = rng.integers(0, n_available, size=n_orders)

    with open(orders_path, "w", newline="", encoding="utf-8") as of, \
         open(items_path,  "w", newline="", encoding="utf-8") as if_:

        o_writer = csv.writer(of)
        i_writer = csv.writer(if_)

        o_writer.writerow(["order_id", "order_date", "device_id", "region_id", "channel_id", "total_amount"])
        i_writer.writerow(["order_id", "line_no", "product_id", "segment_id", "qty", "unit_price", "discount_amount"])

        for order_id, row_idx in enumerate(sample_indices, start=1):
            src = all_rows[row_idx]
            # src layout: [sale_date, product_id, segment_id, device_id, region_id, channel_id,
            #              qty, gross_amount, discount_amount, cost_amount]
            order_date   = src[0]
            device_id    = src[3]
            region_id    = src[4]
            channel_id   = src[5]
            gross_amount = src[7]

            # 1–3 line items per order
            n_lines = rng.integers(1, 4)
            line_total = 0.0

            line_rows = []
            for line_no in range(1, n_lines + 1):
                li_row_idx = rng.integers(0, n_available)
                li_src = all_rows[li_row_idx]
                product_id   = li_src[1]
                segment_id   = li_src[2]
                qty          = max(1, int(li_src[6]))
                # unit_price approximated as gross/qty (no exact inverse available here)
                unit_price   = round(float(li_src[7]) / qty, 2) if qty > 0 else 0.01
                discount_amt = round(float(li_src[8]) / n_lines, 2)
                line_total  += unit_price * qty - discount_amt
                line_rows.append([order_id, line_no, product_id, segment_id, qty, _d2(unit_price), _d2(discount_amt)])

            # Write order
            o_writer.writerow([order_id, order_date, device_id, region_id, channel_id, _d2(gross_amount)])
            # Write line items
            i_writer.writerows(line_rows)

    logger.info("Wrote orders_sample.csv (%d rows) and order_items_sample.csv", n_orders)
    return orders_path, items_path


# ---------------------------------------------------------------------------
# DB_ALLOCATED_MB computation
# ---------------------------------------------------------------------------

def update_db_allocated_mb(conn: mysql.connector.MySQLConnection) -> float:
    """Read current DB size from information_schema and update CONFIG.

    Formula (post-load, labelled simulation):
      slope           = 0.01 × current_mb   (1% daily growth rate)
      DB_ALLOCATED_MB = ROUND(current_mb × 1.25 / 0.9, 2)

    Returns the computed DB_ALLOCATED_MB value.

    NOTE: This function is meant to be called AFTER LOAD DATA LOCAL INFILE
    has completed.  gen_synthetic_sales.py exposes a --update-config CLI
    subcommand for load_sales.sh to invoke after the bulk load.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT ROUND(SUM(data_length + index_length) / 1048576, 2)
        FROM information_schema.TABLES
        WHERE table_schema = DATABASE()
        """
    )
    row = cursor.fetchone()
    current_mb = float(row[0]) if row and row[0] is not None else 0.0
    logger.info("Current DB size (data+index): %.2f MB", current_mb)

    # Formula: slope = 0.01 × current_mb (1% daily growth — labelled simulation)
    # DB_ALLOCATED_MB = ROUND(current_mb × 1.25 / 0.9, 2)
    db_allocated_mb = round(current_mb * 1.25 / 0.9, 2)
    logger.info(
        "DB_ALLOCATED_MB formula: ROUND(%.2f × 1.25 / 0.9, 2) = %.2f",
        current_mb, db_allocated_mb,
    )

    cursor.execute(
        """
        INSERT INTO CONFIG (config_key, config_value)
        VALUES ('DB_ALLOCATED_MB', %s)
        ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)
        """,
        (str(db_allocated_mb),),
    )
    conn.commit()
    logger.info("CONFIG['DB_ALLOCATED_MB'] set to %.2f", db_allocated_mb)
    cursor.close()
    return db_allocated_mb


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic FACT_SALES CSVs for Margin-DB (FY23Q1–FY26Q4).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--update-config",
        action="store_true",
        help=(
            "Skip CSV generation. Instead, read current DB size from "
            "information_schema and update CONFIG['DB_ALLOCATED_MB']. "
            "Call this after LOAD DATA LOCAL INFILE has completed."
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    args = parse_args()

    # --- Environment variables ---
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

    # RNG seed
    seed_str = os.environ.get("SYNTHETIC_SEED", "42").strip()
    try:
        seed = int(seed_str)
    except ValueError:
        logger.warning("SYNTHETIC_SEED='%s' is not an integer; defaulting to 42.", seed_str)
        seed = 42
    logger.info("RNG seed: %d (from SYNTHETIC_SEED env var)", seed)
    rng = np.random.default_rng(seed)

    # --- Connect ---
    try:
        conn = get_connection(env)
    except mysql.connector.Error as exc:
        logger.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    try:
        # --- --update-config mode ---
        if args.update_config:
            logger.info("--update-config mode: computing post-load DB_ALLOCATED_MB")
            db_alloc = update_db_allocated_mb(conn)
            print(f"DB_ALLOCATED_MB updated to {db_alloc:.2f} MB")
            conn.close()
            return

        # --- Full generation mode ---
        logger.info("gen_synthetic_sales.py starting (FY23Q1–FY26Q4)")

        dims       = load_dimensions(conn)
        financials = load_quarterly_financials(conn)
        gaming_rev = load_gaming_revenue_series(conn)
        dim_dates  = load_dim_dates(conn)
        event_lifts = load_event_lifts(conn)

        if not financials:
            logger.error(
                "QUARTERLY_FINANCIALS has no rows for FY23–FY26. "
                "Run load_financials.py first."
            )
            conn.close()
            sys.exit(1)

        all_periods = sorted({
            (meta["fiscal_year"], meta["fiscal_quarter"])
            for meta in dim_dates.values()
        })

        # --- Generate ---
        chunk_paths, recon = generate_sales(
            dims=dims,
            financials=financials,
            dim_dates=dim_dates,
            event_lifts=event_lifts,
            gaming_rev=gaming_rev,
            rng=rng,
            chunk_size=CHUNK_SIZE,
        )

        # --- Reconciliation check ---
        passed = run_reconciliation(recon, financials, dims, all_periods, gaming_rev)
        if not passed:
            logger.error(
                "Reconciliation FAILED. Aborting — CSV files have been written "
                "but should NOT be loaded. Investigate revenue targets and retry."
            )
            conn.close()
            sys.exit(1)

        # --- Orders sample ---
        # Re-read all rows from chunks for the sample (streaming from files)
        logger.info("Reading all generated rows for orders sample…")
        all_rows_flat: list[list] = []
        for cp in chunk_paths:
            with open(cp, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader)  # skip header
                for row in reader:
                    # Parse numerics for orders sample
                    all_rows_flat.append([
                        row[0],                   # sale_date str
                        int(row[1]),              # product_id
                        int(row[2]),              # segment_id
                        int(row[3]),              # device_id
                        int(row[4]),              # region_id
                        int(row[5]),              # channel_id
                        int(row[6]),              # qty
                        float(row[7]) if row[7] else 0.0,  # gross_amount
                        float(row[8]) if row[8] else 0.0,  # discount_amount
                        float(row[9]) if row[9] else float("nan"),  # cost_amount
                    ])

        write_orders_sample(all_rows_flat, dims, rng)

        # --- DB_ALLOCATED_MB (pre-load snapshot) ---
        # NOTE: This updates CONFIG based on the current (pre-LOAD DATA) DB size.
        # load_sales.sh should re-invoke:
        #   python etl/gen_synthetic_sales.py --update-config
        # after LOAD DATA LOCAL INFILE completes to capture the post-load size.
        logger.info(
            "Computing pre-load DB_ALLOCATED_MB snapshot "
            "(re-run with --update-config after LOAD DATA for post-load value)…"
        )
        db_alloc = update_db_allocated_mb(conn)

        # --- Summary ---
        print("\n" + "=" * 72)
        print("GENERATION COMPLETE")
        print("=" * 72)
        total_rows = sum(1 for cp in chunk_paths for _ in open(cp)) - len(chunk_paths)
        print(f"  CSV chunks written   : {len(chunk_paths)}")
        print(f"  Total rows generated : {total_rows:,}")
        print(f"  DB_ALLOCATED_MB set  : {db_alloc:.2f} (pre-load; re-run --update-config after LOAD DATA)")
        print(f"  Chunk files          : {', '.join(p.name for p in chunk_paths)}")
        print(f"  Orders sample        : data/clean/orders_sample.csv")
        print(f"  Order items sample   : data/clean/order_items_sample.csv")
        print()

        logger.info(
            "Done. %d chunk(s) written. Reconciliation PASSED. "
            "Run 'python etl/gen_synthetic_sales.py --update-config' "
            "after LOAD DATA LOCAL INFILE to update post-load DB_ALLOCATED_MB.",
            len(chunk_paths),
        )

    except Exception as exc:
        logger.error("Unexpected error: %s", exc, exc_info=True)
        conn.close()
        sys.exit(1)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
