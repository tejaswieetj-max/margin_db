"""
tests/test_stage3_procedures.py
Verification suite for Stage 3 stored procedures:
- sp_snapshot_sizes
- sp_margin_decomposition
- sp_forecast_business
- sp_update_pricing_rule
- sp_recommend_index
- sp_apply_index
- sp_reject_index
- sp_add_partition
- sp_forecast_database
- sp_insert_qe
- sp_insert_qpn
- sp_record_index_test
- sp_flag_query
"""

import pytest
from pathlib import Path
from dotenv import dotenv_values
import mysql.connector

env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")

def get_connection(user_key="ADMIN_USER", pwd_key="ADMIN_PASSWORD"):
    return mysql.connector.connect(
        host=env["DB_HOST"],
        port=int(env["DB_PORT"]),
        user=env[user_key],
        password=env[pwd_key],
        database=env["DB_NAME"],
        autocommit=True
    )

def exec_query(sql, user_key="ADMIN_USER", pwd_key="ADMIN_PASSWORD"):
    conn = get_connection(user_key, pwd_key)
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall() if cur.description else []
    cur.close()
    conn.close()
    return rows

def test_sp_snapshot_sizes():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("CALL sp_snapshot_sizes()")
    cur.execute("SELECT COUNT(*) FROM DB_SIZE_SNAPSHOT WHERE snapped_at = CURDATE()")
    count = cur.fetchone()[0]
    assert count >= 1
    cur.close()
    conn.close()

def test_sp_margin_decomposition_analyst_all():
    conn = get_connection("ANALYST_ALL_USERNAME", "DEMO_ANALYST_ALL_PASSWORD")
    cur = conn.cursor()
    cur.execute("CALL sp_margin_decomposition('Intelligent Cloud', 'FY24Q2', 'FY25Q4')")
    rows = cur.fetchall()
    assert len(rows) == 2
    comp_map = {r[0]: float(r[1]) for r in rows}
    assert 'revenue_effect' in comp_map
    assert 'margin_rate_effect' in comp_map
    cur.close()
    conn.close()

def test_sp_margin_decomposition_analyst_demo_mpc():
    conn = get_connection("ANALYST_DEMO_USERNAME", "DEMO_ANALYST_PASSWORD")
    cur = conn.cursor()
    cur.execute("CALL sp_margin_decomposition('More Personal Computing', 'FY24Q2', 'FY25Q4')")
    rows = cur.fetchall()
    assert len(rows) == 2
    cur.close()
    conn.close()

    conn2 = get_connection("ANALYST_DEMO_USERNAME", "DEMO_ANALYST_PASSWORD")
    cur2 = conn2.cursor()
    cur2.execute("CALL sp_margin_decomposition('Intelligent Cloud', 'FY24Q2', 'FY25Q4')")
    rows_ic = cur2.fetchall()
    assert len(rows_ic) == 0
    cur2.close()
    conn2.close()

def test_sp_forecast_business_measured():
    conn = get_connection("ANALYST_DEMO_USERNAME", "DEMO_ANALYST_PASSWORD")
    cur = conn.cursor()
    cur.execute("CALL sp_forecast_business(2)")
    rows = cur.fetchall()
    assert len(rows) >= 6
    metrics = {r[2]: (r[3], r[4]) for r in rows}
    assert metrics['lift_source'][1] == 'measured'
    assert metrics['confidence_tier'][1] == 'high'
    assert metrics['projected_revenue_impact'][0] is not None
    cur.close()
    conn.close()

def test_sp_forecast_business_derived():
    conn = get_connection("ANALYST_DEMO_USERNAME", "DEMO_ANALYST_PASSWORD")
    cur = conn.cursor()
    cur.execute("CALL sp_forecast_business(8)")
    rows = cur.fetchall()
    metrics = {r[2]: (r[3], r[4]) for r in rows}
    assert metrics['lift_source'][1] == 'derived_from_prior_launches'
    assert metrics['confidence_tier'][1] == 'medium'
    assert metrics['n_prior_launches'][0] >= 1
    cur.close()
    conn.close()

def test_sp_update_pricing_rule_optimistic_lock():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM PRICING_RULE WHERE rule_id = 999")
    cur.execute("INSERT INTO PRICING_RULE (rule_id, product_id, effective_date, price_value, version) VALUES (999, 1, '2026-01-01', 50.00, 1)")
    
    # Successful update
    cur.execute("CALL sp_update_pricing_rule(999, 55.00, 1, @status)")
    cur.execute("SELECT @status, version, price_value FROM PRICING_RULE WHERE rule_id = 999")
    status, ver, val = cur.fetchone()
    assert status == 'OK'
    assert ver == 2
    assert float(val) == 55.00

    # Conflict update (stale version 1)
    cur.execute("CALL sp_update_pricing_rule(999, 60.00, 1, @status)")
    cur.execute("SELECT @status, version FROM PRICING_RULE WHERE rule_id = 999")
    status, ver = cur.fetchone()
    assert status == 'CONFLICT'
    assert ver == 2

    # Check conflict log
    cur.execute("SELECT COUNT(*) FROM CONFLICT_LOG WHERE rule_id = 999")
    assert cur.fetchone()[0] >= 1

    # Clean up
    cur.execute("DELETE FROM CONFLICT_LOG WHERE rule_id = 999")
    cur.execute("DELETE FROM PRICING_RULE WHERE rule_id = 999")
    cur.close()
    conn.close()

