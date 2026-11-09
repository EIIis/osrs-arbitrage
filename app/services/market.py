import time
from collections import Counter
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from app.core.exceptions import UserError
from app.db.session import Database
from app.domain.analysis import InsufficientData, analyze, rank, recommend
from app.domain.calculations import net
from app.domain.types import RULES, Bucket, Mode, Profile, Quote
from app.models import CachedAggregate, HistorySync, Item, MarketQuote, Position, PriceBucket
from app.services.ledger import Ledger
from app.services.wiki import WikiClient


def bucket_values(item_id: int, b: Bucket) -> dict:
    return dict(
        item_id=item_id,
        interval=b.interval,
        start=b.start,
        high_cents=int(b.high * 100) if b.high is not None else None,
        low_cents=int(b.low * 100) if b.low is not None else None,
        high_volume=b.high_volume,
        low_volume=b.low_volume,
        observed_at=b.observed_at,
    )


def to_bucket(row) -> Bucket:
    return Bucket(
        row.start,
        row.interval,
        Decimal(row.high_cents) / 100 if row.high_cents is not None else None,
        Decimal(row.low_cents) / 100 if row.low_cents is not None else None,
        row.high_volume,
        row.low_volume,
        row.observed_at,
    )


def to_quote(row: MarketQuote) -> Quote:
    return Quote(row.high, row.low, row.high_time, row.low_time, row.observed_at)


