"""Atomic owner ledger. Read models are detached; no Discord dependency."""

import json
import time
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.core.exceptions import UserError
from app.db.session import Database
from app.domain.calculations import MAX_INTEGER, profit, target_for_roi
from app.domain.types import Mode, Profile
from app.models import AlertDelivery, AlertState, Audit, Item, Mutation, Position, Preferences, Sale


@dataclass
class Holding:
    position: Position
    item: Item
    sales: list[Sale]

    @property
    def remaining(self) -> int:
        return self.position.quantity - sum(s.quantity for s in self.sales if s.voided_at is None)

    @property
    def realized(self) -> int:
        return sum(
            profit(self.position.buy_price, s.price, s.quantity, self.item.id)
            for s in self.sales
            if s.voided_at is None
        )


class Ledger:
    def __init__(self, db: Database, default_budget: int = 0):
        self.db, self.default_budget = db, default_budget

    async def mutate(self, interaction: str, action: str, operation, now: int | None = None):
        now = int(time.time()) if now is None else now
        async with self.db.write_lock, self.db.sessions() as session, session.begin():
            previous = await session.get(Mutation, str(interaction))
            if previous:
                return json.loads(previous.result)
            result = await operation(session)
            session.add(Mutation(interaction_id=str(interaction), result=json.dumps(result)))
            session.add(Audit(occurred_at=now, action=action, detail=json.dumps(result)))
            return result

    @staticmethod
    def validate_trade(quantity: int, price: int):
        if not 0 < quantity <= MAX_INTEGER or not 0 < price <= MAX_INTEGER:
            raise UserError("Quantity and unit price must be positive 64-bit whole numbers.")
        if quantity * price > MAX_INTEGER:
            raise UserError("The trade total exceeds supported 64-bit storage limits.")

    async def buy(
        self,
        interaction: str,
        item_id: int,
        quantity: int,
        price: int,
        mode: Mode,
        now: int,
        bought_at: int | None = None,
        target: int | None = None,
        reason: str = "No supported target available.",
    ):
        self.validate_trade(quantity, price)
        bought_at = now if bought_at is None else bought_at
        if bought_at > now:
            raise UserError("Purchase time cannot be in the future.")
        if target is not None and not 0 < target <= MAX_INTEGER:
            raise UserError("Target price must be a positive whole GP amount.")

        async def operation(session):
            if await session.get(Item, item_id) is None:
                raise UserError("Choose an item from the catalog autocomplete.")
            position = Position(
                item_id=item_id,
                quantity=quantity,
                buy_price=price,
                mode=Mode(mode).value,
                bought_at=bought_at,
                target_price=target,
                target_reason=reason,
            )
            session.add(position)
            await session.flush()
            return {"position_id": position.id}

        return await self.mutate(interaction, "buy", operation, now)

    async def _holding(self, session, position_id: int, include_void=False) -> Holding:
        position = await session.get(Position, position_id)
        if position is None or (position.voided_at is not None and not include_void):
            raise UserError("Position not found or voided.")
        item = await session.get(Item, position.item_id)
        sales = list(
            (
                await session.scalars(
                    select(Sale).where(Sale.position_id == position_id).order_by(Sale.sold_at, Sale.id)
                )
            ).all()
        )
        return Holding(position, item, sales)

    async def holding(self, position_id: int) -> Holding:
        async with self.db.sessions() as session:
            return await self._holding(session, position_id)

    async def holdings(self, include_closed=False) -> list[Holding]:
        async with self.db.sessions() as session:
            positions = list(
                (
                    await session.scalars(
                        select(Position).where(Position.voided_at.is_(None)).order_by(Position.id)
                    )
                ).all()
            )
            items = {i.id: i for i in (await session.scalars(select(Item))).all()}
            sales = list((await session.scalars(select(Sale).order_by(Sale.sold_at, Sale.id))).all())
            by_position: dict[int, list[Sale]] = {}
            for sale in sales:
                by_position.setdefault(sale.position_id, []).append(sale)
            result = [Holding(p, items[p.item_id], by_position.get(p.id, [])) for p in positions]
            return result if include_closed else [h for h in result if h.remaining > 0]

    async def sell(
        self,
        interaction: str,
        position_id: int,
        quantity: int,
        price: int,
        now: int,
        sold_at: int | None = None,
    ):
        self.validate_trade(quantity, price)
        sold_at = now if sold_at is None else sold_at
        if sold_at > now:
            raise UserError("Sale time cannot be in the future.")

        async def operation(session):
            holding = await self._holding(session, position_id)
            if quantity > holding.remaining:
                raise UserError(f"Only {holding.remaining:,} units remain in this position.")
            if sold_at < holding.position.bought_at:
                raise UserError("A sale cannot predate its purchase.")
            sale = Sale(position_id=position_id, quantity=quantity, price=price, sold_at=sold_at)
            session.add(sale)
            await session.flush()
            if quantity == holding.remaining:
                await self._cancel(session, position_id)
            return {
                "sale_id": sale.id,
                "profit": profit(holding.position.buy_price, price, quantity, holding.item.id),
            }

        return await self.mutate(interaction, "sell", operation, now)

    async def _cancel(self, session, position_id: int):
        for delivery in (
            await session.scalars(
                select(AlertDelivery).where(
                    AlertDelivery.position_id == position_id, AlertDelivery.status.in_(["pending", "sending"])
                )
            )
        ).all():
            delivery.status = "cancelled"

    async def target(
        self,
        interaction: str,
        position_id: int,
        now: int,
        price: int | None = None,
        percent: Decimal | None = None,
        reset: bool = False,
        reason: str | None = None,
    ):
        async def operation(session):
            holding = await self._holding(session, position_id)
            p = holding.position
            if holding.remaining <= 0:
                raise UserError("This position is closed.")
            if percent is not None:
                new_price = target_for_roi(p.buy_price, percent, p.item_id)
            else:
                new_price = price
            if not reset and (price is None) == (percent is None):
                raise UserError("Provide exactly one of price or net percent.")
            if new_price is not None and not 0 < new_price <= MAX_INTEGER:
                raise UserError("Target must be a positive whole GP amount.")
            p.target_price = new_price
            p.target_reason = reason or "Owner-specified target."
            p.target_revision += 1
            await self._cancel(session, p.id)
            return {"position_id": p.id, "target": p.target_price, "revision": p.target_revision}

        return await self.mutate(interaction, "target", operation, now)

    async def risk(self, interaction: str, position_id: int, percent: Decimal, now: int):
        if not percent.is_finite() or not 0 < percent <= 100:
            raise UserError("Loss threshold must be greater than zero and at most 100%.")

        async def operation(session):
            holding = await self._holding(session, position_id)
            holding.position.loss_percent = str(percent)
            await self._cancel(session, position_id)
            for state in (
                await session.scalars(
                    select(AlertState).where(
                        AlertState.position_id == position_id, AlertState.kind == "warning"
                    )
                )
            ).all():
                state.streak = 0
                state.last_bucket = 0
            return {"position_id": position_id, "loss_percent": str(percent)}

        return await self.mutate(interaction, "risk", operation, now)

    async def snooze(self, interaction: str, position_id: int, minutes: int, now: int):
        if not 1 <= minutes <= 43200:
            raise UserError("Snooze must be between 1 minute and 30 days.")

        async def operation(session):
            holding = await self._holding(session, position_id)
            p = holding.position
            p.snoozed_until = now + minutes * 60
            # Explicitly rearm after the snooze, keeping the same target price.
            p.target_revision += 1
            await self._cancel(session, position_id)
            return {"position_id": position_id, "snoozed_until": p.snoozed_until}

        return await self.mutate(interaction, "snooze", operation, now)

    async def edit(
        self,
        interaction: str,
        kind: str,
        trade_id: int,
        now: int,
        quantity: int | None = None,
        price: int | None = None,
        occurred_at: int | None = None,
        void=False,
    ):
        if kind not in ("buy", "sale"):
            raise UserError("Trade kind must be buy or sale.")
        if not void and all(v is None for v in (quantity, price, occurred_at)):
            raise UserError("Provide a quantity, price, or time to correct.")

        async def operation(session):
            record = await session.get(Position if kind == "buy" else Sale, trade_id)
            if record is None or record.voided_at is not None:
                raise UserError("Trade not found or already voided.")
            position_id = record.id if kind == "buy" else record.position_id
            holding = await self._holding(session, position_id)
            original = {
                "quantity": record.quantity,
                "price": record.buy_price if kind == "buy" else record.price,
            }
            if void:
                if kind == "buy" and any(s.voided_at is None for s in holding.sales):
                    raise UserError("Void the related sales before voiding this purchase.")
                record.voided_at = now
            else:
                q = record.quantity if quantity is None else quantity
                old_price = record.buy_price if kind == "buy" else record.price
                px = old_price if price is None else price
                self.validate_trade(q, px)
                ts = record.bought_at if kind == "buy" else record.sold_at
                ts = ts if occurred_at is None else occurred_at
                if ts > now:
                    raise UserError("Trade time cannot be in the future.")
                if kind == "buy":
                    if q < sum(s.quantity for s in holding.sales if s.voided_at is None):
                        raise UserError("Purchase quantity cannot be less than units already sold.")
                    if any(s.sold_at < ts for s in holding.sales if s.voided_at is None):
                        raise UserError("Purchase cannot be later than a related sale.")
                    record.buy_price, record.bought_at = px, ts
                else:
                    others = sum(
                        s.quantity for s in holding.sales if s.voided_at is None and s.id != record.id
                    )
                    if q + others > holding.position.quantity:
                        raise UserError("Correction would oversell the position.")
                    if ts < holding.position.bought_at:
                        raise UserError("Sale cannot predate its purchase.")
                    record.price, record.sold_at = px, ts
                record.quantity = q
            await self._cancel(session, position_id)
            return {"kind": kind, "trade_id": trade_id, "voided": void, "previous": original}

        return await self.mutate(interaction, "void" if void else "edit", operation, now)

    async def preferences(self):
        async with self.db.write_lock, self.db.sessions() as session, session.begin():
            value = await session.get(Preferences, 1)
            if value is None:
                value = Preferences(id=1, budget=self.default_budget, profile="balanced")
                session.add(value)
            return value

    async def settings(self, interaction: str, budget: int | None, profile: Profile | None, now: int):
        if budget is not None and not 0 < budget <= MAX_INTEGER:
            raise UserError("Budget must be a positive whole GP amount.")

        async def operation(session):
            value = await session.get(Preferences, 1)
            if value is None:
                value = Preferences(id=1, budget=self.default_budget, profile="balanced")
                session.add(value)
            if budget is not None:
                value.budget = budget
            if profile is not None:
                value.profile = Profile(profile).value
            await session.flush()
            return {"budget": value.budget, "profile": value.profile}

        return await self.mutate(interaction, "settings", operation, now)

    async def item_flag(self, interaction: str, item_id: int, flag: str, enabled: bool, now: int):
        if flag not in ("watched", "favorite", "excluded"):
            raise UserError("Unknown item preference.")

        async def operation(session):
            item = await session.get(Item, item_id)
            if item is None:
                raise UserError("Item not found.")
            setattr(item, flag, enabled)
            return {"item_id": item_id, flag: enabled}

        return await self.mutate(interaction, flag, operation, now)

    async def recent_buys(self, now: int) -> dict[int, int]:
        async with self.db.sessions() as session:
            positions = (
                await session.scalars(
                    select(Position).where(
                        Position.voided_at.is_(None),
                        Position.bought_at > now - 14400,
                        Position.bought_at <= now,
                    )
                )
            ).all()
            counts: dict[int, int] = {}
            for p in positions:
                counts[p.item_id] = counts.get(p.item_id, 0) + p.quantity
            return counts
