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
    """Aggregated lending position for a single wallet address."""

    wallet_address: str = Field(
        ..., description="The wallet address whose position is being reported"
    )
    protocol: str = Field(
        ..., description="The name of the lending protocol, e.g. 'Aave V3'"
    )
    network: str = Field(
        ..., description="The blockchain network, e.g. 'Arbitrum'"
    )
    supplied: List[AssetPosition] = Field(
        default_factory=list,
        description="List of assets supplied as collateral and their values",
    )
    borrowed: List[AssetPosition] = Field(
        default_factory=list,
        description="List of assets borrowed and their values",
    )
    collateral_value_usd: float = Field(
        0.0, description="Total USD value of all supplied assets"
    )
    debt_value_usd: float = Field(
        0.0, description="Total USD value of all borrowed assets"
    )
    health_factor: Optional[float] = Field(
        None,
        description="User's health factor as reported by the protocol; may be null if no debt",
    )
    ltv: Optional[float] = Field(
        None,
        description="Loan‑to‑value ratio (debt / collateral). None if collateral is zero.",
    )
    collateral_ratio: Optional[float] = Field(
        None,
        description="Collateral ratio (collateral / debt). None if debt is zero.",
    )

    class Config:
        arbitrary_types_allowed = True