def test_sp_telemetry_and_index_lifecycle():
    conn = get_connection()
    cur = conn.cursor()

    # Insert a valid query execution via sp_insert_qe
    cur.execute("CALL sp_insert_qe('engineer_demo', 'SELECT * FROM AZURE_VM WHERE vm_category = ?', NOW(3), 5000, 2000000, 100, @qe_id)")
    cur.execute("SELECT @qe_id")
    qe_id = cur.fetchone()[0]
    assert qe_id is not None

    # Insert plan node
    cur.execute(f"CALL sp_insert_qpn({qe_id}, 1, NULL, 'Table Scan', 'AZURE_VM', 100.0, 2695548, 2695548)")

    # Flag query
    cur.execute(f"CALL sp_flag_query({qe_id})")
    cur.execute(f"SELECT flagged FROM QUERY_EXECUTION WHERE qe_id = {qe_id}")
    assert cur.fetchone()[0] == 1

    # 1. Invalid table
    with pytest.raises(Exception) as excinfo:
        cur.execute(f"CALL sp_recommend_index({qe_id}, 'INVALID_TABLE', 'col1', @rec_id)")
    assert '45001' in str(excinfo.value)

    # 2. Invalid column
    with pytest.raises(Exception) as excinfo:
        cur.execute(f"CALL sp_recommend_index({qe_id}, 'AZURE_VM', 'non_existent_column', @rec_id)")
    assert '45002' in str(excinfo.value)

    # 3. Clean any existing rec
    cur.execute("DELETE FROM INDEX_RECOMMENDATION WHERE table_name = 'AZURE_VM' AND columns_csv = 'vm_category,vcpu_bucket'")
    
    # 4. Valid recommend index 1
    cur.execute(f"CALL sp_recommend_index({qe_id}, 'AZURE_VM', 'vm_category,vcpu_bucket', @rec_id)")
    cur.execute("SELECT @rec_id")
    rec_id1 = cur.fetchone()[0]
    assert rec_id1 is not None

    cur.execute(f"SELECT status, index_name FROM INDEX_RECOMMENDATION WHERE rec_id = {rec_id1}")
    status1, idx_name1 = cur.fetchone()
    assert status1 == 'testing'

    # Check index exists as INVISIBLE
    cur.execute(f"SELECT IS_VISIBLE FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'AZURE_VM' AND INDEX_NAME = '{idx_name1}' LIMIT 1")
    assert cur.fetchone()[0] == 'NO'
    cur.close()
    conn.close()

    # 5. Apply before test -> returns 'Index test not yet complete'
    res = exec_query(f"CALL sp_apply_index({rec_id1})")
    assert res[0][0] == 'Index test not yet complete'

    # 6. Record index test
    exec_query(f"CALL sp_record_index_test({rec_id1}, 5000, 10)")
    res = exec_query(f"SELECT before_ms, after_ms FROM INDEX_RECOMMENDATION WHERE rec_id = {rec_id1}")
    assert res[0] == (5000, 10)

    # 7. Apply index -> VISIBLE
    res = exec_query(f"CALL sp_apply_index({rec_id1})")
    assert res[0][0] == 'applied'

    res = exec_query(f"SELECT IS_VISIBLE FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'AZURE_VM' AND INDEX_NAME = '{idx_name1}' LIMIT 1")
    assert res[0][0] == 'YES'

    # Drop index 1
    exec_query(f"ALTER TABLE AZURE_VM DROP INDEX `{idx_name1}`")

    # 8. Recommend index 2 to test reject flow
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM INDEX_RECOMMENDATION WHERE table_name = 'AZURE_VM' AND columns_csv = 'vm_category,memory_bucket'")
    cur.execute(f"CALL sp_recommend_index({qe_id}, 'AZURE_VM', 'vm_category,memory_bucket', @rec_id)")
    cur.execute("SELECT @rec_id")
    rec_id2 = cur.fetchone()[0]
    cur.execute(f"SELECT index_name FROM INDEX_RECOMMENDATION WHERE rec_id = {rec_id2}")
    idx_name2 = cur.fetchone()[0]
    cur.close()
    conn.close()

    # Reject index 2 -> drops index, status='rejected'
    res = exec_query(f"CALL sp_reject_index({rec_id2})")
    assert res[0][0] == 'rejected'

    res = exec_query(f"SHOW INDEX FROM AZURE_VM WHERE Key_name = '{idx_name2}'")
    assert len(res) == 0

    # Clean up
    exec_query(f"DELETE FROM INDEX_RECOMMENDATION WHERE rec_id IN ({rec_id1}, {rec_id2})")
    exec_query(f"DELETE FROM QUERY_PLAN_NODE WHERE qe_id = {qe_id}")
    exec_query(f"DELETE FROM QUERY_EXECUTION WHERE qe_id = {qe_id}")

def test_sp_add_partition():
    conn = get_connection("ENGINEER_DEMO_USERNAME", "DEMO_ENGINEER_PASSWORD")
    cur = conn.cursor()

    # Invalid table raises 45003
    with pytest.raises(Exception) as excinfo:
        cur.execute("CALL sp_add_partition('FACT_SALES', '2026-10-01')")
    assert '45003' in str(excinfo.value)

    # Valid add partition on AZURE_VM
    cur.execute("CALL sp_add_partition('AZURE_VM', '2026-10-01')")
    row = cur.fetchone()
    assert row[0] in ('partition_added', 'already_exists')
    assert 'p_FY27Q2' in row[1]
    cur.close()
    conn.close()

def test_sp_forecast_database():
    conn = get_connection("ENGINEER_DEMO_USERNAME", "DEMO_ENGINEER_PASSWORD")
    cur = conn.cursor()
    cur.execute("CALL sp_forecast_database(2)")
    rows = cur.fetchall()
    assert len(rows) >= 2
    cur.close()
    conn.close()

if __name__ == "__main__":
    pytest.main(["-v", __file__])
