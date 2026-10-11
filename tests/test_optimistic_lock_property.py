"""
tests/test_optimistic_lock_property.py
Property 1: Optimistic Lock Exclusivity
Validates: Requirements 12.1, 12.2, 12.3

Property:
For N concurrent updates to PRICING_RULE with the same expected_version:
- Exactly one update succeeds (status = 'OK') and increments version by 1
- Exactly N - 1 updates fail with status = 'CONFLICT'
- CONFLICT_LOG contains exactly N - 1 entries for that rule
- Final version in PRICING_RULE is exactly initial_version + 1
"""

import concurrent.futures
from pathlib import Path
from dotenv import dotenv_values
import mysql.connector
import pytest
from hypothesis import given, settings, strategies as st

env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")

def get_db_conn():
    return mysql.connector.connect(
        host=env["DB_HOST"],
        port=int(env["DB_PORT"]),
        user=env["ADMIN_USER"],
        password=env["ADMIN_PASSWORD"],
        database=env["DB_NAME"],
        autocommit=True
    )

def perform_update(rule_id, new_value, expected_version):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("CALL sp_update_pricing_rule(%s, %s, %s, @status)", (rule_id, new_value, expected_version))
    cur.execute("SELECT @status")
    status = cur.fetchone()[0]
    cur.close()
    conn.close()
    return status

@settings(max_examples=10, deadline=15000)
@given(
    n_attempts=st.integers(min_value=2, max_value=8),
    base_price=st.floats(min_value=10.0, max_value=500.0, allow_nan=False, allow_infinity=False)
)
def test_optimistic_lock_concurrency(n_attempts, base_price):
    rule_id = 8888
    init_version = 1
    
    # Setup test rule
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM CONFLICT_LOG WHERE rule_id = %s", (rule_id,))
    cur.execute("DELETE FROM PRICING_RULE WHERE rule_id = %s", (rule_id,))
    cur.execute(
        "INSERT INTO PRICING_RULE (rule_id, product_id, effective_date, price_value, version) VALUES (%s, 1, '2026-01-01', %s, %s)",
        (rule_id, round(base_price, 4), init_version)
    )
    cur.close()
    conn.close()

    # Run N concurrent attempts with the same expected_version
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_attempts) as executor:
        futures = [
            executor.submit(perform_update, rule_id, round(base_price + i + 1, 4), init_version)
            for i in range(n_attempts)
        ]
        results = [f.result() for f in futures]

    ok_count = results.count("OK")
    conflict_count = results.count("CONFLICT")

    assert ok_count == 1, f"Expected exactly 1 OK, got {ok_count} in {results}"
    assert conflict_count == n_attempts - 1, f"Expected {n_attempts - 1} CONFLICTs, got {conflict_count}"

    # Verify database state
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT version FROM PRICING_RULE WHERE rule_id = %s", (rule_id,))
    final_ver = cur.fetchone()[0]
    assert final_ver == init_version + 1

    cur.execute("SELECT COUNT(*) FROM CONFLICT_LOG WHERE rule_id = %s", (rule_id,))
    conflicts_logged = cur.fetchone()[0]
    assert conflicts_logged == n_attempts - 1

    # Cleanup
    cur.execute("DELETE FROM CONFLICT_LOG WHERE rule_id = %s", (rule_id,))
    cur.execute("DELETE FROM PRICING_RULE WHERE rule_id = %s", (rule_id,))
    cur.close()
    conn.close()

if __name__ == "__main__":
    pytest.main(["-v", __file__])
