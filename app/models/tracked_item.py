from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base import Base


class TrackedItem(Base):
    """
    Represents an OSRS item that we're tracking for arbitrage opportunities.

    Items are added via the API and will be polled every 5 minutes for price updates.
    """
    __tablename__ = "tracked_items"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # OSRS item ID (unique identifier from the game)
    item_id = Column(Integer, unique=True, nullable=False, index=True)

    # Human-readable item name
    item_name = Column(String(255), nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    price_snapshots = relationship("PriceSnapshot", back_populates="tracked_item", cascade="all, delete-orphan")
    arbitrage_snapshots = relationship("ArbitrageSnapshot", back_populates="tracked_item", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<TrackedItem(id={self.id}, item_id={self.item_id}, item_name='{self.item_name}')>"
