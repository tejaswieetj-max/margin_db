import os
import re
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

from backend.db import call_sp, call_sp_out, get_connection

load_dotenv()

SVC_TELEMETRY_USER = os.environ.get("SVC_TELEMETRY_USER", "svc_telemetry")
SVC_TELEMETRY_PASSWORD = os.environ.get("SVC_TELEMETRY_PASSWORD", "teluser")


def parse_explain_tree(explain_text: str) -> Tuple[List[Dict[str, Any]], float, int]:
    """
    Parse MySQL 8.0/8.4 EXPLAIN ANALYZE tree output into structured query plan nodes.
    Returns: (nodes_list, duration_ms, rows_examined)
    """
    if isinstance(explain_text, list):
        raw_lines = explain_text
    else:
        raw_lines = explain_text.strip().split("\n")

    lines = [line for line in raw_lines if line.strip()]
    if not lines:
        return [], 0.0, 0

    nodes: List[Dict[str, Any]] = []
    # Indent stack entries: (depth, node_id)
    stack: List[Tuple[int, int]] = []

    for idx, line in enumerate(lines):
        node_id = idx + 1
        leading_spaces = len(line) - len(line.lstrip(" "))
        depth = leading_spaces // 2

        # Pop stack until parent has depth < current depth
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent_node_id = stack[-1][1] if stack else None
        stack.append((depth, node_id))

        cleaned_line = line.strip()
        if cleaned_line.startswith("-> "):
            cleaned_line = cleaned_line[3:].strip()

        # Extract operation (text before cost / actual time / double space parenthetical)
        op_match = re.split(r"\s{2,}|\s*\(cost=|\s*\(actual time=", cleaned_line)
        operation = op_match[0].strip() if op_match else cleaned_line
        if len(operation) > 200:
            operation = operation[:197] + "..."

        # Extract table_name
        table_name = None
        tbl_match = re.search(r"(?:Table scan|Index range scan|Index lookup|Index scan|Scan) on\s+([a-zA-Z0-9_]+)", cleaned_line, re.IGNORECASE)
        if tbl_match:
            table_name = tbl_match.group(1).upper()
        elif "azure_vm" in cleaned_line.lower():
            table_name = "AZURE_VM"

        # Parse actual time=N..M rows=R loops=L
        actual_ms = 0.0
        loops = 1
        actual_rows = 0

        time_match = re.search(r"actual time=([\d\.]+)\.\.([\d\.]+)", cleaned_line)
        if time_match:
            actual_ms = float(time_match.group(2))

        loops_match = re.search(r"loops=(\d+)", cleaned_line)
        if loops_match:
            loops = int(loops_match.group(1))

        rows_match = re.search(r"actual time=.*?\brows=([0-9.e+-]+)", cleaned_line)
        if rows_match:
            try:
                actual_rows = int(float(rows_match.group(1)))
            except ValueError:
                actual_rows = 0

        # Parse est_rows from (cost=... rows=...)
        est_rows = None
        est_match = re.search(r"\(cost=[\d\.]+\s+rows=([0-9.e+-]+)\)", cleaned_line)
        if est_match:
            try:
                est_rows = int(float(est_match.group(1)))
            except ValueError:
                est_rows = None

        total_ms = actual_ms * loops

        nodes.append({
            "node_id": node_id,
            "parent_node_id": parent_node_id,
            "operation": operation,
            "table_name": table_name,
            "actual_ms": actual_ms,
            "loops": loops,
            "total_ms": total_ms,
            "est_rows": est_rows,
            "actual_rows": actual_rows,
            "pct_time": 0.0,
        })

    # Invariants computation
    root_node = nodes[0]
    root_actual_ms = root_node["actual_ms"]
    root_total_ms = root_node["actual_ms"] * root_node["loops"]
    duration_ms = root_actual_ms

    if root_total_ms > 0:
        for node in nodes:
            pct = (node["total_ms"] / root_total_ms) * 100.0
            node["pct_time"] = round(pct, 2)
        root_node["pct_time"] = 100.0
    else:
        root_node["pct_time"] = 100.0

    # rows_examined: sum of (actual_rows * loops) for table scan nodes
    rows_examined = 0
    for node in nodes:
        if node["operation"].startswith("Table scan on") or "Table scan" in node["operation"]:
            rows_examined += int(node["actual_rows"] * node["loops"])

    return nodes, duration_ms, rows_examined


def is_query_flagged(nodes: List[Dict[str, Any]]) -> bool:
    """
    Flag query if any plan node has operation starting with 'Table scan on' and pct_time > 50.
    Property 3: Full-Scan Flag Rule Completeness.
    """
    for node in nodes:
        op = node.get("operation", "")
        pct = node.get("pct_time", 0.0)
        if op.startswith("Table scan on") and pct > 50.0:
            return True
        # Also check without leading '-> ' or case variations
        if re.search(r"^Table scan on\b", op, re.IGNORECASE) and pct > 50.0:
            return True
    return False


