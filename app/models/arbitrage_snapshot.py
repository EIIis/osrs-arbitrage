from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base import Base


class ArbitrageSnapshot(Base):
    """
    Represents a precomputed arbitrage calculation for an item at a specific point in time.

    These are computed during each polling cycle and stored for fast retrieval.
    Even negative margins are stored for historical analysis.
    """
    __tablename__ = "arbitrage_snapshots"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # Foreign key to tracked_items (references item_id directly)
    item_id = Column(Integer, ForeignKey("tracked_items.item_id", ondelete="CASCADE"), nullable=False, index=True)

    # Time window ('1m' or '5m')
    window = Column(String(2), nullable=False)

    # Price data used in calculation (stored for reference)
    avg_high_price = Column(Integer, nullable=False)
    avg_low_price = Column(Integer, nullable=False)

    # Arbitrage calculation results
    spread = Column(Integer, nullable=False)  # avgHigh - avgLow
    tax = Column(Integer, nullable=False)  # 0.02 * avgHigh
    margin_after_tax = Column(Integer, nullable=False)  # spread - tax
    percent_margin = Column(Float, nullable=False, index=True)  # (margin / avgLow) * 100

    # Volume data (optional, stored for reference)
    high_volume = Column(Integer, nullable=True)
    low_volume = Column(Integer, nullable=True)

    # Timestamp when this arbitrage was computed
    computed_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    tracked_item = relationship("TrackedItem", back_populates="arbitrage_snapshots")

    def __repr__(self):
        return f"<ArbitrageSnapshot(id={self.id}, item_id={self.item_id}, window='{self.window}', percent_margin={self.percent_margin:.2f}%)>"
