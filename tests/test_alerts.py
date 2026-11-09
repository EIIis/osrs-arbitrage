from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.domain.types import Bucket, Mode, Quote
from app.models import AlertDelivery
from app.services.alerts import Alerts, PermanentDeliveryError

NOW = 1794268800


class FakeMarket:
    def __init__(self):
        self.now = NOW
        self.high = 120
        self.low = 115
        self.stale = False
        self.trend_rows = []

    async def snapshot(self, item_id):
        now = self.now
        return Quote(self.high, self.low, now - 10000 if self.stale else now, now, now), {
            300: Bucket(now - 300, 300, Decimal(self.high), Decimal(self.low), 1000, 1000, now),
            3600: Bucket(now - 3600, 3600, Decimal(self.high), Decimal(self.low), 1000, 1000, now),
        }

    async def observations(self, item_id, mode, now):
        return self.trend_rows


async def setup(ledger, database):
    position = await ledger.buy("buy", 453, 100, 100, Mode.QUICK, NOW, target=110)
    market = FakeMarket()
    alerts = Alerts(database, ledger, market)
    return position["position_id"], market, alerts


async def deliveries(db):
    async with db.sessions() as s:
        return list((await s.scalars(select(AlertDelivery).order_by(AlertDelivery.id))).all())


async def qualify(market, alerts):
    await alerts.evaluate(market.now)
    await alerts.evaluate(market.now)  # same source bucket is not another confirmation
    market.now += 300
    await alerts.evaluate(market.now)


async def test_distinct_buckets_restart_and_no_repeat(ledger, database):
    pid, market, alerts = await setup(ledger, database)
    await alerts.evaluate(NOW)
    await alerts.evaluate(NOW)
    assert await deliveries(database) == []
    market.now += 300
    await alerts.evaluate(market.now)
    assert len(await deliveries(database)) == 1
    restarted = Alerts(database, ledger, market)
    send, find = AsyncMock(return_value=42), AsyncMock(return_value=None)
    await restarted.deliver(send, find, market.now)
    send.assert_awaited_once()
    market.now += 300
    await restarted.evaluate(market.now)
    await restarted.deliver(send, find, market.now)
    assert len(await deliveries(database)) == 1
    assert (await deliveries(database))[0].message_id == 42


async def test_stale_prices_and_confirmation_gaps(ledger, database):
    _, market, alerts = await setup(ledger, database)
    await alerts.evaluate(NOW)
    market.stale = True
    market.now += 300
    await alerts.evaluate(market.now)
    market.stale = False
    market.now += 300
    await alerts.evaluate(market.now)
    assert await deliveries(database) == []
    market.now += 300
    await alerts.evaluate(market.now)
    assert len(await deliveries(database)) == 1


async def test_target_revision_and_closed_position_cancel_pending(ledger, database):
    pid, market, alerts = await setup(ledger, database)
    await qualify(market, alerts)
    await ledger.target("target", pid, market.now, price=130)
    assert (await deliveries(database))[0].status == "cancelled"
    await ledger.target("target2", pid, market.now, price=110)
    await qualify(market, alerts)
    await ledger.sell("sale", pid, 100, 120, market.now)
    send = AsyncMock(return_value=42)
    await alerts.deliver(send, AsyncMock(), market.now)
    send.assert_not_awaited()


async def test_uncertain_delivery_reconciles_without_duplicate(ledger, database):
    _, market, alerts = await setup(ledger, database)
    await qualify(market, alerts)
    send = AsyncMock(side_effect=TimeoutError("response lost"))
    await alerts.deliver(send, AsyncMock(return_value=None), market.now)
    assert (await deliveries(database))[0].status == "sending"
    await alerts.deliver(send, AsyncMock(return_value=42), market.now + 60)
    assert send.await_count == 1
    assert (await deliveries(database))[0].status == "sent"


async def test_permanent_permissions_failure_stops_retry(ledger, database):
    _, market, alerts = await setup(ledger, database)
    await qualify(market, alerts)
    send = AsyncMock(side_effect=PermanentDeliveryError("missing channel permission"))
    await alerts.deliver(send, AsyncMock(return_value=None), market.now)
    market.now += 300
    await alerts.evaluate(market.now)
    await alerts.deliver(send, AsyncMock(), market.now)
    assert send.await_count == 1
    assert len(await deliveries(database)) == 1
    assert (await deliveries(database))[0].status == "failed"


async def test_snooze_rearms_after_expiry(ledger, database):
    pid, market, alerts = await setup(ledger, database)
    await qualify(market, alerts)
    await alerts.deliver(AsyncMock(return_value=42), AsyncMock(), market.now)
    await ledger.snooze("snooze", pid, 5, market.now)
    await alerts.evaluate(market.now)
    market.now += 300
    await alerts.evaluate(market.now)
    market.now += 300
    await alerts.evaluate(market.now)
    assert len(await deliveries(database)) == 2


async def test_loss_warning_cooldown_and_combined_alert(ledger, database):
    pid, market, alerts = await setup(ledger, database)
    market.low = 90
    await qualify(market, alerts)
    rows = await deliveries(database)
    assert len(rows) == 1 and rows[0].kind == "target+warning"
    await alerts.deliver(AsyncMock(return_value=42), AsyncMock(), market.now)
    market.now += 300
    await alerts.evaluate(market.now)
    assert len(await deliveries(database)) == 1


async def test_queued_alert_is_cancelled_when_market_reverses(ledger, database):
    _, market, alerts = await setup(ledger, database)
    await qualify(market, alerts)
    market.high = 105
    send = AsyncMock()
    await alerts.deliver(send, AsyncMock(), market.now)
    send.assert_not_awaited()
    assert (await deliveries(database))[0].status == "cancelled"


async def test_zero_supported_quantity_does_not_trigger_sell_alert(ledger, database):
    _, market, alerts = await setup(ledger, database)
    original = market.snapshot

    async def thin_snapshot(item_id):
        quote, aggregates = await original(item_id)
        aggregates[3600] = replace(aggregates[3600], high_volume=1, low_volume=1)
        return quote, aggregates

    market.snapshot = thin_snapshot
    await qualify(market, alerts)
    assert await deliveries(database) == []
