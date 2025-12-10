from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class ArbitrageSnapshotBase(BaseModel):
    """Base schema for arbitrage snapshots."""
    item_id: int
    window: str = Field(..., description="Time window ('1m' or '5m')")
    avg_high_price: int = Field(..., description="Average high price used in calculation")
    avg_low_price: int = Field(..., description="Average low price used in calculation")
    spread: int = Field(..., description="Price spread (high - low)")
    tax: int = Field(..., description="Grand Exchange tax (2% of high price)")
    margin_after_tax: int = Field(..., description="Profit margin after tax")
    percent_margin: float = Field(..., description="Profit margin as percentage")
    high_volume: Optional[int] = Field(None, description="Trading volume at high price")
    low_volume: Optional[int] = Field(None, description="Trading volume at low price")


class ArbitrageSnapshotResponse(ArbitrageSnapshotBase):
    """Schema for arbitrage snapshot responses."""
    id: int
    computed_at: datetime

    class Config:
        from_attributes = True


class ArbitrageOpportunityResponse(ArbitrageSnapshotResponse):
    """
    Schema for arbitrage opportunities with item details.

    Technical Explanation:
    Extends ArbitrageSnapshotResponse to include the item name for display purposes.

    Simple Explanation:
    This is the data you get when viewing arbitrage opportunities - it includes
    all the profit calculations plus the item's name so you know what to trade.
    """
    item_name: str = Field(..., description="Name of the item")


class ArbitrageTopResponse(BaseModel):
    """Schema for top arbitrage opportunities endpoint."""
    opportunities: list[ArbitrageOpportunityResponse]
    count: int
    window: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class ArbitrageHistoryResponse(BaseModel):
    """Schema for arbitrage history for a specific item."""
    item_id: int
    item_name: str
    window: str
    snapshots: list[ArbitrageSnapshotResponse]
    total: int
