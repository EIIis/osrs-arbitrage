"""
Price service for managing price snapshots.

Handles storing and retrieving price data from the RuneLite API.
"""
from datetime import datetime
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.price_snapshot import PriceSnapshot
from app.core.exceptions import InvalidWindowError


async def create_price_snapshot(
    db: AsyncSession,
    item_id: int,
    window: str,
    avg_high_price: int | None,
    avg_low_price: int | None,
    high_price_volume: int | None,
    low_price_volume: int | None,
    fetched_at: datetime
) -> PriceSnapshot:
    """
    Create and store a new price snapshot.

    Note: Null prices are allowed and stored as-is. The arbitrage service
    will look back for the most recent non-null price when calculating margins.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')
        avg_high_price: Average high (sell) price, can be None
        avg_low_price: Average low (buy) price, can be None
        high_price_volume: Volume at high price, can be None
        low_price_volume: Volume at low price, can be None
        fetched_at: Timestamp when data was fetched

    Returns:
        The newly created PriceSnapshot

    Raises:
        InvalidWindowError: If window is not '1m' or '5m'
    """
    if window not in ('1m', '5m'):
        raise InvalidWindowError(f"Window must be '1m' or '5m', got '{window}'")

    snapshot = PriceSnapshot(
        item_id=item_id,
        window=window,
        avg_high_price=avg_high_price,
        avg_low_price=avg_low_price,
        high_price_volume=high_price_volume,
        low_price_volume=low_price_volume,
        fetched_at=fetched_at
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)

    return snapshot


async def get_latest_price(
    db: AsyncSession,
    item_id: int,
    window: str
) -> PriceSnapshot | None:
    """
    Get the most recent price snapshot for an item, including null prices.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')

    Returns:
        Most recent PriceSnapshot or None if no snapshots exist

    Raises:
        InvalidWindowError: If window is not '1m' or '5m'
    """
    if window not in ('1m', '5m'):
        raise InvalidWindowError(f"Window must be '1m' or '5m', got '{window}'")

    result = await db.execute(
        select(PriceSnapshot)
        .where(
            and_(
                PriceSnapshot.item_id == item_id,
                PriceSnapshot.window == window
            )
        )
        .order_by(desc(PriceSnapshot.fetched_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_non_null_price(
    db: AsyncSession,
    item_id: int,
    window: str
) -> PriceSnapshot | None:
    """
    Get the most recent price snapshot with non-null prices.

    This is used for arbitrage calculations - we look back to find the most
    recent snapshot where both avg_high_price and avg_low_price are not null.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')

    Returns:
        Most recent non-null PriceSnapshot or None if no valid snapshots exist

    Raises:
        InvalidWindowError: If window is not '1m' or '5m'
    """
    if window not in ('1m', '5m'):
        raise InvalidWindowError(f"Window must be '1m' or '5m', got '{window}'")

    result = await db.execute(
        select(PriceSnapshot)
        .where(
            and_(
                PriceSnapshot.item_id == item_id,
                PriceSnapshot.window == window,
                PriceSnapshot.avg_high_price.is_not(None),
                PriceSnapshot.avg_low_price.is_not(None)
            )
        )
        .order_by(desc(PriceSnapshot.fetched_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_price_history(
    db: AsyncSession,
    item_id: int,
    window: str,
    limit: int = 100
) -> list[PriceSnapshot]:
    """
    Get historical price snapshots for an item.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')
        limit: Maximum number of snapshots to return (default: 100, max: 1000)

    Returns:
        List of PriceSnapshot objects, ordered by most recent first

    Raises:
        InvalidWindowError: If window is not '1m' or '5m'
    """
    if window not in ('1m', '5m'):
        raise InvalidWindowError(f"Window must be '1m' or '5m', got '{window}'")

    # Cap limit at 1000
    limit = min(limit, 1000)

    result = await db.execute(
        select(PriceSnapshot)
        .where(
            and_(
                PriceSnapshot.item_id == item_id,
                PriceSnapshot.window == window
            )
        )
        .order_by(desc(PriceSnapshot.fetched_at))
        .limit(limit)
    )
    return list(result.scalars().all())
