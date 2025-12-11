"""
Price Service - Price Snapshot Management.

Technical Explanation:
This module handles creating and querying price snapshots for tracked items.
It supports both 1-minute and 5-minute time windows and gracefully handles
null price values from the API.

Simple Explanation:
This code saves and retrieves price data for items. Since prices change
constantly, we take "snapshots" at regular intervals. Sometimes the API
doesn't have price data (null values), and that's okay - we store those too.
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_snapshot import PriceSnapshot
from app.core.exceptions import InvalidWindowError

logger = logging.getLogger(__name__)

# Valid time windows
# - 'latest_price': Most recent instant trade prices (from /latest endpoint)
# - '5m': 5-minute average prices (from /5m endpoint)
VALID_WINDOWS = ("latest_price", "5m")


def validate_window(window: str) -> None:
    """
    Validate that a time window parameter is valid.

    Technical Explanation:
    Centralizes window validation to avoid repetition across functions.
    Raises InvalidWindowError immediately if invalid.

    Simple Explanation:
    Checks if the time window is either '1m' or '5m'. If not, throws an error.

    Args:
        window: Time window string to validate

    Raises:
        InvalidWindowError: If window is not '1m' or '5m'
    """
    if window not in VALID_WINDOWS:
        raise InvalidWindowError(f"Window must be one of: {', '.join(VALID_WINDOWS)}")


async def create_price_snapshot(
    db: AsyncSession,
    item_id: int,
    window: str,
    price_data: Dict[str, Any]
) -> PriceSnapshot:
    """
    Store a price snapshot for an item.

    Technical Explanation:
    Accepts nullable price values directly from the API response (via extract_price_data).
    Uses .get() to safely handle missing keys. The snapshot is timestamped with
    the current UTC time.

    Simple Explanation:
    Saves the current prices for an item. If some prices are missing (null),
    that's fine - we still save what we have so we have a complete history.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')
        price_data: Dictionary with price fields (may contain nulls)

    Returns:
        The created PriceSnapshot
    """
    validate_window(window)

    snapshot = PriceSnapshot(
        item_id=item_id,
        window=window,
        avg_high_price=price_data.get("avg_high_price"),
        avg_low_price=price_data.get("avg_low_price"),
        high_price_volume=price_data.get("high_price_volume"),
        low_price_volume=price_data.get("low_price_volume"),
        fetched_at=datetime.utcnow()
    )
    db.add(snapshot)
    # Note: commit is handled by the caller (allows batching)

    logger.debug(f"Created price snapshot for item {item_id} ({window})")
    return snapshot


async def get_latest_prices(
    db: AsyncSession,
    item_id: int,
    window: str
) -> Optional[PriceSnapshot]:
    """
    Get the most recent price snapshot for an item/window.

    Technical Explanation:
    Orders by fetched_at descending and limits to 1. Returns None if no
    data exists (does not raise an exception - caller decides how to handle).

    Simple Explanation:
    Gets the most recent price data we have for an item. Returns None if
    we haven't collected any data yet.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')

    Returns:
        Most recent PriceSnapshot or None if no data exists
    """
    validate_window(window)

    result = await db.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.item_id == item_id)
        .where(PriceSnapshot.window == window)
        .order_by(desc(PriceSnapshot.fetched_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_price_history(
    db: AsyncSession,
    item_id: int,
    window: str,
    limit: int = 100
) -> List[PriceSnapshot]:
    """
    Get price history for an item, most recent first.

    Technical Explanation:
    Returns up to `limit` snapshots ordered by fetched_at descending.
    Limit is capped at 1000 to prevent memory issues on large queries.

    Simple Explanation:
    Gets the price history for an item - like looking at a chart of
    past prices. Most recent prices come first.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')
        limit: Max records to return (default 100, max 1000)

    Returns:
        List of PriceSnapshots, most recent first
    """
    validate_window(window)
    limit = min(limit, 1000)  # Cap at 1000

    result = await db.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.item_id == item_id)
        .where(PriceSnapshot.window == window)
        .order_by(desc(PriceSnapshot.fetched_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_latest_non_null_prices(
    db: AsyncSession,
    item_id: int,
    window: str
) -> Optional[PriceSnapshot]:
    """
    Get the most recent price snapshot with non-null prices.

    Technical Explanation:
    Filters out records where either avg_high_price or avg_low_price is NULL.
    This is essential for arbitrage calculations which require both prices
    to compute spreads and margins.

    Simple Explanation:
    Finds the last time we got complete price data (both buy and sell prices)
    for this item. Used when calculating profit margins since we need both numbers.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('1m' or '5m')

    Returns:
        Most recent complete PriceSnapshot or None if no complete data exists
    """
    validate_window(window)

    result = await db.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.item_id == item_id)
        .where(PriceSnapshot.window == window)
        .where(PriceSnapshot.avg_high_price.isnot(None))
        .where(PriceSnapshot.avg_low_price.isnot(None))
        .order_by(desc(PriceSnapshot.fetched_at))
        .limit(1)
    )
    return result.scalar_one_or_none()