def write_telemetry(
    conn_analyst,
    conn_telemetry,
    sql_text: str,
    started_at: str,
    db_user: str
) -> Tuple[List[Any], int, bool, float, Optional[str], Optional[str]]:
    """
    Execute EXPLAIN ANALYZE on conn_analyst, parse tree, execute query,
    write telemetry to QUERY_EXECUTION and QUERY_PLAN_NODE via conn_telemetry,
    and flag slow full table scans.
    Returns: (result_rows, qe_id, flagged, duration_ms, candidate_table, candidate_columns)
    """
    # 1. Run EXPLAIN ANALYZE on analyst connection
    explain_sql = f"EXPLAIN ANALYZE {sql_text}"
    cur_analyst = conn_analyst.cursor()
    try:
        cur_analyst.execute(explain_sql)
        explain_lines = [r[0] for r in cur_analyst.fetchall()]
    finally:
        cur_analyst.close()

    explain_str = "\n".join(explain_lines)
    nodes, duration_ms, rows_examined = parse_explain_tree(explain_str)

    # 2. Run query itself to collect results
    cur_run = conn_analyst.cursor()
    try:
        cur_run.execute(sql_text)
        result_rows = []
        if cur_run.description:
            result_rows = cur_run.fetchall()
        rows_sent = len(result_rows)
        # Release shared read metadata lock from read query
        conn_analyst.rollback()
    finally:
        cur_run.close()

    # 3. Insert QUERY_EXECUTION via sp_insert_qe
    qe_id = call_sp_out(
        conn_telemetry,
        "sp_insert_qe",
        [db_user, sql_text, started_at, duration_ms, rows_examined, rows_sent],
        1
    )
    qe_id = int(qe_id)

    # 4. Insert QUERY_PLAN_NODE rows via sp_insert_qpn
    for n in nodes:
        call_sp(
            conn_telemetry,
            "sp_insert_qpn",
            [
                qe_id,
                n["node_id"],
                n["parent_node_id"],
                n["operation"],
                n["table_name"],
                n["pct_time"],
                n["est_rows"],
                n["actual_rows"]
            ]
        )

    # 5. Check flagging rule
    flagged = is_query_flagged(nodes)
    candidate_table = None
    candidate_cols = None

    if flagged:
        call_sp(conn_telemetry, "sp_flag_query", [qe_id])
        # Find candidate table & columns for index recommendation
        for n in nodes:
            if n["operation"].startswith("Table scan on") or "Table scan" in n["operation"]:
                candidate_table = n.get("table_name") or "AZURE_VM"
                break
        if not candidate_table:
            candidate_table = "AZURE_VM"
        candidate_cols = "vm_category, vcpu_bucket, created_at"

    return result_rows, qe_id, flagged, duration_ms, candidate_table, candidate_cols


def run_index_recommendation(
    qe_id: int,
    table: str,
    columns_csv: str,
    sql_text: str,
    username: str,
    password: str
):
    """
    Background worker for index recommendation:
    1. Measures baseline execution time before index (median of 3 runs).
    2. Explicitly rolls back and closes the analyst baseline connection so no SHARED_READ
       metadata locks are held on the table.
    3. Calls sp_recommend_index on conn_telemetry (creates INVISIBLE index via DDL).
    4. Opens a fresh analyst connection, sets optimizer_switch='use_invisible_indexes=on',
       and measures execution time with invisible index (median of 3 runs).
    5. Explicitly rolls back and closes the second analyst connection.
    6. Calls sp_record_index_test with before/after timings on conn_telemetry.
    """
    conn_telemetry = get_connection(SVC_TELEMETRY_USER, SVC_TELEMETRY_PASSWORD, role="role_telemetry")

    try:
        # Step 1: Measure baseline before_ms on dedicated analyst connection
        conn_baseline = get_connection(username, password, role="business_analyst")
        try:
            cur = conn_baseline.cursor()
            cur.execute("SET SESSION optimizer_switch='use_invisible_indexes=off'")
            before_times = []
            for _ in range(3):
                t0 = time.perf_counter()
                cur.execute(sql_text)
                cur.fetchall()
                before_times.append((time.perf_counter() - t0) * 1000.0)
            before_ms = statistics.median(before_times)
            cur.close()
            # Explicit rollback before close to release transaction metadata lock
            conn_baseline.rollback()
        finally:
            conn_baseline.close()

        # Step 2: Call sp_recommend_index while no analyst connection holds table locks
        rec_id = call_sp_out(conn_telemetry, "sp_recommend_index", [qe_id, table, columns_csv], 1)
        if not rec_id:
            return

        rec_id = int(rec_id)

        # Step 3: Measure after_ms on fresh analyst connection with invisible indexes enabled
        conn_after = get_connection(username, password, role="business_analyst")
        try:
            cur = conn_after.cursor()
            cur.execute("SET SESSION optimizer_switch='use_invisible_indexes=on'")
            after_times = []
            for _ in range(3):
                t0 = time.perf_counter()
                cur.execute(sql_text)
                cur.fetchall()
                after_times.append((time.perf_counter() - t0) * 1000.0)
            after_ms = statistics.median(after_times)
            cur.close()
            # Explicit rollback to cleanly close read transaction
            conn_after.rollback()
        finally:
            conn_after.close()

        # Step 4: Record index test result in DB via telemetry service account
        call_sp(conn_telemetry, "sp_record_index_test", [rec_id, before_ms, after_ms])
    finally:
        conn_telemetry.close()
