"""
tests/test_concurrency.py
Requirement 18.2: Optimistic Lock Concurrency Test

Setup:
- Admin inserts test PRICING_RULE (rule_id=1, product_id=1, price_value=10.0, version=1)
- Analyst logs in to obtain JWT token
- Spawns two threads simultaneously submitting PUT /pricing/1 with expected_version=1
- Asserts sorted HTTP status codes are [200, 409]
- Asserts CONFLICT_LOG has exactly one conflict entry for rule_id=1
- Teardown: Clean up test rule and conflict log entries
"""

import concurrent.futures
import os
import pyodbc
import pytest
from dotenv import dotenv_values
from starlette.testclient import TestClient

from backend.main import app

env = dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env"))


def get_admin_conn():
    conn_str = (
        f"DRIVER={{{env.get('ODBC_DRIVER', 'MySQL ODBC 8.0 Unicode Driver')}}};"
        f"SERVER={env['DB_HOST']};"
        f"PORT={env['DB_PORT']};"
        f"DATABASE={env['DB_NAME']};"
        f"UID={env['ADMIN_USER']};"
        f"PWD={env['ADMIN_PASSWORD']};"
        f"charset=utf8mb4;"
    )
    return pyodbc.connect(conn_str, timeout=10)


def test_pricing_rule_optimistic_lock_concurrency():
    client = TestClient(app)

    # 1. Login as business analyst
    login_res = client.post("/auth/login", json={
        "username": env["ANALYST_DEMO_USERNAME"],
        "password": env["DEMO_ANALYST_PASSWORD"]
    })
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    token = login_res.json()["token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    rule_id = 9001
    init_price = 100.0
    init_version = 1

    admin_conn = get_admin_conn()
    cur = admin_conn.cursor()
    try:
        # Clean potential leftover state
        cur.execute("DELETE FROM CONFLICT_LOG WHERE rule_id = ?", (rule_id,))
        cur.execute("DELETE FROM PRICING_RULE WHERE rule_id = ?", (rule_id,))
        # Insert initial test rule
        cur.execute("""
            INSERT INTO PRICING_RULE (rule_id, product_id, effective_date, price_value, version)
            VALUES (?, 1, '2026-01-01', ?, ?)
        """, (rule_id, init_price, init_version))
        admin_conn.commit()

        # 2. Concurrently submit 2 PUT requests with expected_version=1
        def update_worker(new_val):
            return client.put(
                f"/pricing/{rule_id}",
                json={"new_value": new_val, "expected_version": init_version},
                headers=auth_header
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(update_worker, 110.0)
            fut2 = executor.submit(update_worker, 120.0)
            res1 = fut1.result()
            res2 = fut2.result()

        status_codes = sorted([res1.status_code, res2.status_code])
        assert status_codes == [200, 409], f"Expected [200, 409] but got {status_codes}"

        # 3. Verify exactly one conflict log row was created
        cur.execute("SELECT COUNT(*) FROM CONFLICT_LOG WHERE rule_id = ?", (rule_id,))
        conflict_count = cur.fetchone()[0]
        assert conflict_count == 1, f"Expected 1 conflict row, got {conflict_count}"

        # Verify rule version is now init_version + 1
        cur.execute("SELECT version FROM PRICING_RULE WHERE rule_id = ?", (rule_id,))
        new_version = cur.fetchone()[0]
        assert new_version == init_version + 1, f"Expected version {init_version + 1}, got {new_version}"

    finally:
        # Teardown
        cur.execute("DELETE FROM CONFLICT_LOG WHERE rule_id = ?", (rule_id,))
        cur.execute("DELETE FROM PRICING_RULE WHERE rule_id = ?", (rule_id,))
        admin_conn.commit()
        cur.close()
        admin_conn.close()


if __name__ == "__main__":
    pytest.main(["-v", __file__])
