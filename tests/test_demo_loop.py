"""
tests/test_demo_loop.py
Requirements: 11.6, 18.3: End-to-End Database Intelligence Loop Test

Steps:
1. Drop any prior test index on AZURE_VM via admin connection.
2. Step 1: POST /sql with Heavy_Demo_Query as business_analyst; record pre-index duration_ms.
3. Step 2: Poll GET /index-recs as db_engineer up to 60s until rec_id appears.
4. Step 3: POST /index-recs/{rec_id}/approve as db_engineer; assert HTTP 200.
5. Step 4: Poll information_schema.STATISTICS via admin up to 30s until index is VISIBLE.
6. Step 5: Rerun Heavy_Demo_Query 2x via POST /sql; assert duration_ms <= 0.5 * pre-index duration_ms.
7. Step 6: POST /forecast/database/{future_event_id} as db_engineer; assert HTTP 200.
8. Step 7: GET /capacity/forecast; assert days_to_90pct is not None and >= 1 at-risk query.
9. Assert total elapsed time <= 120 seconds.
"""

import os
import time
import pyodbc
import pytest
from dotenv import dotenv_values
from starlette.testclient import TestClient

from backend.main import app

env = dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env"))

HEAVY_DEMO_QUERY = """
SELECT
    vm_category,
    vcpu_bucket,
    COUNT(*)                     AS vm_count,
    ROUND(AVG(avg_cpu), 4)       AS mean_avg_cpu,
    ROUND(AVG(max_cpu), 4)       AS mean_max_cpu,
    ROUND(AVG(p95_max_cpu), 4)   AS mean_p95_cpu
FROM v_vm_workload
WHERE vm_category = 'Delay-insensitive'
  AND vcpu_bucket = '8'
  AND created_at BETWEEN 500000 AND 2000000
GROUP BY
    vm_category,
    vcpu_bucket
"""


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
    conn = pyodbc.connect(conn_str, timeout=10)
    conn.autocommit = True
    return conn



def drop_prior_indexes(admin_conn):
    cur = admin_conn.cursor()
    cur.execute("""
        SELECT DISTINCT INDEX_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'AZURE_VM'
          AND (INDEX_NAME LIKE 'idx_rec_%' OR INDEX_NAME = 'idx_stage2_test')
    """)
    indexes = [r[0] for r in cur.fetchall()]
    for idx in indexes:
        try:
            cur.execute(f"ALTER TABLE AZURE_VM DROP INDEX {idx}")
            admin_conn.commit()
        except Exception:
            pass
    try:
        cur.execute("""
            UPDATE INDEX_RECOMMENDATION
            SET status = 'rejected'
            WHERE table_name = 'AZURE_VM'
              AND status IN ('proposed', 'testing', 'applied')
        """)
        admin_conn.commit()
    except Exception:
        pass
    cur.close()


