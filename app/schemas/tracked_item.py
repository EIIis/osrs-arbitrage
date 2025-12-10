from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class TrackedItemBase(BaseModel):
    """Base schema for tracked items with common fields."""
    item_id: int = Field(..., description="OSRS item ID", example=453)
    item_name: str = Field(..., description="Item name", example="Coal")


class TrackedItemCreate(TrackedItemBase):
    """Schema for creating a new tracked item."""
    pass


class TrackedItemResponse(TrackedItemBase):
    """Schema for tracked item responses."""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TrackedItemListResponse(BaseModel):
    """Schema for paginated list of tracked items."""
    items: list[TrackedItemResponse]
    total: int
