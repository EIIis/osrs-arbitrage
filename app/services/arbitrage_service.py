"""
Arbitrage service for calculating and managing arbitrage opportunities.

Computes profit margins accounting for the Grand Exchange 2% tax.
"""
from datetime import datetime
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.arbitrage_snapshot import ArbitrageSnapshot
from app.services import price_service
from app.core.exceptions import InvalidWindowError, NoDataAvailableError


async def calculate_and_store_arbitrage(
    db: AsyncSession,
    item_id: int,
    window: str,
    computed_at: datetime | None = None
) -> ArbitrageSnapshot | None:
    """
    Calculate arbitrage opportunity for an item and store it.

    Uses the most recent non-null price data to calculate:
    - spread = avg_high_price - avg_low_price
    - tax = 0.02 * avg_high_price (2% GE tax)
    - margin_after_tax = spread - tax
    - percent_margin = (margin_after_tax / avg_low_price) * 100

    Note: Both positive and negative margins are stored for analysis.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')
        computed_at: Timestamp for calculation (defaults to now)

    Returns:
        The newly created ArbitrageSnapshot, or None if no price data available

    Raises:
        InvalidWindowError: If window is not '1m' or '5m'
    """
    if window not in ('1m', '5m'):
        raise InvalidWindowError(f"Window must be '1m' or '5m', got '{window}'")

    # Get the most recent non-null price data
    price_snapshot = await price_service.get_latest_non_null_price(db, item_id, window)

    if not price_snapshot:
        # No price data available - this is expected for new items or API outages
        return None

    # Extract prices
    avg_high_price = price_snapshot.avg_high_price
    avg_low_price = price_snapshot.avg_low_price

    # Calculate arbitrage metrics
    spread = avg_high_price - avg_low_price
    tax = int(0.02 * avg_high_price)  # 2% GE tax, rounded down
    margin_after_tax = spread - tax
    percent_margin = (margin_after_tax / avg_low_price) * 100 if avg_low_price > 0 else 0.0

    # Create and store arbitrage snapshot
    if computed_at is None:
        computed_at = datetime.utcnow()

    arbitrage_snapshot = ArbitrageSnapshot(
        item_id=item_id,
        window=window,
        avg_high_price=avg_high_price,
        avg_low_price=avg_low_price,
        spread=spread,
        tax=tax,
        margin_after_tax=margin_after_tax,
        percent_margin=percent_margin,
        high_volume=price_snapshot.high_price_volume,
        low_volume=price_snapshot.low_price_volume,
        computed_at=computed_at
    )

    db.add(arbitrage_snapshot)
    await db.commit()
    await db.refresh(arbitrage_snapshot)

    return arbitrage_snapshot


async def get_top_opportunities(
    db: AsyncSession,
    window: str,
    limit: int = 10,
    min_margin: float = 0.0
) -> list[ArbitrageSnapshot]:
    """
    Get the top arbitrage opportunities sorted by profit margin.

    Args:
        db: Database session
        window: Time window ('1m' or '5m')
        limit: Maximum number of opportunities to return (default: 10, max: 100)
        min_margin: Minimum percent margin to include (default: 0.0)

    Returns:
        List of ArbitrageSnapshot objects, ordered by highest percent_margin first

    Raises:
        InvalidWindowError: If window is not '1m' or '5m'
    """
    if window not in ('1m', '5m'):
        raise InvalidWindowError(f"Window must be '1m' or '5m', got '{window}'")

    # Cap limit at 100
    limit = min(limit, 100)

    # For each item, get the most recent arbitrage snapshot
    # We use a subquery to get the latest computed_at per item_id
    from sqlalchemy import func

    subquery = (
        select(
            ArbitrageSnapshot.item_id,
            func.max(ArbitrageSnapshot.computed_at).label('max_computed_at')
        )
        .where(ArbitrageSnapshot.window == window)
        .group_by(ArbitrageSnapshot.item_id)
        .subquery()
    )

    result = await db.execute(
        select(ArbitrageSnapshot)
        .join(
            subquery,
            and_(
                ArbitrageSnapshot.item_id == subquery.c.item_id,
                ArbitrageSnapshot.computed_at == subquery.c.max_computed_at
            )
        )
        .where(
            and_(
                ArbitrageSnapshot.window == window,
                ArbitrageSnapshot.percent_margin >= min_margin
            )
        )
        .order_by(desc(ArbitrageSnapshot.percent_margin))
        .limit(limit)
    )

    return list(result.scalars().all())


async def get_arbitrage_history(
    db: AsyncSession,
    item_id: int,
    window: str,
    limit: int = 100
) -> list[ArbitrageSnapshot]:
    """
    Get historical arbitrage snapshots for a specific item.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')
        limit: Maximum number of snapshots to return (default: 100, max: 1000)

    Returns:
        List of ArbitrageSnapshot objects, ordered by most recent first

    Raises:
        InvalidWindowError: If window is not '1m' or '5m'
    """
    if window not in ('1m', '5m'):
        raise InvalidWindowError(f"Window must be '1m' or '5m', got '{window}'")

    # Cap limit at 1000
    limit = min(limit, 1000)

    result = await db.execute(
        select(ArbitrageSnapshot)
        .where(
            and_(
                ArbitrageSnapshot.item_id == item_id,
                ArbitrageSnapshot.window == window
            )
        )
        .order_by(desc(ArbitrageSnapshot.computed_at))
        .limit(limit)
    )

    return list(result.scalars().all())
