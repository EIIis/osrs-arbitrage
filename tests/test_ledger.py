import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.exceptions import UserError
from app.db.session import Database
from app.domain.types import Mode, Profile
from app.models import Audit, Mutation, Position
from app.services.ledger import Ledger

NOW = 1794268800


async def purchase(ledger, key="buy1", quantity=100):
    return await ledger.buy(key, 453, quantity, 100, Mode.QUICK, NOW)


async def test_separate_lots_partial_sales_corrections_and_restart(ledger, database):
    first = await purchase(ledger)
    second = await purchase(ledger, "buy2")
    assert first["position_id"] != second["position_id"]
    sale = await ledger.sell("sale1", first["position_id"], 40, 110, NOW)
    assert sale["profit"] == 320
    await ledger.edit("edit1", "sale", sale["sale_id"], NOW, price=120)
    h = await ledger.holding(first["position_id"])
    assert h.remaining == 60 and h.realized == 720
    reopened = Database(database.path)
    try:
        h = await Ledger(reopened).holding(first["position_id"])
        assert h.remaining == 60 and h.realized == 720
        assert len(await Ledger(reopened).holdings()) == 2
    finally:
        await reopened.close()


async def test_idempotency_and_atomic_invalid_sales(ledger, database):
    p = await purchase(ledger)
    assert await purchase(ledger) == p
    with pytest.raises(UserError):
        await ledger.sell("oversold", p["position_id"], 101, 110, NOW)
    async with database.sessions() as session:
        assert await session.get(Mutation, "oversold") is None
    assert (await ledger.holding(p["position_id"])).remaining == 100
    result = await ledger.sell("actual", p["position_id"], 100, 110, NOW)
    assert await ledger.sell("actual", p["position_id"], 100, 110, NOW) == result
    assert await ledger.holdings() == []


async def test_concurrent_sales_cannot_oversell(ledger):
    p = await purchase(ledger)
    results = await asyncio.gather(
        ledger.sell("s1", p["position_id"], 70, 110, NOW),
        ledger.sell("s2", p["position_id"], 70, 110, NOW),
        return_exceptions=True,
    )
    assert sum(isinstance(r, UserError) for r in results) == 1
    assert (await ledger.holding(p["position_id"])).remaining == 30


async def test_edit_rejects_oversell_and_invalid_time(ledger):
    p = await purchase(ledger)
    sale = await ledger.sell("s1", p["position_id"], 80, 110, NOW)
    with pytest.raises(UserError):
        await ledger.edit("e1", "buy", p["position_id"], NOW, quantity=70)
    with pytest.raises(UserError):
        await ledger.edit("e2", "sale", sale["sale_id"], NOW, quantity=110)
    with pytest.raises(UserError):
        await ledger.edit("e3", "buy", p["position_id"], NOW + 10, occurred_at=NOW + 1)


async def test_voids_are_retained_and_audited(ledger, database):
    p = await purchase(ledger)
    sale = await ledger.sell("s1", p["position_id"], 50, 110, NOW)
    with pytest.raises(UserError):
        await ledger.edit("v1", "buy", p["position_id"], NOW, void=True)
    await ledger.edit("v2", "sale", sale["sale_id"], NOW, void=True)
    assert (await ledger.holding(p["position_id"])).remaining == 100
    await ledger.edit("v3", "buy", p["position_id"], NOW, void=True)
    async with database.sessions() as session:
        assert (await session.get(Position, p["position_id"])).voided_at == NOW
        assert len((await session.scalars(select(Audit))).all()) == 4


async def test_targets_preferences_and_budget_independence(ledger):
    p = await purchase(ledger)
    result = await ledger.target("t1", p["position_id"], NOW, percent=Decimal(5))
    assert result["target"] == 107
    await ledger.settings("prefs", 1000000, Profile.CONSERVATIVE, NOW)
    await ledger.sell("s1", p["position_id"], 100, 110, NOW)
    assert (await ledger.preferences()).budget == 1000000
    with pytest.raises(UserError):
        await ledger.target("t2", p["position_id"], NOW, price=120)
    with pytest.raises(UserError):
        await ledger.risk("risk", p["position_id"], Decimal("NaN"), NOW)


async def test_recent_buy_limit_is_conservative(ledger):
    await ledger.buy("old", 453, 50, 100, Mode.HOLD, NOW, bought_at=NOW - 15000)
    await purchase(ledger, quantity=30)
    assert await ledger.recent_buys(NOW) == {453: 30}
