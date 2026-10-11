import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from dotenv import load_dotenv

from backend.auth import require_role
from backend.db import call_sp, get_connection
from backend.models.requests import PartitionRequest
from backend.models.responses import (
    CapacityForecastResponse,
    ConflictLogResponse,
    ForecastDatabaseResponse,
    IndexRecResponse,
    SlowQueryPlanNodeResponse,
    SlowQueryResponse,
)

load_dotenv()

router = APIRouter(prefix="", tags=["engineer"])


@router.get("/workload")
def get_workload(user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
                DIGEST_TEXT,
                COUNT_STAR,
                SUM_TIMER_WAIT / 1000000000.0 AS sum_timer_ms,
                AVG_TIMER_WAIT / 1000000000.0 AS avg_timer_ms,
                SUM_ROWS_EXAMINED,
                SUM_ROWS_SENT
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT IS NOT NULL
            ORDER BY COUNT_STAR DESC
            LIMIT 20
        """)
        cols = [col[0].lower() for col in cur.description]
        return [dict(zip(cols, list(r))) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.get("/slow-queries", response_model=List[SlowQueryResponse])
def get_slow_queries(user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
                qe_id,
                db_user,
                digest_text,
                DATE_FORMAT(started_at, '%Y-%m-%d %H:%i:%s.%f') AS started_at,
                duration_ms,
                rows_examined,
                rows_sent,
                flagged
            FROM QUERY_EXECUTION
            WHERE flagged = 1
            ORDER BY started_at DESC
        """)
        cols = [col[0].lower() for col in cur.description]
        rows = [dict(zip(cols, list(r))) for r in cur.fetchall()]
        return [
            SlowQueryResponse(
                qe_id=r["qe_id"],
                db_user=r["db_user"],
                digest_text=r["digest_text"],
                started_at=str(r["started_at"]),
                duration_ms=float(r["duration_ms"]),
                rows_examined=int(r["rows_examined"]),
                rows_sent=int(r["rows_sent"]),
                flagged=int(r["flagged"])
            )
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()


@router.get("/slow-queries/{qe_id}/plan", response_model=List[SlowQueryPlanNodeResponse])
def get_slow_query_plan(qe_id: int, user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
                node_id,
                parent_node_id,
                operation,
                table_name,
                pct_time,
                est_rows,
                actual_rows
            FROM QUERY_PLAN_NODE
            WHERE qe_id = ?
            ORDER BY node_id
        """, (qe_id,))
        cols = [col[0].lower() for col in cur.description]
        rows = [dict(zip(cols, list(r))) for r in cur.fetchall()]
        return [
            SlowQueryPlanNodeResponse(
                node_id=r["node_id"],
                parent_node_id=r["parent_node_id"],
                operation=r["operation"],
                table_name=r["table_name"],
                pct_time=float(r["pct_time"]),
                est_rows=r["est_rows"],
                actual_rows=r["actual_rows"]
            )
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()


@router.get("/index-recs", response_model=List[IndexRecResponse])
def get_index_recommendations(user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
                ir.rec_id,
                ir.qe_id,
                ir.table_name,
                ir.index_name,
                ir.columns_csv,
                ir.status,
                ir.before_ms,
                ir.after_ms,
                DATE_FORMAT(qe.started_at, '%Y-%m-%d %H:%i:%s') AS created_at
            FROM INDEX_RECOMMENDATION ir
            LEFT JOIN QUERY_EXECUTION qe ON ir.qe_id = qe.qe_id
            WHERE ir.status IN ('proposed', 'testing')
            ORDER BY ir.rec_id DESC
        """)
        cols = [col[0].lower() for col in cur.description]
        rows = [dict(zip(cols, list(r))) for r in cur.fetchall()]
        return [
            IndexRecResponse(
                rec_id=r["rec_id"],
                qe_id=r["qe_id"],
                table_name=r["table_name"],
                index_name=r["index_name"],
                columns_csv=r["columns_csv"],
                status=r["status"],
                before_ms=float(r["before_ms"]) if r["before_ms"] is not None else None,
                after_ms=float(r["after_ms"]) if r["after_ms"] is not None else None,
                created_at=str(r["created_at"]) if r["created_at"] is not None else None
            )
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()


@router.post("/index-recs/{rec_id}/approve")
def approve_index_recommendation(rec_id: int, user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    try:
        rows = call_sp(conn, "sp_apply_index", [rec_id])
        result_msg = rows[0].get("result") if rows else "applied"
        if result_msg == "Index test not yet complete":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Index test not yet complete"
            )
        return {"status": "success", "result": result_msg}
    finally:
        conn.close()


@router.post("/index-recs/{rec_id}/reject")
def reject_index_recommendation(rec_id: int, user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    try:
        rows = call_sp(conn, "sp_reject_index", [rec_id])
        result_msg = rows[0].get("result") if rows else "rejected"
        return {"status": "success", "result": result_msg}
    finally:
        conn.close()


@router.get("/partitions")
def get_partitions(user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM v_partitions")
        cols = [col[0].lower() for col in cur.description]
        return [dict(zip(cols, list(r))) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.post("/partitions")
def add_partition(req: PartitionRequest, user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    try:
        call_sp(conn, "sp_add_partition", [req.table_name, req.boundary])
        return {"status": "success", "table_name": req.table_name, "boundary": req.boundary}
    finally:
        conn.close()


@router.get("/capacity/forecast", response_model=CapacityForecastResponse)
def get_capacity_forecast(user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT forecast_id, line_no, metric_name, metric_value, error_indicator
            FROM v_forecast_database
            ORDER BY forecast_id DESC, line_no ASC
        """)
        cols = [col[0].lower() for col in cur.description]
        rows = [dict(zip(cols, list(r))) for r in cur.fetchall()]

        days_to_90pct = None
        at_risk_queries: List[Dict[str, Any]] = []
        projected_rows = None
        for r in rows:
            m_name = r.get("metric_name")
            m_val = r.get("metric_value")
            if m_name in ("days_to_90", "days_to_90_pct") and m_val is not None:
                days_to_90pct = float(m_val)
            elif m_name == "projected_rows" and m_val is not None:
                projected_rows = float(m_val)
            elif m_name and m_name.startswith("at_risk_query_"):
                at_risk_queries.append(r)

        at_risk_factor = 1.25
        try:
            cur.execute("SELECT config_value FROM CONFIG WHERE config_key = 'AT_RISK_FACTOR'")
            cfg_row = cur.fetchone()
            if cfg_row and cfg_row[0]:
                at_risk_factor = float(cfg_row[0])
        except Exception:
            pass

        current_rows = 2695548
        if projected_rows and projected_rows > current_rows:
            ratio = projected_rows / float(current_rows)
            cur.execute("""
                SELECT qe_id, digest_text, duration_ms
                FROM QUERY_EXECUTION
                ORDER BY qe_id DESC
                LIMIT 20
            """)
            for qe_id_val, digest, b_ms in cur.fetchall():
                baseline_ms = float(b_ms)
                projected_ms = round(baseline_ms * ratio, 3)
                if projected_ms >= at_risk_factor * baseline_ms:
                    at_risk_queries.append({
                        "qe_id": qe_id_val,
                        "digest_text": digest,
                        "baseline_ms": baseline_ms,
                        "projected_ms": projected_ms,
                        "at_risk": True
                    })

        return CapacityForecastResponse(
            days_to_90pct=days_to_90pct,
            at_risk_queries=at_risk_queries,
            lines=rows
        )
    finally:
        cur.close()
        conn.close()


@router.post("/forecast/database/{event_id}", response_model=ForecastDatabaseResponse)
def run_forecast_database(
    event_id: int,
    as_of_date: Optional[str] = Query(None),
    user: tuple = Depends(require_role(["db_engineer"]))
):
    username, password, role = user
    conn = get_connection(username, password, role)
    try:
        cur = conn.cursor()
        if as_of_date:
            cur.execute("SET @as_of_date = ?", (as_of_date,))
        else:
            cur.execute("SET @as_of_date = NULL")
        cur.close()

        lines = call_sp(conn, "sp_forecast_database", [event_id])
        forecast_id = lines[0].get("forecast_id") if lines else None
        return ForecastDatabaseResponse(forecast_id=forecast_id, lines=lines)
    finally:
        conn.close()


@router.get("/conflicts", response_model=List[ConflictLogResponse])
def get_conflicts(user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
                conflict_id,
                rule_id,
                attempted_value,
                stale_version,
                current_version,
                attempted_by,
                DATE_FORMAT(at, '%Y-%m-%d %H:%i:%s.%f') AS at
            FROM CONFLICT_LOG
            ORDER BY at DESC
            LIMIT 100
        """)
        cols = [col[0].lower() for col in cur.description]
        rows = [dict(zip(cols, list(r))) for r in cur.fetchall()]
        return [
            ConflictLogResponse(
                conflict_id=r["conflict_id"],
                rule_id=r["rule_id"],
                attempted_value=float(r["attempted_value"]),
                stale_version=r["stale_version"],
                current_version=r["current_version"],
                attempted_by=r["attempted_by"],
                at=str(r["at"])
            )
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()


@router.get("/engineer/events")
def get_engineer_events(user: tuple = Depends(require_role(["db_engineer"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM v_events_min")
        cols = [col[0].lower() for col in cur.description]
        return [dict(zip(cols, list(r))) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()
