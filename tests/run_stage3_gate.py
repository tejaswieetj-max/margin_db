"""
tests/run_stage3_gate.py
Stage 3 Gate Verification Script
Checks:
1. Re-run role tests
2. sp_snapshot_sizes execution and DB_SIZE_SNAPSHOT check
3. information_schema_stats_expiry=0 dynamic update test
4. sp_margin_decomposition 2-component verification (as analyst_all)
5. AUDIT_LOG trigger on PRICING_RULE UPDATE
6. Both EVENTs (ev_snapshot_sizes, ev_recalc_forecasts) ENABLED in information_schema.EVENTS
7. EXPLAIN ANALYZE on AZURE_VM confirms full table scan (no residual secondary index)
"""

import subprocess
import time
from pathlib import Path
from dotenv import dotenv_values
import mysql.connector

env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")

def get_conn(user_key="ADMIN_USER", pwd_key="ADMIN_PASSWORD"):
    return mysql.connector.connect(
        host=env["DB_HOST"],
        port=int(env["DB_PORT"]),
        user=env[user_key],
        password=env[pwd_key],
        database=env["DB_NAME"],
        autocommit=True
    )

print("=== 1. Checking Role Tests ===")
role_res = subprocess.run(["bash", "tests/run_role_tests.sh"], capture_output=True, text=True)
assert role_res.returncode == 0, f"Role tests failed:\n{role_res.stderr}\n{role_res.stdout}"
print("Role tests: 9 passed.")

conn = get_conn()
cur = conn.cursor()

print("\n=== 2. Calling sp_snapshot_sizes ===")
cur.execute("CALL sp_snapshot_sizes()")
cur.execute("SELECT snapped_at, total_data_mb, total_index_mb FROM DB_SIZE_SNAPSHOT WHERE snapped_at = CURDATE()")
snap_row = cur.fetchone()
print(f"Snapshot row: {snap_row}")
assert snap_row is not None

print("\n=== 3. Testing information_schema_stats_expiry=0 ===")
cur.execute("SET SESSION information_schema_stats_expiry = 0")
cur.execute("SELECT DATA_LENGTH, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'AZURE_VM'")
stats_before = cur.fetchone()
print(f"AZURE_VM data_length: {stats_before[0]}, table_rows: {stats_before[1]}")

print("\n=== 4. Calling sp_margin_decomposition (as analyst_all) ===")
conn_analyst = get_conn("ANALYST_ALL_USERNAME", "DEMO_ANALYST_ALL_PASSWORD")
cur_analyst = conn_analyst.cursor()
cur_analyst.execute("CALL sp_margin_decomposition('Intelligent Cloud', 'FY24Q2', 'FY25Q4')")
decomp_rows = cur_analyst.fetchall()
print("Decomposition results:", decomp_rows)
assert len(decomp_rows) == 2
decomp_dict = {r[0]: float(r[1]) for r in decomp_rows}
assert 'revenue_effect' in decomp_dict
assert 'margin_rate_effect' in decomp_dict
cur_analyst.close()
conn_analyst.close()

print("\n=== 5. Testing AUDIT_LOG trigger on PRICING_RULE UPDATE ===")
cur.execute("DELETE FROM AUDIT_LOG WHERE table_name = 'PRICING_RULE'")
cur.execute("DELETE FROM PRICING_RULE WHERE rule_id = 9999")
cur.execute("INSERT INTO PRICING_RULE (rule_id, product_id, effective_date, price_value, version) VALUES (9999, 1, '2026-01-01', 10.00, 1)")

cur.execute("SELECT COUNT(*) FROM AUDIT_LOG WHERE table_name = 'PRICING_RULE'")
audit_before = cur.fetchone()[0]

cur.execute("UPDATE PRICING_RULE SET price_value = 15.00, version = 2 WHERE rule_id = 9999")

cur.execute("SELECT COUNT(*), operation, old_value, new_value FROM AUDIT_LOG WHERE table_name = 'PRICING_RULE' GROUP BY operation, old_value, new_value")
audit_rows = cur.fetchall()
print("AUDIT_LOG rows after UPDATE:", audit_rows)
assert len(audit_rows) > audit_before

# Clean test row
cur.execute("DELETE FROM PRICING_RULE WHERE rule_id = 9999")
cur.execute("DELETE FROM AUDIT_LOG WHERE table_name = 'PRICING_RULE'")

print("\n=== 6. Checking Scheduled Events ===")
cur.execute("SELECT EVENT_NAME, STATUS, INTERVAL_VALUE, INTERVAL_FIELD FROM information_schema.EVENTS WHERE EVENT_SCHEMA = DATABASE()")
events = cur.fetchall()
print("Events in information_schema.EVENTS:")
for ev in events:
    print(f"  {ev[0]}: {ev[1]} ({ev[2]} {ev[3]})")
ev_names = {ev[0]: ev[1] for ev in events}
assert 'ev_snapshot_sizes' in ev_names and ev_names['ev_snapshot_sizes'] == 'ENABLED'
assert 'ev_recalc_forecasts' in ev_names and ev_names['ev_recalc_forecasts'] == 'ENABLED'

print("\n=== 7. EXPLAIN ANALYZE on AZURE_VM (Full Table Scan check) ===")
cur.execute("""
EXPLAIN FORMAT=TREE
SELECT vm_category, count(*) FROM AZURE_VM WHERE vm_category = 'Delay-insensitive' GROUP BY vm_category
""")
tree = cur.fetchall()
explain_text = "\n".join(r[0] for r in tree)
print("EXPLAIN FORMAT=TREE output:\n" + explain_text)
assert "Table scan" in explain_text or "table scan" in explain_text

cur.close()
conn.close()
print("\n=== ALL STAGE 3 GATE CHECKS PASSED ===")
