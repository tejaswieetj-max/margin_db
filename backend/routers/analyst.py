import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from dotenv import load_dotenv

from backend.auth import require_role
from backend.db import call_sp, call_sp_out, get_connection
from backend.models.requests import PricingUpdateRequest, SqlRequest
from backend.models.responses import (
    ForecastBusinessResponse,
    MarginDecompositionItem,
    MarginDecompositionResponse,
    PricingUpdateResponse,
    SqlResponse,
)
from backend.sql_validator import validate_sql_console_query
from backend.telemetry import run_index_recommendation, write_telemetry

load_dotenv()

SVC_TELEMETRY_USER = os.environ.get("SVC_TELEMETRY_USER", "svc_telemetry")
SVC_TELEMETRY_PASSWORD = os.environ.get("SVC_TELEMETRY_PASSWORD", "teluser")

router = APIRouter(prefix="", tags=["analyst"])


@router.get("/events")
def get_events(user: tuple = Depends(require_role(["business_analyst"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM v_events")
        cols = [col[0] for col in cur.description]
        rows = [dict(zip(cols, list(r))) for r in cur.fetchall()]
        return rows
    finally:
        cur.close()
        conn.close()


@router.get("/margin", response_model=MarginDecompositionResponse)
def get_margin_decomposition(
    segment: str = Query(...),
    period_a: str = Query(...),
    period_b: str = Query(...),
    group_by: Optional[str] = Query(None),
    user: tuple = Depends(require_role(["business_analyst"]))
):
    username, password, role = user
    conn = get_connection(username, password, role)
    try:
        cur = conn.cursor()
        if group_by:
            cur.execute("SET @group_by = ?", (group_by,))
        else:
            cur.execute("SET @group_by = NULL")
        cur.close()

        rows = call_sp(conn, "sp_margin_decomposition", [segment, period_a, period_b])
        items = [
            MarginDecompositionItem(
                component=r["component"],
                value_delta=float(r["value_delta"])
            )
            for r in rows
            if "component" in r and "value_delta" in r
        ]
        return MarginDecompositionResponse(items=items)
    finally:
        conn.close()


@router.get("/forecast/business/{event_id}", response_model=ForecastBusinessResponse)
def get_forecast_business(
    event_id: int,
    as_of_date: Optional[str] = Query(None),
    user: tuple = Depends(require_role(["business_analyst"]))
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

        lines = call_sp(conn, "sp_forecast_business", [event_id])
        forecast_id = lines[0].get("forecast_id") if lines else None
        return ForecastBusinessResponse(forecast_id=forecast_id, lines=lines)
    finally:
        conn.close()


@router.post("/sql", response_model=SqlResponse)
def execute_sql_console(
    req: SqlRequest,
    background_tasks: BackgroundTasks,
    user: tuple = Depends(require_role(["business_analyst"]))
):
    username, password, role = user

    # 1. SQL Console validation
    if not validate_sql_console_query(req.sql):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SQL query rejected: query must be a single read-only SELECT or WITH statement"
        )

    # 2. Connections
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    conn_analyst = get_connection(username, password, role)
    conn_telemetry = get_connection(SVC_TELEMETRY_USER, SVC_TELEMETRY_PASSWORD, role="role_telemetry")

    try:
        result_rows, qe_id, flagged, duration_ms, cand_table, cand_cols = write_telemetry(
            conn_analyst, conn_telemetry, req.sql, started_at, username
        )

        # 3. If flagged, dispatch background recommendation worker
        if flagged and cand_table and cand_cols:
            background_tasks.add_task(
                run_index_recommendation,
                qe_id,
                cand_table,
                cand_cols,
                req.sql,
                username,
                password
            )

        # Format rows for JSON serialization
        formatted_rows = [list(r) for r in result_rows]

        return SqlResponse(
            qe_id=qe_id,
            flagged=flagged,
            rows=formatted_rows,
            duration_ms=round(duration_ms, 3)
        )
    finally:
        conn_analyst.close()
        conn_telemetry.close()


@router.put("/pricing/{rule_id}", response_model=PricingUpdateResponse)
def update_pricing_rule(
    rule_id: int,
    req: PricingUpdateRequest,
    user: tuple = Depends(require_role(["business_analyst"]))
):
    username, password, role = user
    conn = get_connection(username, password, role)
    try:
        p_status = call_sp_out(
            conn,
            "sp_update_pricing_rule",
            [rule_id, req.new_value, req.expected_version],
            1
        )
        if p_status == "OK":
            return PricingUpdateResponse(
                status="OK",
                rule_id=rule_id,
                version=req.expected_version + 1
            )
        elif p_status == "CONFLICT":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Optimistic lock conflict on rule_id {rule_id}"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unexpected procedure status: {p_status}"
            )
    finally:
        conn.close()


@router.get("/my-queries")
def get_my_queries(user: tuple = Depends(require_role(["business_analyst"]))):
    username, password, role = user
    conn = get_connection(username, password, role)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM v_my_queries ORDER BY started_at DESC")
        cols = [col[0] for col in cur.description]
        rows = [dict(zip(cols, list(r))) for r in cur.fetchall()]
        return rows
    finally:
        cur.close()
        conn.close()
