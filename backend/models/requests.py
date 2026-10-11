from typing import Any, List, Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class SqlRequest(BaseModel):
    sql: str


class PricingUpdateRequest(BaseModel):
    new_value: float
    expected_version: int


class OrderRequest(BaseModel):
    order_date: str
    device_id: int
    region_id: int
    channel_id: int
    items: List[Any] = Field(default_factory=list)


class PartitionRequest(BaseModel):
    table_name: str
    boundary: str
