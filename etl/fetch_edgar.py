"""
fetch_edgar.py — Fetch Microsoft quarterly financial facts from SEC EDGAR.

Fiscal year determination:
  Microsoft FY ends June 30.
  Formula: If end_month >= 7: fiscal_year = calendar_year + 1
           If end_month <= 6: fiscal_year = calendar_year

Quarterly identification by DURATION (~days between start and end):
  75-105 days:   Single quarter (Q1, or discrete Q2/Q3/Q4)
  165-195 days:  Q2 (YTD cumulative: 6 months)
  255-285 days:  Q3 (YTD cumulative: 9 months)
  350-380 days:  FY (annual)

Q4 derivation: Q4 = FY_annual - Q3_ytd (the 9-month YTD total)

Requirements: 4.1, 4.10, 21.1
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

EDGAR_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"

METRIC_MAP = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue"],
    "operating_income": ["OperatingIncomeLoss"],
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "raw" / "msft_facts.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
logger = logging.getLogger(__name__)


def fetch_company_facts(url: str, user_agent: str) -> dict:
    logger.info("Fetching %s with User-Agent '%s'", url, user_agent)
    try:
        response = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    except requests.RequestException as exc:
        utc_now = datetime.now(timezone.utc).isoformat()
        logger.error("%s | %s | network error: %s", utc_now, url, exc)
        sys.exit(1)

    if response.status_code != 200:
        utc_now = datetime.now(timezone.utc).isoformat()
        logger.error("%s | %s | HTTP %s", utc_now, url, response.status_code)
        sys.exit(1)

    return response.json()


def get_fiscal_year_from_end_date(end_date_str: str) -> int:
    """Determine fiscal year from period end date.
    Microsoft FY ends June 30.
    If end_month >= 7: fiscal_year = calendar_year + 1
    If end_month <= 6: fiscal_year = calendar_year
    """
    try:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        return end_date.year + 1 if end_date.month >= 7 else end_date.year
    except Exception:
        return None


def get_duration(start_str: str, end_str: str):
    try:
        s = datetime.strptime(start_str, "%Y-%m-%d")
        e = datetime.strptime(end_str, "%Y-%m-%d")
        return (e - s).days
    except Exception:
        return None


def classify_entry(start_str: str, end_str: str, form: str) -> str:
    dur = get_duration(start_str, end_str) if start_str and end_str else None
    try:
        end_dt = datetime.strptime(end_str, "%Y-%m-%d") if end_str else None
    except Exception:
        return None
    if not end_dt:
        return None
    month = end_dt.month

    if dur is not None:
        if 75 <= dur <= 105:
            if month in (9, 10):
                return "Q1"
            elif month in (12, 1):
                return "Q2_discrete"
            elif month in (3, 4):
                return "Q3_discrete"
            elif month in (6, 7):
                return "Q4_discrete"
        elif 165 <= dur <= 195:
            if month in (12, 1):
                return "Q2_ytd"
        elif 255 <= dur <= 285:
            if month in (3, 4):
                return "Q3_ytd"
        elif 350 <= dur <= 380:
            if month in (6, 7):
                return "FY_annual"
    else:
        if form == "10-K" and month in (6, 7):
            return "FY_annual"
    return None


def extract_facts(company_facts: dict) -> list[dict]:
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    missing_quarters: list[str] = []
    output: list[dict] = []

    for metric_name, concept_names in METRIC_MAP.items():
        all_entries = []
        for concept_name in concept_names:
            tag_data = us_gaap.get(concept_name)
            if tag_data:
                tag_entries = tag_data.get("units", {}).get("USD", [])
                logger.info("Found %d USD entries for %s under tag %s", len(tag_entries), metric_name, concept_name)
                for e in tag_entries:
                    all_entries.append((concept_name, e))
            else:
                logger.debug("Concept %s not found for %s", concept_name, metric_name)

        if not all_entries:
            logger.warning("No concepts found for %s (tried %s)", metric_name, concept_names)
            continue

        # Classify entries
        processed_by_period: dict[tuple[int, str], dict] = {}
        for tag, entry in all_entries:
            end = entry.get("end", "")
            start = entry.get("start", "")
            form = entry.get("form", "")
            if not end:
                continue

            fiscal_year = get_fiscal_year_from_end_date(end)
            if not fiscal_year:
                continue

            period_type = classify_entry(start, end, form)
            if not period_type:
                continue

            key = (fiscal_year, period_type)
            filed = entry.get("filed", "")
            # Deduplicate by keeping latest filed date per (fiscal_year, period_type)
            if key not in processed_by_period or filed > processed_by_period[key].get("filed", ""):
                processed_by_period[key] = {
                    "fiscal_year": fiscal_year,
                    "period_type": period_type,
                    "val": entry["val"],
                    "filed": filed,
                    "end": end,
                    "tag": tag,
                }

        # Resolve Q1, Q2, Q3, Q4 for each fiscal year
        all_fys = sorted(set(fy for (fy, _) in processed_by_period.keys()))
        resolved_quarters: dict[tuple[int, int], dict] = {}

        for fy in all_fys:
            q1 = processed_by_period.get((fy, "Q1"))
            q2_disc = processed_by_period.get((fy, "Q2_discrete"))
            q2_ytd = processed_by_period.get((fy, "Q2_ytd"))
            q3_disc = processed_by_period.get((fy, "Q3_discrete"))
            q3_ytd = processed_by_period.get((fy, "Q3_ytd"))
            q4_disc = processed_by_period.get((fy, "Q4_discrete"))
            fy_ann = processed_by_period.get((fy, "FY_annual"))

            # Q1
            if q1:
                resolved_quarters[(fy, 1)] = {
                    "val": q1["val"],
                    "filed": q1["filed"],
                    "derived": False,
                }
            else:
                missing_quarters.append(f"metric={metric_name} FY{fy} Q1 not found")

            # Q2: prefer discrete; else Q2_ytd - Q1
            val_q1 = resolved_quarters.get((fy, 1), {}).get("val")
            if q2_disc:
                resolved_quarters[(fy, 2)] = {
                    "val": q2_disc["val"],
                    "filed": q2_disc["filed"],
                    "derived": False,
                }
            elif q2_ytd and val_q1 is not None:
                resolved_quarters[(fy, 2)] = {
                    "val": q2_ytd["val"] - val_q1,
                    "filed": q2_ytd["filed"],
                    "derived": True,
                }
            else:
                missing_quarters.append(f"metric={metric_name} FY{fy} Q2 not found/derivable")

            # Q3: prefer discrete; else Q3_ytd - Q2_ytd
            if q3_disc:
                resolved_quarters[(fy, 3)] = {
                    "val": q3_disc["val"],
                    "filed": q3_disc["filed"],
                    "derived": False,
                }
            elif q3_ytd and q2_ytd:
                resolved_quarters[(fy, 3)] = {
                    "val": q3_ytd["val"] - q2_ytd["val"],
                    "filed": q3_ytd["filed"],
                    "derived": True,
                }
            else:
                missing_quarters.append(f"metric={metric_name} FY{fy} Q3 not found/derivable")

            # Q4: prefer discrete; else FY_annual - Q3_ytd; else FY_annual - (Q1+Q2+Q3)
            val_q2 = resolved_quarters.get((fy, 2), {}).get("val")
            val_q3 = resolved_quarters.get((fy, 3), {}).get("val")
            if q4_disc:
                resolved_quarters[(fy, 4)] = {
                    "val": q4_disc["val"],
                    "filed": q4_disc["filed"],
                    "derived": False,
                }
            elif fy_ann and q3_ytd:
                resolved_quarters[(fy, 4)] = {
                    "val": fy_ann["val"] - q3_ytd["val"],
                    "filed": fy_ann["filed"],
                    "derived": True,
                }
            elif fy_ann and val_q1 is not None and val_q2 is not None and val_q3 is not None:
                resolved_quarters[(fy, 4)] = {
                    "val": fy_ann["val"] - (val_q1 + val_q2 + val_q3),
                    "filed": fy_ann["filed"],
                    "derived": True,
                }
            else:
                if fy_ann:
                    missing_quarters.append(f"metric={metric_name} FY{fy} Q4 cannot derive (missing Q3_ytd/prior quarters)")

        for (fy, q), rec in sorted(resolved_quarters.items()):
            output.append({
                "fiscal_year": fy,
                "fiscal_quarter": q,
                "metric": metric_name,
                "value": rec["val"],
                "filed_date": rec.get("filed", ""),
            })

    if missing_quarters:
        logger.info("Missing/skipped quarters logged: %d instances", len(missing_quarters))

    return output


def validate_and_report(records: list[dict], raw_data: dict) -> bool:
    """Validate FY24 Q2=62,020, FY25 Q4=76,441, FY26 Q2=81,273 ($M),
    generate FY2022-FY2026 coverage table, and print quarters-vs-annual verification.
    """
    anchors = [
        (2024, 2, 62020),
        (2025, 4, 76441),
        (2026, 2, 81273),
    ]

    all_passed = True
    print("\n==================== ANCHOR VALIDATION ====================")
    for fy, q, exp_m in anchors:
        match = [r for r in records if r["fiscal_year"] == fy and r["fiscal_quarter"] == q and r["metric"] == "revenue"]
        if match:
            act_raw = match[0]["value"]
            act_m = act_raw // 1_000_000
            if act_m == exp_m:
                print(f"  [PASS] FY{fy} Q{q} revenue: expected ${exp_m:,}M, got ${act_m:,}M (raw: {act_raw})")
            else:
                print(f"  [FAIL] FY{fy} Q{q} revenue: expected ${exp_m:,}M, got ${act_m:,}M (raw: {act_raw})")
                all_passed = False
        else:
            print(f"  [FAIL] FY{fy} Q{q} revenue: NOT FOUND")
            all_passed = False

    print("\n==================== FY2022–FY2026 COVERAGE TABLE ($M) ====================")
    metrics = ["revenue", "cogs", "operating_income"]
    header = f"{'Fiscal Year':<12} | {'Metric':<16} | {'Q1 ($M)':<10} | {'Q2 ($M)':<10} | {'Q3 ($M)':<10} | {'Q4 ($M)':<10} | {'Sum ($M)':<12}"
    print(header)
    print("-" * len(header))

    rec_map = {(r["fiscal_year"], r["fiscal_quarter"], r["metric"]): r["value"] // 1_000_000 for r in records}

    for fy in range(2022, 2027):
        for m in metrics:
            q1 = rec_map.get((fy, 1, m))
            q2 = rec_map.get((fy, 2, m))
            q3 = rec_map.get((fy, 3, m))
            q4 = rec_map.get((fy, 4, m))
            vals = [v for v in (q1, q2, q3, q4) if v is not None]
            tot = sum(vals) if len(vals) == 4 else None
            q1_s = f"{q1:,}" if q1 is not None else "-"
            q2_s = f"{q2:,}" if q2 is not None else "-"
            q3_s = f"{q3:,}" if q3 is not None else "-"
            q4_s = f"{q4:,}" if q4 is not None else "-"
            tot_s = f"{tot:,}" if tot is not None else "-"
            print(f"FY{fy:<10} | {m:<16} | {q1_s:>10} | {q2_s:>10} | {q3_s:>10} | {q4_s:>10} | {tot_s:>12}")
        if fy < 2026:
            print("-" * len(header))

    print("\n==================== QUARTERS VS ANNUAL VERIFICATION ====================")
    # Check against raw 10-K annual figures
    us_gaap = raw_data.get("facts", {}).get("us-gaap", {})
    for m in metrics:
        print(f"\nMetric: {m.upper()}")
        concept_names = METRIC_MAP[m]
        # Find 10-K FY entries
        annual_map = {}
        for cname in concept_names:
            tag_data = us_gaap.get(cname)
            if tag_data:
                for entry in tag_data.get("units", {}).get("USD", []):
                    end = entry.get("end", "")
                    fy = get_fiscal_year_from_end_date(end)
                    form = entry.get("form", "")
                    dur = get_duration(entry.get("start", ""), end) if entry.get("start") and end else None
                    if fy and fy >= 2022:
                        if (dur and 350 <= dur <= 380) or (form == "10-K" and end.endswith("-06-30")):
                            filed = entry.get("filed", "")
                            if fy not in annual_map or filed > annual_map[fy]["filed"]:
                                annual_map[fy] = {"val": entry["val"] // 1_000_000, "filed": filed}

        for fy in range(2022, 2027):
            q1 = rec_map.get((fy, 1, m))
            q2 = rec_map.get((fy, 2, m))
            q3 = rec_map.get((fy, 3, m))
            q4 = rec_map.get((fy, 4, m))
            if q1 is not None and q2 is not None and q3 is not None and q4 is not None:
                q_sum = q1 + q2 + q3 + q4
                ann_val = annual_map.get(fy, {}).get("val")
                if ann_val is not None:
                    diff = q_sum - ann_val
                    match_str = "MATCH" if diff == 0 else f"DIFF={diff}"
                    print(f"  FY{fy}: Q1+Q2+Q3+Q4 = {q_sum:,} | 10-K Annual = {ann_val:,} | [{match_str}]")
                else:
                    print(f"  FY{fy}: Q1+Q2+Q3+Q4 = {q_sum:,} | 10-K Annual not found")
            else:
                print(f"  FY{fy}: Incomplete quarters")

    return all_passed


def main() -> None:
    load_dotenv(override=True)

    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent or "@" not in user_agent:
        logger.error("SEC_USER_AGENT '%s' is missing or not a valid 'Name email' format", user_agent)
        sys.exit(1)

    company_facts = fetch_company_facts(EDGAR_URL, user_agent)
    records = extract_facts(company_facts)

    if not records:
        logger.error("No records extracted")
        sys.exit(1)

    logger.info("Extracted %d quarterly fact records", len(records))

    valid = validate_and_report(records, company_facts)
    if not valid:
        logger.error("Validation checks failed")
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    logger.info("Written to %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
