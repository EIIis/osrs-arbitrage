from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class PriceSnapshotBase(BaseModel):
    """Base schema for price snapshots."""
    item_id: int
    window: str = Field(..., description="Time window ('1m' or '5m')", example="5m")
    avg_high_price: Optional[int] = Field(None, description="Average high price in GP")
    avg_low_price: Optional[int] = Field(None, description="Average low price in GP")
    high_price_volume: Optional[int] = Field(None, description="High price volume")
    low_price_volume: Optional[int] = Field(None, description="Low price volume")


class PriceSnapshotCreate(PriceSnapshotBase):
    """Schema for creating a price snapshot."""
    pass


class PriceSnapshotResponse(PriceSnapshotBase):
    """Schema for price snapshot responses."""
    id: int
    fetched_at: datetime

    class Config:
        from_attributes = True


class PriceHistoryResponse(BaseModel):
    """Schema for item price history."""
    item_id: int
    item_name: str
    window: str
    snapshots: list[PriceSnapshotResponse]
    total: int
