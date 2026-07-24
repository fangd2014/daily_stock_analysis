"""API schemas for the quantitative strategy workspace."""

from pydantic import BaseModel, Field


class QuantRunRequest(BaseModel):
    """Request to execute one registered strategy."""

    strategy_id: str = Field(..., min_length=1, max_length=64)