def test_demo_loop():
    start_total_time = time.time()
    client = TestClient(app)

    # Clean prior test indexes
    admin_conn = get_admin_conn()
    drop_prior_indexes(admin_conn)

    # 1. Login as business analyst
    analyst_login = client.post("/auth/login", json={
        "username": env["ANALYST_DEMO_USERNAME"],
        "password": env["DEMO_ANALYST_PASSWORD"]
    })
    assert analyst_login.status_code == 200
    analyst_token = analyst_login.json()["token"]
    analyst_headers = {"Authorization": f"Bearer {analyst_token}"}

    # 2. Login as db engineer
    engineer_login = client.post("/auth/login", json={
        "username": env["ENGINEER_DEMO_USERNAME"],
        "password": env["DEMO_ENGINEER_PASSWORD"]
    })
    assert engineer_login.status_code == 200
    engineer_token = engineer_login.json()["token"]
    engineer_headers = {"Authorization": f"Bearer {engineer_token}"}

    # Step 1: POST /sql with Heavy_Demo_Query (scans AZURE_VM via v_vm_workload)
    sql_res = client.post("/sql", json={"sql": HEAVY_DEMO_QUERY}, headers=analyst_headers)
    assert sql_res.status_code == 200, f"Query failed: {sql_res.text}"
    sql_data = sql_res.json()
    qe_id = sql_data.get("qe_id")
    pre_duration_ms = sql_data.get("duration_ms", 0.0)
    flagged = sql_data.get("flagged")

    assert qe_id is not None, "Missing qe_id in response"
    assert flagged is True, "Expected Heavy_Demo_Query to be flagged as full table scan"
    assert pre_duration_ms > 0, "Expected positive pre_duration_ms"

    # Step 2: Poll GET /index-recs up to 60s for this qe_id
    rec_id = None
    index_name = None
    poll_deadline = time.time() + 60
    while time.time() < poll_deadline:
        recs_res = client.get("/index-recs", headers=engineer_headers)
        if recs_res.status_code == 200:
            recs = recs_res.json()
            matching = [r for r in recs if r.get("qe_id") == qe_id]
            if matching and matching[0].get("after_ms") is not None:
                rec_id = matching[0]["rec_id"]
                index_name = matching[0]["index_name"]
                break
        time.sleep(2)

    assert rec_id is not None, f"Timed out waiting for index recommendation for qe_id={qe_id}"

    # Step 3: POST /index-recs/{rec_id}/approve
    approve_res = client.post(f"/index-recs/{rec_id}/approve", headers=engineer_headers)
    assert approve_res.status_code == 200, f"Approval failed: {approve_res.text}"

    # Step 4: Poll information_schema.STATISTICS via admin connection until index is visible
    poll_stat_deadline = time.time() + 30
    is_visible = False
    cur_admin = admin_conn.cursor()
    try:
        while time.time() < poll_stat_deadline:
            cur_admin.execute("""
                SELECT COUNT(*), IS_VISIBLE
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'AZURE_VM'
                  AND INDEX_NAME = ?
                GROUP BY IS_VISIBLE
            """, (index_name,))
            row = cur_admin.fetchone()
            if row and row[0] > 0 and row[1] == "YES":
                is_visible = True
                break
            time.sleep(1)
    finally:
        cur_admin.close()

    assert is_visible is True, f"Index {index_name} was not made VISIBLE within 30s"

    # Step 5: Rerun Heavy_Demo_Query 2x via POST /sql; assert duration_ms <= 0.5 * pre_duration_ms
    post_durations = []
    for _ in range(2):
        rerun_res = client.post("/sql", json={"sql": HEAVY_DEMO_QUERY}, headers=analyst_headers)
        assert rerun_res.status_code == 200
        post_durations.append(rerun_res.json().get("duration_ms", 0.0))

    for post_dur in post_durations:
        assert post_dur <= 0.5 * pre_duration_ms, (
            f"Post-index duration {post_dur}ms not <= 50% of pre-index duration {pre_duration_ms}ms"
        )

    # Step 6: POST /forecast/database/{event_id} with known future game_launch event_id (8)
    fc_res = client.post("/forecast/database/8", headers=engineer_headers)
    assert fc_res.status_code == 200, f"Database forecast failed: {fc_res.text}"

    # Step 7: GET /capacity/forecast
    cap_res = client.get("/capacity/forecast", headers=engineer_headers)
    assert cap_res.status_code == 200
    cap_data = cap_res.json()

    assert cap_data.get("days_to_90pct") is not None, "Expected days_to_90pct to be non-null"
    at_risk_list = cap_data.get("at_risk_queries", [])
    assert len(at_risk_list) >= 1, "Expected at least one at-risk query in capacity forecast"

    # Check at-risk formula on returned item: projected_ms >= AT_RISK_FACTOR (1.25) * baseline_ms
    at_risk_item = at_risk_list[0]
    b_ms = at_risk_item["baseline_ms"]
    p_ms = at_risk_item["projected_ms"]
    assert p_ms >= 1.25 * b_ms, f"Projected ms {p_ms} not >= 1.25 * {b_ms}"

    # Cleanup test index
    drop_prior_indexes(admin_conn)
    admin_conn.close()

    total_time = time.time() - start_total_time
    assert total_time <= 120.0, f"Demo loop exceeded 120 seconds limit (took {total_time:.2f}s)"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
