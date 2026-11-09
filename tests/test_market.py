from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.core.exceptions import UserError
from app.domain.types import Bucket, Mode, Profile, Quote
from app.models import CachedAggregate, PriceBucket
from app.services.market import Market
from app.services.wiki import CatalogItem, History
from tests.test_analysis import NOW, history


@pytest.fixture
async def market(database, ledger):
    wiki = AsyncMock()
    wiki.catalog.return_value = [
        CatalogItem(id=453, name="Coal", members=False, limit=13000),
        CatalogItem(id=440, name="Iron ore", members=False, limit=13000),
    ]
    wiki.latest.return_value = {453: Quote(121, 101, NOW, NOW, NOW)}
    wiki.averages.side_effect = lambda interval, now: {
        453: Bucket(now - interval, interval, Decimal("120.5"), Decimal("100.25"), 1000, 1000, now)
    }
    wiki.history.side_effect = lambda item_id, lookback, ttl, now: History(
        item_id, now - 86400, now, 300, history(Mode.QUICK)
    )
    return Market(database, wiki, ledger)


async def test_catalog_upsert_retains_flags_and_ambiguous_names(market, ledger):
    await ledger.item_flag("watch", 453, "watched", True, NOW)
    await market.catalog()
    assert (await market.resolve("453")).watched
    assert (await market.resolve("coal")).id == 453
    with pytest.raises(UserError):
        await market.resolve("Coal%")


async def test_storage_dedup_precision_and_suggestions(market, database):
    await market.catalog()
    await market.refresh_latest(NOW)
    await market.refresh_averages(300, NOW)
    await market.refresh_averages(3600, NOW)
    await market.ensure_history(453, Mode.QUICK, NOW)
    await market.ensure_history(453, Mode.QUICK, NOW)
    assert market.wiki.history.await_count == 1
    async with database.sessions() as s:
        count = await s.scalar(select(func.count()).select_from(PriceBucket))
        assert count == 288
        assert (await s.scalar(select(PriceBucket).where(PriceBucket.item_id == 453))).high_cents == 12050
        assert await s.scalar(select(func.count()).select_from(CachedAggregate)) == 2
    results, rejected = await market.suggest(Mode.QUICK, Profile.BALANCED, 100000, NOW)
    assert len(results) == 1 and not rejected
    assert results[0].analysis.buy_high == 101
    target, _ = await market.suggested_target(453, 101, Mode.QUICK, NOW)
    assert target == 120


async def test_no_supported_target_still_allows_purchase(market, ledger):
    target, reason = await market.suggested_target(453, 100, Mode.QUICK, NOW)
    assert target is None
    result = await ledger.buy("buy", 453, 10, 100, Mode.QUICK, NOW, target=target, reason=reason)
    assert (await ledger.holding(result["position_id"])).position.target_price is None


async def test_exclusion_does_not_disable_owned_monitoring(market, ledger):
    await market.catalog()
    await market.refresh_latest(NOW)
    await market.refresh_averages(3600, NOW)
    await ledger.item_flag("exclude", 453, "excluded", True, NOW)
    await ledger.buy("buy", 453, 10, 100, Mode.HOLD, NOW)
    assert await market.shortlist(Mode.QUICK, NOW) == []
    assert (await ledger.holdings())[0].item.excluded


async def test_prune_does_not_remove_transactions(database, market, ledger):
    await ledger.buy("buy", 453, 10, 100, Mode.QUICK, NOW)
    async with database.sessions() as s, s.begin():
        s.add_all(
            [
                PriceBucket(
                    item_id=453,
                    start=NOW - 8 * 86400,
                    interval=300,
                    high_cents=12000,
                    low_cents=10000,
                    high_volume=1,
                    low_volume=1,
                    observed_at=NOW,
                ),
                PriceBucket(
                    item_id=453,
                    start=NOW - 80 * 86400,
                    interval=3600,
                    high_cents=12000,
                    low_cents=10000,
                    high_volume=1,
                    low_volume=1,
                    observed_at=NOW,
                ),
            ]
        )
    await market.prune(NOW)
    async with database.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(PriceBucket)) == 1
    assert len(await ledger.holdings()) == 1
