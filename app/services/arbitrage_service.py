"""
Arbitrage Service - Profit Calculation and Storage.

Technical Explanation:
This module calculates arbitrage opportunities based on Grand Exchange prices.
It computes spreads, GE tax (2%), and profit margins, storing results as
ArbitrageSnapshots for later querying.

Simple Explanation:
This code figures out which items are good for "flipping" - buying low and
selling high. It accounts for the 2% tax the game charges when you sell items,
so the profits shown are what you'd actually make.
"""
import logging
from datetime import datetime
from typing import Optional, List, Tuple

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.arbitrage_snapshot import ArbitrageSnapshot
from app.models.tracked_item import TrackedItem
from app.services.price_service import validate_window

logger = logging.getLogger(__name__)

# Grand Exchange tax rate (2%)
GE_TAX_RATE = 0.02


async def calculate_and_store_arbitrage(
    db: AsyncSession,
    item_id: int,
    window: str,
    avg_high_price: int,
    avg_low_price: int,
    high_volume: Optional[int] = None,
    low_volume: Optional[int] = None
) -> ArbitrageSnapshot:
    """
    Calculate arbitrage metrics and store a snapshot.

    Technical Explanation:
    Formula breakdown:
    - spread = avg_high_price - avg_low_price (raw profit before tax)
    - tax = floor(0.02 * avg_high_price) (2% GE tax on sale, rounded down)
    - margin_after_tax = spread - tax (actual profit per flip)
    - percent_margin = (margin_after_tax / avg_low_price) * 100 (ROI percentage)

    The tax is calculated on the sell price (high price) because that's when
    the GE takes its cut in OSRS.

    Simple Explanation:
    Calculates how much profit you'd make buying an item at the low price
    and selling at the high price. The game takes 2% of your sale, so we
    subtract that to show your real profit.

    Example:
    - Buy at 100 GP, sell at 120 GP
    - Spread: 20 GP
    - Tax: 2 GP (2% of 120)
    - Actual profit: 18 GP
    - Return: 18% on your 100 GP investment

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')
        avg_high_price: Average high (sell) price in GP
        avg_low_price: Average low (buy) price in GP
        high_volume: Trading volume at high price (optional)
        low_volume: Trading volume at low price (optional)

    Returns:
        The created ArbitrageSnapshot
    """
    validate_window(window)

    # Calculate arbitrage metrics
    spread = avg_high_price - avg_low_price
    tax = int(GE_TAX_RATE * avg_high_price)  # Floor (round down)
    margin_after_tax = spread - tax

    # Calculate percentage margin (avoid division by zero)
    if avg_low_price > 0:
        percent_margin = (margin_after_tax / avg_low_price) * 100
    else:
        percent_margin = 0.0

    snapshot = ArbitrageSnapshot(
        item_id=item_id,
        window=window,
        avg_high_price=avg_high_price,
        avg_low_price=avg_low_price,
        spread=spread,
        tax=tax,
        margin_after_tax=margin_after_tax,
        percent_margin=percent_margin,
        high_volume=high_volume,
        low_volume=low_volume,
        computed_at=datetime.utcnow()
    )
    db.add(snapshot)
    # Note: commit is handled by the caller (allows batching)

    logger.debug(
        f"Created arbitrage snapshot for item {item_id} ({window}): "
        f"{percent_margin:.2f}% margin"
    )
    return snapshot


async def get_top_opportunities(
    db: AsyncSession,
    window: str = "5m",
    limit: int = 10,
    min_margin: float = 0.0
) -> List[Tuple[ArbitrageSnapshot, TrackedItem]]:
    """
    Get top arbitrage opportunities sorted by percent margin.

    Technical Explanation:
    Uses a subquery to find the latest arbitrage snapshot per item for the
    given window, then joins with TrackedItem to get item names. Results are
    filtered by min_margin and sorted by percent_margin descending.

    The subquery pattern ensures we only compare the most recent data for
    each item, not historical snapshots against each other.

    Simple Explanation:
    Shows the best money-making opportunities right now. Items are ranked
    by their profit percentage, so a 5% margin on a 100 GP item shows up
    the same as 5% on a 10,000 GP item.

    Args:
        db: Database session
        window: Time window ('1m' or '5m'), defaults to '5m' for stability
        limit: Max results (default 10, max 100)
        min_margin: Minimum percent margin filter (default 0.0, includes negatives)

    Returns:
        List of (ArbitrageSnapshot, TrackedItem) tuples for display
    """
    validate_window(window)
    limit = min(limit, 100)  # Cap at 100

    # Subquery: find the latest computed_at for each item in this window
    latest_subq = (
        select(
            ArbitrageSnapshot.item_id,
            func.max(ArbitrageSnapshot.computed_at).label("max_computed_at")
        )
        .where(ArbitrageSnapshot.window == window)
        .group_by(ArbitrageSnapshot.item_id)
        .subquery()
    )

    # Main query: get full records for latest snapshots, joined with item info
    result = await db.execute(
        select(ArbitrageSnapshot, TrackedItem)
        .join(TrackedItem, ArbitrageSnapshot.item_id == TrackedItem.item_id)
        .join(
            latest_subq,
            (ArbitrageSnapshot.item_id == latest_subq.c.item_id) &
            (ArbitrageSnapshot.computed_at == latest_subq.c.max_computed_at)
        )
        .where(ArbitrageSnapshot.window == window)
        .where(ArbitrageSnapshot.percent_margin >= min_margin)
        .order_by(desc(ArbitrageSnapshot.percent_margin))
        .limit(limit)
    )

    return list(result.all())


async def get_arbitrage_history(
    db: AsyncSession,
    item_id: int,
    window: str,
    limit: int = 100
) -> List[ArbitrageSnapshot]:
    """
    Get arbitrage history for a specific item.

    Technical Explanation:
    Returns historical arbitrage calculations ordered by computed_at descending.
    Useful for seeing how an item's profitability changes over time.

    Simple Explanation:
    Shows how an item's profit margin has changed over time. You can see
    if an item is getting more or less profitable.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')
        limit: Max records (default 100, max 1000)

    Returns:
        List of ArbitrageSnapshots, most recent first
    """
    validate_window(window)
    limit = min(limit, 1000)  # Cap at 1000

    result = await db.execute(
        select(ArbitrageSnapshot)
        .where(ArbitrageSnapshot.item_id == item_id)
        .where(ArbitrageSnapshot.window == window)
        .order_by(desc(ArbitrageSnapshot.computed_at))
        .limit(limit)
    )
    return list(result.scalars().all())