class Market:
    def __init__(self, db: Database, wiki: WikiClient, ledger: Ledger, shortlist_size=20):
        self.db, self.wiki, self.ledger = db, wiki, ledger
        self.shortlist_size = shortlist_size
        self.last_success: dict[str, int] = {}
        self.last_errors: dict[str, str] = {}
        self.preparing = 0
        self.history_progress = (0, 0)

    async def catalog(self):
        items = await self.wiki.catalog()
        async with self.db.write_lock, self.db.sessions() as session, session.begin():
            for offset in range(0, len(items), 200):
                rows = [
                    dict(
                        id=i.id,
                        name=i.name,
                        members=i.members,
                        buy_limit=i.limit,
                        watched=False,
                        favorite=False,
                        excluded=False,
                    )
                    for i in items[offset : offset + 200]
                ]
                statement = insert(Item).values(rows)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            name: getattr(statement.excluded, name)
                            for name in ("name", "members", "buy_limit")
                        },
                    )
                )
        self.last_success["catalog"] = int(time.time())

    async def items(self) -> list[Item]:
        async with self.db.sessions() as session:
            return list((await session.scalars(select(Item).order_by(Item.name, Item.id))).all())

    async def resolve(self, text: str) -> Item:
        async with self.db.sessions() as session:
            if text.isdigit():
                item = await session.get(Item, int(text))
                if item:
                    return item
            matches = list(
                (
                    await session.scalars(
                        select(Item).where(
                            Item.name.ilike(text.replace("%", "\\%").replace("_", "\\_"), escape="\\")
                        )
                    )
                ).all()
            )
            if len(matches) == 1:
                return matches[0]
        raise UserError("Item not found or ambiguous. Select an item from autocomplete or use its exact ID.")

    async def refresh_latest(self, now: int):
        data = await self.wiki.latest(now)
        async with self.db.write_lock, self.db.sessions() as session, session.begin():
            known = set((await session.scalars(select(Item.id))).all())
            rows = [
                dict(
                    item_id=key,
                    high=q.high,
                    low=q.low,
                    high_time=q.high_time,
                    low_time=q.low_time,
                    observed_at=q.observed_at,
                )
                for key, q in data.items()
                if key in known
            ]
            for offset in range(0, len(rows), 200):
                statement = insert(MarketQuote).values(rows[offset : offset + 200])
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["item_id"],
                        set_={
                            name: getattr(statement.excluded, name)
                            for name in ("high", "low", "high_time", "low_time", "observed_at")
                        },
                    )
                )
        self.last_success["latest"] = now

    async def refresh_averages(self, interval: int, now: int):
        data = await self.wiki.averages(interval, now)
        async with self.db.write_lock, self.db.sessions() as session, session.begin():
            known = set((await session.scalars(select(Item.id))).all())
            tracked = set((await session.scalars(select(Item.id).where(Item.watched | Item.favorite))).all())
            tracked.update(
                (await session.scalars(select(Position.item_id).where(Position.voided_at.is_(None)))).all()
            )
            tracked.update(
                (
                    await session.scalars(
                        select(HistorySync.item_id).where(HistorySync.fetched_at >= now - 86400)
                    )
                ).all()
            )
            rows = [bucket_values(key, b) for key, b in data.items() if key in known]
            for offset in range(0, len(rows), 100):
                statement = insert(CachedAggregate).values(rows[offset : offset + 100])
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["item_id", "interval"],
                        set_={
                            name: getattr(statement.excluded, name)
                            for name in rows[0]
                            if name not in ("item_id", "interval")
                        },
                    )
                )
            history_rows = [row for row in rows if row["item_id"] in tracked]
            for offset in range(0, len(history_rows), 100):
                statement = insert(PriceBucket).values(history_rows[offset : offset + 100])
                await session.execute(
                    statement.on_conflict_do_nothing(index_elements=["item_id", "interval", "start"])
                )
        self.last_success[f"{interval}s"] = now

    async def ensure_history(self, item_id: int, mode: Mode, now: int):
        rule = RULES[mode]
        async with self.db.sessions() as session:
            current = await session.scalar(
                select(HistorySync).where(HistorySync.item_id == item_id, HistorySync.mode == mode.value)
            )
            if current and 0 <= now - current.fetched_at < rule.history_ttl:
                return
        if mode == Mode.HOLD:
            # Thirty-day history is currently six-hourly: fetch actual hourly context for volatility.
            await self.ensure_history(item_id, Mode.SWING, now)
        self.preparing += 1
        try:
            history = await self.wiki.history(item_id, rule.lookback, rule.history_ttl, now)
            async with self.db.write_lock, self.db.sessions() as session, session.begin():
                rows = [bucket_values(item_id, row) for row in history.buckets]
                for offset in range(0, len(rows), 100):
                    statement = insert(PriceBucket).values(rows[offset : offset + 100])
                    # Never rewrite observation time during a repeat fetch.
                    await session.execute(
                        statement.on_conflict_do_nothing(index_elements=["item_id", "interval", "start"])
                    )
                statement = insert(HistorySync).values(
                    item_id=item_id,
                    mode=mode.value,
                    fetched_at=now,
                    start=history.start,
                    end=history.end,
                    interval=history.interval,
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["item_id", "mode"],
                        set_={
                            key: getattr(statement.excluded, key)
                            for key in ("fetched_at", "start", "end", "interval")
                        },
                    )
                )
        finally:
            self.preparing -= 1

    async def observations(self, item_id: int, mode: Mode, now: int):
        async with self.db.sessions() as session:
            sync = await session.scalar(
                select(HistorySync).where(HistorySync.item_id == item_id, HistorySync.mode == mode.value)
            )
            if sync is None or sync.fetched_at > now:
                raise InsufficientData("History is still preparing.")
            rows = (
                await session.scalars(
                    select(PriceBucket)
                    .where(
                        PriceBucket.item_id == item_id,
                        PriceBucket.interval == sync.interval,
                        PriceBucket.start >= now - RULES[mode].history_seconds,
                        PriceBucket.start <= now,
                        PriceBucket.observed_at <= now,
                    )
                    .order_by(PriceBucket.start)
                )
            ).all()
            return [to_bucket(row) for row in rows]

    async def snapshot(self, item_id: int):
        async with self.db.sessions() as session:
            q = await session.get(MarketQuote, item_id)
            aggregates = (
                await session.scalars(select(CachedAggregate).where(CachedAggregate.item_id == item_id))
            ).all()
            return (to_quote(q) if q else None, {a.interval: to_bucket(a) for a in aggregates})

    async def suggested_target(self, item_id: int, buy_price: int, mode: Mode, now: int):
        try:
            quote, aggregates = await self.snapshot(item_id)
            hourly = aggregates.get(3600)
            if (
                not quote
                or not quote.fresh(now, RULES[mode].freshness)
                or not hourly
                or not hourly.valid
                or not 0 <= now - hourly.end <= 7200
            ):
                raise InsufficientData("Fresh quotes and hourly volume are required.")
            analysis = analyze(await self.observations(item_id, mode, now), mode, now)
            roi = Decimal(net(analysis.sell_low, item_id) - buy_price) / buy_price * 100
            if analysis.sell_low <= 0 or roi < RULES[mode].min_roi:
                raise InsufficientData("Historical exit band does not support the minimum net return.")
            if analysis.trend_percent < (Decimal("-1") if mode == Mode.QUICK else Decimal(0)):
                raise InsufficientData("The recent trend is still declining.")
            return (
                analysis.sell_low,
                f"Historical {mode.value} exit band; {roi:.2f}% estimated net return at entry.",
            )
        except InsufficientData as exc:
            return None, f"No supported target: {exc}"

    async def shortlist(self, mode: Mode, now: int) -> list[int]:
        async with self.db.sessions() as session:
            rows = (
                await session.execute(
                    select(Item, MarketQuote, CachedAggregate)
                    .join(MarketQuote, MarketQuote.item_id == Item.id)
                    .join(CachedAggregate, CachedAggregate.item_id == Item.id)
                    .where(~Item.excluded, CachedAggregate.interval == 3600, Item.id != 13190)
                )
            ).all()
            candidates = []
            for item, quote, aggregate in rows:
                q, a = to_quote(quote), to_bucket(aggregate)
                if not q.fresh(now, RULES[mode].freshness) or not a.valid or not 0 <= now - a.end <= 7200:
                    continue
                if min(a.high_volume, a.low_volume) < 10:
                    continue
                # Longer holds can have recovery potential even when today's spread is small.
                margin = (a.high - a.low) / a.low
                candidates.append((not item.favorite, -margin, item.id))
            candidates.sort()
            return [item_id for _, _, item_id in candidates[: self.shortlist_size]]

    async def suggest(self, mode: Mode, profile: Profile, budget: int, now: int):
        if budget <= 0:
            raise UserError("Set /settings budget or provide a positive /suggest budget.")
        ids = await self.shortlist(mode, now)
        bought = await self.ledger.recent_buys(now)
        items = {item.id: item for item in await self.items()}
        results, rejected = [], Counter()
        for item_id in ids:
            item = items[item_id]
            try:
                quote, aggregates = await self.snapshot(item_id)
                rows = await self.observations(item_id, mode, now)
                hourly_rows = None
                if mode == Mode.HOLD:
                    try:
                        hourly_rows = await self.observations(item_id, Mode.SWING, now)
                    except InsufficientData:
                        hourly_rows = []
                result = recommend(
                    item_id,
                    item.name,
                    item.buy_limit,
                    bought.get(item_id, 0),
                    mode,
                    profile,
                    budget,
                    quote,
                    aggregates.get(3600),
                    rows,
                    now,
                    hourly_rows=hourly_rows,
                )
                results.append(result)
            except InsufficientData as exc:
                rejected[str(exc)] += 1
        if not ids:
            rejected["No fresh, liquid bulk-market candidates; waiting for market data."] += 1
        return rank(results)[:5], rejected

    async def refresh_histories(self, now: int):
        # Holdings take priority over discovery, then watchlists/favorites, then twenty candidates/mode.
        holdings = await self.ledger.holdings()
        jobs = list(dict.fromkeys((h.item.id, Mode(h.position.mode)) for h in holdings))
        items = await self.items()
        for item in items:
            if item.watched or item.favorite:
                jobs.extend((item.id, mode) for mode in Mode)
        for mode in Mode:
            jobs.extend((item_id, mode) for item_id in await self.shortlist(mode, now))
        jobs = list(dict.fromkeys(jobs))
        self.history_progress = (0, len(jobs))
        for index, (item_id, mode) in enumerate(jobs):
            try:
                await self.ensure_history(item_id, mode, int(time.time()))
                self.last_errors.pop(f"history:{item_id}:{mode}", None)
            except Exception as exc:
                self.last_errors[f"history:{item_id}:{mode}"] = str(exc)
            finally:
                self.history_progress = (index + 1, len(jobs))

    async def prune(self, now: int):
        async with self.db.write_lock, self.db.sessions() as session, session.begin():
            await session.execute(
                delete(PriceBucket).where(
                    ((PriceBucket.interval < 3600) & (PriceBucket.start < now - 7 * 86400))
                    | ((PriceBucket.interval >= 3600) & (PriceBucket.start < now - 90 * 86400))
                )
            )
