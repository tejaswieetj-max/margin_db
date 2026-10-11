"""
profile_azure_csv.py — Profile the Azure Public Dataset V2 VM table CSV
to recommend precise DDL data types, partitioning boundaries, and nullability.
"""

import csv
import gzip
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = PROJECT_ROOT / "data" / "raw" / "azure_v2" / "vmtable.csv"
if not CSV_PATH.exists():
    CSV_PATH = PROJECT_ROOT / "data" / "raw" / "azure" / "vmtable.csv"

COL_NAMES = [
    "vm_id",
    "subscription_id",
    "deployment_id",
    "created_at",
    "deleted_at",
    "max_cpu",
    "avg_cpu",
    "p95_max_cpu",
    "vm_category",
    "vcpu_bucket",
    "memory_bucket",
]

def profile():
    print(f"Profiling {CSV_PATH} ...")
    row_count = 0
    max_lens = {c: 0 for c in COL_NAMES}
    null_counts = {c: 0 for c in COL_NAMES}
    min_vals = {c: float("inf") for c in ["created_at", "deleted_at", "max_cpu", "avg_cpu", "p95_max_cpu"]}
    max_vals = {c: float("-inf") for c in ["created_at", "deleted_at", "max_cpu", "avg_cpu", "p95_max_cpu"]}
    min_trace_day = float("inf")
    max_trace_day = float("-inf")
    distinct_categories = set()
    distinct_vcpu = set()
    distinct_mem = set()

    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            row_count += 1
            if len(row) != 11:
                print(f"Row {row_count} has {len(row)} columns instead of 11: {row}")
                continue

            for col, val in zip(COL_NAMES, row):
                val_str = val.strip()
                max_lens[col] = max(max_lens[col], len(val_str))
                if not val_str or val_str == "\\N" or val_str.upper() == "NULL":
                    null_counts[col] += 1
                else:
                    if col in ("created_at", "deleted_at"):
                        try:
                            num = float(val_str)
                            min_vals[col] = min(min_vals[col], num)
                            max_vals[col] = max(max_vals[col], num)
                            if col == "created_at":
                                day = int(num // 86400)
                                if day < min_trace_day:
                                    min_trace_day = day
                                if day > max_trace_day:
                                    max_trace_day = day
                        except ValueError:
                            pass
                    elif col in ("max_cpu", "avg_cpu", "p95_max_cpu"):
                        try:
                            num = float(val_str)
                            min_vals[col] = min(min_vals[col], num)
                            max_vals[col] = max(max_vals[col], num)
                        except ValueError:
                            pass
                    elif col == "vm_category":
                        if len(distinct_categories) < 50:
                            distinct_categories.add(val_str)
                    elif col == "vcpu_bucket":
                        if len(distinct_vcpu) < 50:
                            distinct_vcpu.add(val_str)
                    elif col == "memory_bucket":
                        if len(distinct_mem) < 50:
                            distinct_mem.add(val_str)

            if row_count % 500000 == 0:
                print(f"  Processed {row_count:,} rows...")

    print(f"\nTotal rows: {row_count:,}")
    print("\nColumn statistics:")
    for col in COL_NAMES:
        print(f"  {col:<16}: max_len={max_lens[col]:<4} nulls={null_counts[col]:<8}")

    print("\nNumeric ranges:")
    for col in min_vals:
        print(f"  {col:<16}: min={min_vals[col]}  max={max_vals[col]}")

    print(f"\nTrace day range: min={min_trace_day} max={max_trace_day}")
    print(f"Distinct vm_category ({len(distinct_categories)}): {distinct_categories}")
    print(f"Distinct vcpu_bucket ({len(distinct_vcpu)}): {distinct_vcpu}")
    print(f"Distinct memory_bucket ({len(distinct_mem)}): {distinct_mem}")

if __name__ == "__main__":
    profile()
