"""Persisted confirmation, cooldowns, and an outbox independent of Discord."""

import json
from decimal import Decimal

from sqlalchemy import select

from app.domain.analysis import InsufficientData, analyze
from app.domain.calculations import floor, net, profit
from app.domain.types import PROFILE_RULES, RULES, Mode, Profile
from app.models import AlertDelivery, AlertState


class PermanentDeliveryError(RuntimeError):
    pass


class Alerts:
    def __init__(self, db, ledger, market):
        self.db, self.ledger, self.market = db, ledger, market

    async def conditions(self, holding, now: int):
        p, item = holding.position, holding.item
        mode = Mode(p.mode)
        rule = RULES[mode]
        if holding.remaining <= 0 or p.voided_at is not None or p.snoozed_until > now:
            return None
        quote, aggregates = await self.market.snapshot(item.id)
        bucket, hourly = aggregates.get(300), aggregates.get(3600)
        if (
            not quote
            or not quote.fresh(now, rule.freshness)
            or not bucket
            or not bucket.valid
            or not 0 <= now - bucket.end <= 600
            or not hourly
            or not hourly.valid
            or not 0 <= now - hourly.end <= 7200
        ):
            return None
        # The lower of observed latest/average high is a cautious patient-sale estimate.
        passive = min(quote.high, floor(bucket.high))
        immediate = quote.low
        target_hit = p.target_price is not None and passive >= p.target_price
        loss = Decimal(net(immediate, item.id) - p.buy_price) / p.buy_price * 100
        warnings = []
        if loss <= -Decimal(p.loss_percent):
            warnings.append(f"Immediate-sale estimate is {loss:.2f}% versus purchase after tax.")
        analysis = None
        try:
            analysis = analyze(await self.market.observations(item.id, mode, now), mode, now)
            if analysis.trend_percent <= -rule.decline_warning:
                warnings.append(f"{mode.value.title()} midpoint trend fell {analysis.trend_percent:.2f}%.")
        except InsufficientData:
            pass
        preferences = await self.ledger.preferences()
        participation = PROFILE_RULES[Profile(preferences.profile)][1]
        supported = min(
            holding.remaining, floor(Decimal(min(hourly.high_volume, hourly.low_volume)) * participation)
        )
        target_hit = target_hit and supported > 0
        return dict(
            position_id=p.id,
            item_name=item.name,
            quantity=holding.remaining,
            buy_price=p.buy_price,
            target=p.target_price,
            mode=mode.value,
            revision=p.target_revision,
            target_hit=target_hit,
            warnings=warnings,
            sell_low=analysis.sell_low if analysis else passive,
            sell_high=analysis.sell_high if analysis else passive,
            passive_profit=profit(p.buy_price, passive, holding.remaining, item.id),
            immediate_profit=profit(p.buy_price, immediate, holding.remaining, item.id),
            instant_target_hit=p.target_price is not None and immediate >= p.target_price,
            hourly_volume=min(hourly.high_volume, hourly.low_volume),
            supported_quantity=supported,
            bucket=bucket.start,
            quote_age=max(now - quote.high_time, now - quote.low_time),
            explanation=p.target_reason,
        )

    async def evaluate(self, now: int):
        for holding in await self.ledger.holdings():
            conditions = await self.conditions(holding, now)
            p = holding.position
            async with self.db.write_lock, self.db.sessions() as session, session.begin():
                existing = {
                    s.kind: s
                    for s in (
                        await session.scalars(select(AlertState).where(AlertState.position_id == p.id))
                    ).all()
                }
                ready = []
                for kind in ("target", "warning"):
                    state = existing.get(kind)
                    if state is None:
                        state = AlertState(
                            position_id=p.id,
                            kind=kind,
                            revision=p.target_revision,
                            last_bucket=0,
                            streak=0,
                            last_sent=0,
                            notified_revision=0,
                        )
                        session.add(state)
                    if state.revision != p.target_revision:
                        state.revision, state.streak, state.last_bucket = p.target_revision, 0, 0
                    if conditions is None:
                        state.streak = 0
                        continue
                    stamp = conditions["bucket"]
                    triggered = conditions["target_hit"] if kind == "target" else bool(conditions["warnings"])
                    if not triggered:
                        state.streak = 0
                        state.last_bucket = stamp
                        continue
                    if stamp != state.last_bucket:
                        state.streak = state.streak + 1 if stamp == state.last_bucket + 300 else 1
                        state.last_bucket = stamp
                    if state.streak < 2:
                        continue
                    if kind == "target" and state.notified_revision == p.target_revision:
                        continue
                    if (
                        kind == "warning"
                        and state.last_sent
                        and now - state.last_sent < RULES[Mode(p.mode)].cooldown
                    ):
                        continue
                    outstanding = (
                        await session.scalars(
                            select(AlertDelivery).where(
                                AlertDelivery.position_id == p.id,
                                AlertDelivery.revision == p.target_revision,
                                AlertDelivery.status.in_(["pending", "sending", "failed"]),
                            )
                        )
                    ).all()
                    if any(kind in delivery.kind.split("+") for delivery in outstanding):
                        continue
                    ready.append(kind)
                if ready:
                    kind = "+".join(ready)
                    reference = f"P{p.id}-R{p.target_revision}-B{conditions['bucket']}-{kind}"
                    if (
                        await session.scalar(
                            select(AlertDelivery.id).where(AlertDelivery.reference == reference)
                        )
                        is None
                    ):
                        session.add(
                            AlertDelivery(
                                reference=reference,
                                position_id=p.id,
                                revision=p.target_revision,
                                kind=kind,
                                payload=json.dumps(conditions),
                                created_at=now,
                                status="pending",
                                attempts=0,
                                next_attempt=now,
                            )
                        )

    async def deliver(self, send, find_reference, now: int):
        async with self.db.sessions() as session:
            pending = list(
                (
                    await session.scalars(
                        select(AlertDelivery)
                        .where(
                            AlertDelivery.status.in_(["pending", "sending"]),
                            AlertDelivery.next_attempt <= now,
                        )
                        .order_by(AlertDelivery.id)
                    )
                ).all()
            )
        for delivery in pending:
            try:
                holding = await self.ledger.holding(delivery.position_id)
                current = await self.conditions(holding, now)
                valid = (
                    current is not None
                    and current["revision"] == delivery.revision
                    and all(
                        current["target_hit"] if kind == "target" else bool(current["warnings"])
                        for kind in delivery.kind.split("+")
                    )
                )
                if not valid:
                    await self._update(delivery.id, status="cancelled")
                    continue
                if delivery.status == "sending" or delivery.attempts:
                    found = await find_reference(delivery.reference)
                    if found:
                        await self._sent(delivery, found, now)
                        continue
                await self._update(delivery.id, status="sending", attempts=delivery.attempts + 1)
                # Current quantities/valuations prevent queued messages from showing stale ledger values.
                message_id = await send(delivery.reference, delivery.kind, current)
                await self._sent(delivery, message_id, now)
            except PermanentDeliveryError as exc:
                await self._update(delivery.id, status="failed", error=str(exc))
            except Exception as exc:
                await self._update(
                    delivery.id,
                    status="sending",
                    next_attempt=now + min(3600, 30 * 2 ** min(delivery.attempts, 7)),
                    error=f"{type(exc).__name__}: {exc}",
                )

    async def _update(self, delivery_id: int, **values):
        async with self.db.write_lock, self.db.sessions() as session, session.begin():
            delivery = await session.get(AlertDelivery, delivery_id)
            for key, value in values.items():
                setattr(delivery, key, value)

    async def _sent(self, delivery, message_id: int, now: int):
        async with self.db.write_lock, self.db.sessions() as session, session.begin():
            record = await session.get(AlertDelivery, delivery.id)
            record.status, record.message_id, record.error = "sent", message_id, None
            for kind in delivery.kind.split("+"):
                state = await session.scalar(
                    select(AlertState).where(
                        AlertState.position_id == delivery.position_id, AlertState.kind == kind
                    )
                )
                if state:
                    state.last_sent = now
                    if kind == "target":
                        state.notified_revision = delivery.revision

    async def failures(self):
        async with self.db.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(AlertDelivery)
                        .where(
                            AlertDelivery.status.in_(["failed", "sending", "pending"]),
                            AlertDelivery.error.is_not(None),
                        )
                        .order_by(AlertDelivery.id.desc())
                        .limit(5)
                    )
                ).all()
            )
