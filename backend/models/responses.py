from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LoginResponse(BaseModel):
    token: str
    role: str
    username: str


class SqlResponse(BaseModel):
    qe_id: Optional[int] = None
    flagged: bool = False
    rows: List[Any] = Field(default_factory=list)
    duration_ms: float = 0.0


class PricingUpdateResponse(BaseModel):
    status: str
    rule_id: int
    version: Optional[int] = None


class MarginDecompositionItem(BaseModel):
    component: str
    value_delta: float


class MarginDecompositionResponse(BaseModel):
    items: List[MarginDecompositionItem] = Field(default_factory=list)


class ForecastBusinessResponse(BaseModel):
    forecast_id: Optional[int] = None
    lines: List[Dict[str, Any]] = Field(default_factory=list)


class ForecastDatabaseResponse(BaseModel):
    forecast_id: Optional[int] = None
    lines: List[Dict[str, Any]] = Field(default_factory=list)


class SlowQueryResponse(BaseModel):
    qe_id: int
    db_user: str
    digest_text: str
    started_at: str
    duration_ms: float
    rows_examined: int
    rows_sent: int
    flagged: int


class SlowQueryPlanNodeResponse(BaseModel):
    node_id: int
    parent_node_id: Optional[int] = None
    operation: str
    table_name: Optional[str] = None
    pct_time: float
    est_rows: Optional[int] = None
    actual_rows: Optional[int] = None


class IndexRecResponse(BaseModel):
    rec_id: int
    qe_id: Optional[int] = None
    table_name: str
    index_name: str
    columns_csv: str
    status: str
    before_ms: Optional[float] = None
    after_ms: Optional[float] = None
    created_at: Optional[str] = None


class ConflictLogResponse(BaseModel):
    conflict_id: int
    rule_id: int
    attempted_value: float
    stale_version: int
    current_version: int
    attempted_by: str
    at: str


class CapacityForecastResponse(BaseModel):
    days_to_90pct: Optional[float] = None
    at_risk_queries: List[Dict[str, Any]] = Field(default_factory=list)
    lines: List[Dict[str, Any]] = Field(default_factory=list)
