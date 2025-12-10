from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base import Base


class PriceSnapshot(Base):
    """
    Represents a point-in-time capture of an item's Grand Exchange prices.

    Snapshots are created every 5 minutes for both 1m and 5m time windows.
    Price values can be null if the RuneLite API returns null data.
    """
    __tablename__ = "price_snapshots"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # Foreign key to tracked_items (references item_id directly)
    item_id = Column(Integer, ForeignKey("tracked_items.item_id", ondelete="CASCADE"), nullable=False, index=True)

    # Time window ('1m' or '5m')
    window = Column(String(2), nullable=False)

    # Price data (in GP - gold pieces)
    # Can be null if API returns null
    avg_high_price = Column(Integer, nullable=True)
    avg_low_price = Column(Integer, nullable=True)
    high_price_volume = Column(Integer, nullable=True)
    low_price_volume = Column(Integer, nullable=True)

    # Timestamp when this snapshot was fetched
    fetched_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    tracked_item = relationship("TrackedItem", back_populates="price_snapshots")

    def __repr__(self):
        return f"<PriceSnapshot(id={self.id}, item_id={self.item_id}, window='{self.window}', fetched_at={self.fetched_at})>"
