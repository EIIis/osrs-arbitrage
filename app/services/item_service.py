"""
Item Service - CRUD Operations for Tracked Items.

Technical Explanation:
This module provides async database operations for managing tracked items.
It uses SQLAlchemy async sessions and raises custom exceptions for error cases.

Simple Explanation:
This code handles adding, viewing, and removing items from our tracking list.
Think of it like a shopping list manager - you can add items, see what's on the list,
and remove items you no longer want to track.
"""
import logging
from typing import Tuple, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracked_item import TrackedItem
from app.core.exceptions import ItemAlreadyExistsError, ItemNotFoundError

logger = logging.getLogger(__name__)


async def add_item(db: AsyncSession, item_id: int, item_name: str) -> TrackedItem:
    """
    Add a new item to track.

    Technical Explanation:
    Uses SELECT before INSERT to check uniqueness, avoiding database constraint
    errors and providing a cleaner error message. Commits and refreshes to get
    the auto-generated fields (id, created_at, updated_at).

    Simple Explanation:
    Checks if we're already tracking this item. If not, adds it to our list
    and returns the new entry with all its details.

    Args:
        db: Database session
        item_id: OSRS item ID (from game data)
        item_name: Human-readable item name

    Returns:
        The newly created TrackedItem

    Raises:
        ItemAlreadyExistsError: If item_id is already tracked
    """
    logger.debug(f"Attempting to add item {item_id} ({item_name})")

    # Check if item already exists
    result = await db.execute(
        select(TrackedItem).where(TrackedItem.item_id == item_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        logger.warning(f"Item {item_id} already exists")
        raise ItemAlreadyExistsError(f"Item {item_id} is already being tracked")

    # Create new item
    item = TrackedItem(item_id=item_id, item_name=item_name)
    db.add(item)
    await db.commit()
    await db.refresh(item)

    logger.info(f"Added item {item_id} ({item_name})")
    return item


async def get_item(db: AsyncSession, item_id: int) -> TrackedItem:
    """
    Get a single tracked item by item_id.

    Technical Explanation:
    Queries by item_id (the OSRS game ID), not by the database primary key.
    This is the natural identifier users will use.

    Simple Explanation:
    Looks up an item by its OSRS ID and returns it, or tells you if it's not found.

    Args:
        db: Database session
        item_id: OSRS item ID to look up

    Returns:
        The TrackedItem if found

    Raises:
        ItemNotFoundError: If item doesn't exist
    """
    result = await db.execute(
        select(TrackedItem).where(TrackedItem.item_id == item_id)
    )
    item = result.scalar_one_or_none()

    if not item:
        raise ItemNotFoundError(f"Item {item_id} is not being tracked")

    return item


async def list_items(db: AsyncSession) -> Tuple[List[TrackedItem], int]:
    """
    Get all tracked items with count.

    Technical Explanation:
    Returns both the list and total count in a single call. Count is separate
    from len(items) to support future pagination without additional queries.

    Simple Explanation:
    Returns everything we're tracking, along with how many items that is.

    Args:
        db: Database session

    Returns:
        Tuple of (items list, total count)
    """
    # Get items ordered by name
    result = await db.execute(
        select(TrackedItem).order_by(TrackedItem.item_name)
    )
    items = list(result.scalars().all())

    # Get count (separate query for future pagination support)
    count_result = await db.execute(
        select(func.count()).select_from(TrackedItem)
    )
    total = count_result.scalar() or 0

    logger.debug(f"Listed {total} tracked items")
    return items, total


async def delete_item(db: AsyncSession, item_id: int) -> None:
    """
    Delete a tracked item and all its related data.

    Technical Explanation:
    CASCADE delete is handled by foreign key constraints in the model.
    Deleting a TrackedItem automatically removes all associated
    PriceSnapshots and ArbitrageSnapshots.

    Simple Explanation:
    Removes an item from tracking and deletes all its price history.
    This action cannot be undone.

    Args:
        db: Database session
        item_id: OSRS item ID to delete

    Raises:
        ItemNotFoundError: If item doesn't exist
    """
    logger.debug(f"Attempting to delete item {item_id}")

    # get_item raises ItemNotFoundError if not found
    item = await get_item(db, item_id)

    await db.delete(item)
    await db.commit()

    logger.info(f"Deleted item {item_id} ({item.item_name}) and all related data")
