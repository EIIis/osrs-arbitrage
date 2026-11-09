"""SQLite schema. Money averages are stored as hundredths of a GP."""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), index=True)
    members: Mapped[bool] = mapped_column(Boolean, default=False)
    buy_limit: Mapped[int | None]
    watched: Mapped[bool] = mapped_column(default=False)
    favorite: Mapped[bool] = mapped_column(default=False)
    excluded: Mapped[bool] = mapped_column(default=False)


class Preferences(Base):
    __tablename__ = "preferences"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    budget: Mapped[int] = mapped_column(default=0)
    profile: Mapped[str] = mapped_column(default="balanced")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (CheckConstraint("quantity > 0 AND buy_price > 0"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    quantity: Mapped[int]
    buy_price: Mapped[int]
    bought_at: Mapped[int]
    mode: Mapped[str]
    target_price: Mapped[int | None]
    target_reason: Mapped[str] = mapped_column(Text, default="No supported target available.")
    target_revision: Mapped[int] = mapped_column(default=1)
    loss_percent: Mapped[str] = mapped_column(default="5")
    snoozed_until: Mapped[int] = mapped_column(default=0)
    voided_at: Mapped[int | None]


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (CheckConstraint("quantity > 0 AND price > 0"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    quantity: Mapped[int]
    price: Mapped[int]
    sold_at: Mapped[int]
    voided_at: Mapped[int | None]


class Mutation(Base):
    __tablename__ = "mutations"
    interaction_id: Mapped[str] = mapped_column(primary_key=True)
    result: Mapped[str] = mapped_column(Text)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[int]
    action: Mapped[str]
    detail: Mapped[str] = mapped_column(Text)


class MarketQuote(Base):
    __tablename__ = "quotes"
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    high: Mapped[int | None]
    low: Mapped[int | None]
    high_time: Mapped[int | None]
    low_time: Mapped[int | None]
    observed_at: Mapped[int]


class PriceBucket(Base):
    __tablename__ = "price_buckets"
    __table_args__ = (
        UniqueConstraint("item_id", "interval", "start"),
        Index("ix_bucket_item_start", "item_id", "start"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    interval: Mapped[int]
    start: Mapped[int]
    high_cents: Mapped[int | None]
    low_cents: Mapped[int | None]
    high_volume: Mapped[int]
    low_volume: Mapped[int]
    observed_at: Mapped[int]


class CachedAggregate(Base):
    __tablename__ = "aggregates"
    __table_args__ = (UniqueConstraint("item_id", "interval"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    interval: Mapped[int]
    start: Mapped[int]
    high_cents: Mapped[int | None]
    low_cents: Mapped[int | None]
    high_volume: Mapped[int]
    low_volume: Mapped[int]
    observed_at: Mapped[int]


class HistorySync(Base):
    __tablename__ = "history_sync"
    __table_args__ = (UniqueConstraint("item_id", "mode"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    mode: Mapped[str]
    fetched_at: Mapped[int]
    start: Mapped[int]
    end: Mapped[int]
    interval: Mapped[int]


class AlertState(Base):
    __tablename__ = "alert_state"
    __table_args__ = (UniqueConstraint("position_id", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    kind: Mapped[str]
    revision: Mapped[int] = mapped_column(default=0)
    last_bucket: Mapped[int] = mapped_column(default=0)
    streak: Mapped[int] = mapped_column(default=0)
    last_sent: Mapped[int] = mapped_column(default=0)
    notified_revision: Mapped[int] = mapped_column(default=0)


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(unique=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    revision: Mapped[int]
    kind: Mapped[str]
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[int]
    message_id: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
