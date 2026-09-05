from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime


# Pydantic Schemas for API
class SQLQueryRequest(BaseModel):
    query: str = Field(..., description="SQL SELECT query to execute against the database")


class SQLQueryResponse(BaseModel):
    success: bool
    columns: List[str] = []
    rows: List[Dict[str, Any]] = []
    row_count: int = 0
    execution_time_ms: float = 0.0
    error: Optional[str] = None


class EngineControlRequest(BaseModel):
    action: str = Field(..., description="'pause', 'resume', 'reset', or 'set_threshold'")
    value: Optional[float] = None


class PortfolioResponse(BaseModel):
    initial_capital: float
    cash: float
    locked_capital: float
    total_equity: float
    total_pnl: float
    return_pct: float
    win_rate: float
    total_trades: int
    open_positions_count: int
    is_running: bool
    spread_threshold: float


class OpportunityResponse(BaseModel):
    id: Optional[int] = None
    event_id: str
    event_title: str
    outcomes_count: int
    basket_sum: float
    gross_spread: float
    net_spread: float
    actionable: bool
    created_at: str
