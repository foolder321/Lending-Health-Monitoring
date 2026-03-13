"""
Type definitions used across the monitoring application.

These Pydantic models provide a structured representation of the data
produced by protocol adapters and consumed by the risk engine and
alerting services. Using Pydantic ensures that values are validated
and parsed consistently throughout the codebase.
"""

from __future__ import annotations

from typing import List, Optional, Literal

from pydantic import BaseModel, Field


class AssetPosition(BaseModel):
    """Represents a single supplied or borrowed asset within a position."""

    token_address: str = Field(..., description="Underlying ERC20 token address")
    token_symbol: Optional[str] = Field(
        None, description="Human‑readable token symbol, if available"
    )
    amount: float = Field(
        ..., description="Quantity of the asset held or owed, expressed in token units"
    )
    usd_value: float = Field(
        ..., description="USD value of the position for this asset (amount × price)"
    )
    position_type: Literal["supply", "borrow"] = Field(
        ..., description="Whether this asset is supplied (collateral) or borrowed"
    )


class Position(BaseModel):
    wallet_address: str
    protocol: str
    network: str
    supplied: List[AssetPosition]
    borrowed: List[AssetPosition]
    collateral_value_usd: Optional[float]
    debt_value_usd: Optional[float]
    health_factor: Optional[float]
    ltv: Optional[float]
    collateral_ratio: Optional[float]

    liquidation_distance_pct: Optional[float] = None
    estimated_liquidation_price: Optional[float] = None
    risk_status: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True
