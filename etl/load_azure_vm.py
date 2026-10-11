"""
load_azure_vm.py — Load Azure Public Dataset V2 VM trace into AZURE_VM table.

Batch loading by trace_day ranges:
  Batch 1: trace_day 0..4
  Batch 2: trace_day 5..9
  Batch 3: trace_day 10..14
  Batch 4: trace_day 15..19
  Batch 5: trace_day 20..24
  Batch 6: trace_day 25..29

After each batch:
  - Takes a DB size measurement from information_schema.TABLES
  - Updates DB_SIZE_SNAPSHOT (is_synthetic=FALSE, label='load-progress snapshot')
  - Logs progress and verified row count

Final verification:
  - COUNT(*) on AZURE_VM must equal exactly 2,695,548.

Requirements: 4.11, 21.1, 21.2
"""

import csv
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = PROJECT_ROOT / "data" / "raw" / "azure_v2" / "vmtable.csv"
if not CSV_PATH.exists():
    CSV_PATH = PROJECT_ROOT / "data" / "raw" / "azure" / "vmtable.csv"

LOG_DIR = PROJECT_ROOT / "data" / "clean"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "load_azure_vm.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
logger = logging.getLogger(__name__)

EXPECTED_TOTAL_ROWS = 2695548
BATCH_RANGES = [
    (0, 4),
    (5, 9),
    (10, 14),
    (15, 19),
    (20, 24),
    (25, 29),
]


def get_db_connection():
    load_dotenv(override=True)
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("ADMIN_USER", "admin_user"),
        password=os.getenv("ADMIN_PASSWORD", ""),
        database=os.getenv("DB_NAME", "margin_db"),
        allow_local_infile=True,
    )


def record_size_snapshot(conn, batch_label: str):
    cur = conn.cursor()
    cur.execute("SET SESSION information_schema_stats_expiry = 0")
    cur.execute("""
        SELECT
            COALESCE(SUM(data_length) / 1048576, 0),
            COALESCE(SUM(index_length) / 1048576, 0)
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
    """)
    row = cur.fetchone()
    data_mb = float(row[0]) if row else 0.0
    index_mb = float(row[1]) if row else 0.0

    # Insert or update snapshot for today
    cur.execute("""
        INSERT INTO DB_SIZE_SNAPSHOT (snapped_at, total_data_mb, total_index_mb, is_synthetic)
        VALUES (CURDATE(), %s, %s, FALSE)
        ON DUPLICATE KEY UPDATE
            total_data_mb = VALUES(total_data_mb),
            total_index_mb = VALUES(total_index_mb),
            is_synthetic = FALSE
    """, (data_mb, index_mb))
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM AZURE_VM")
    current_vm_rows = cur.fetchone()[0]
    cur.close()

    logger.info(
        "Snapshot [%s]: total_data_mb=%.2f, total_index_mb=%.2f, AZURE_VM rows=%d",
        batch_label, data_mb, index_mb, current_vm_rows
    )


def load_batches():
    logger.info("Starting Azure VM workload load from %s", CSV_PATH)
    start_time = time.time()

    conn = get_db_connection()
    cur = conn.cursor()

    # Check initial rows
    cur.execute("SELECT COUNT(*) FROM AZURE_VM")
    initial_rows = cur.fetchone()[0]
    if initial_rows == EXPECTED_TOTAL_ROWS:
        logger.info("AZURE_VM is already fully loaded (%d rows). Verification complete.", initial_rows)
        record_size_snapshot(conn, "load-progress snapshot (already loaded)")
        cur.close()
        conn.close()
        return

    logger.info("Current rows in AZURE_VM: %d", initial_rows)

    # For fast batching: open source CSV and stream into temporary batch CSVs
    temp_dir = LOG_DIR / "temp_batches"
    temp_dir.mkdir(parents=True, exist_ok=True)

    batch_files = {r: open(temp_dir / f"batch_days_{r[0]}_{r[1]}.csv", "w", encoding="utf-8") for r in BATCH_RANGES}

    logger.info("Splitting CSV into %d trace_day batches...", len(BATCH_RANGES))
    split_start = time.time()
    line_count = 0

    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as src:
        for line in src:
            line_count += 1
            # 4th column is created_at
            # Fast split by comma
            parts = line.split(",", 4)
            if len(parts) >= 4:
                try:
                    created_at = float(parts[3])
                    trace_day = int(created_at // 86400)
                    for r in BATCH_RANGES:
                        if r[0] <= trace_day <= r[1]:
                            batch_files[r].write(line)
                            break
                except ValueError:
                    pass

    for f in batch_files.values():
        f.close()

    logger.info("Splitting finished in %.1fs (%d lines read). Loading batches into MySQL...", time.time() - split_start, line_count)

    for i, r in enumerate(BATCH_RANGES, 1):
        batch_path = temp_dir / f"batch_days_{r[0]}_{r[1]}.csv"
        batch_size_mb = batch_path.stat().st_size / 1048576
        logger.info("Batch %d/%d: trace_day [%d..%d] (file size: %.1f MB)", i, len(BATCH_RANGES), r[0], r[1], batch_size_mb)

        b_start = time.time()
        # Execute LOAD DATA LOCAL INFILE for this batch
        load_sql = f"""
            LOAD DATA LOCAL INFILE '{batch_path}'
            IGNORE INTO TABLE AZURE_VM
            FIELDS TERMINATED BY ','
            LINES TERMINATED BY '\\n'
            (vm_id, subscription_id, deployment_id, @created_at, deleted_at, max_cpu, avg_cpu, p95_max_cpu, vm_category, vcpu_bucket, memory_bucket)
            SET created_at = @created_at, trace_day = FLOOR(@created_at / 86400);
        """
        cur.execute(load_sql)
        conn.commit()

        logger.info("Batch %d loaded in %.1fs.", i, time.time() - b_start)

        # Record progress snapshot
        record_size_snapshot(conn, f"load-progress snapshot batch {i} (days {r[0]}-{r[1]})")

        # Cleanup batch file
        try:
            batch_path.unlink()
        except OSError:
            pass

    try:
        temp_dir.rmdir()
    except OSError:
        pass

    # Final verification
    cur.execute("SELECT COUNT(*) FROM AZURE_VM")
    final_count = cur.fetchone()[0]
    elapsed = time.time() - start_time

    logger.info("==================================================")
    logger.info("Load complete in %.1fs. Total rows in AZURE_VM: %d", elapsed, final_count)
    if final_count == EXPECTED_TOTAL_ROWS:
        logger.info("[PASS] Exactly matches expected row count: %d", EXPECTED_TOTAL_ROWS)
    else:
        logger.error("[FAIL] Row count mismatch: expected %d, got %d", EXPECTED_TOTAL_ROWS, final_count)
        sys.exit(1)

    cur.close()
    conn.close()


if __name__ == "__main__":
    load_batches()
