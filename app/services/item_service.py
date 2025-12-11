"""
Item service for managing tracked OSRS items.

Provides CRUD operations for items that are being tracked for arbitrage opportunities.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.tracked_item import TrackedItem
from app.core.exceptions import ItemAlreadyExistsError, ItemNotFoundError


async def add_item(db: AsyncSession, item_id: int, item_name: str) -> TrackedItem:
    """
    Add a new item to track for arbitrage opportunities.

    Args:
        db: Database session
        item_id: OSRS item ID
        item_name: Human-readable item name

    Returns:
        The newly created TrackedItem

    Raises:
        ItemAlreadyExistsError: If the item is already being tracked
    """
    # Check if item already exists
    existing_item = await get_item_by_id(db, item_id)
    if existing_item:
        raise ItemAlreadyExistsError(f"Item with ID {item_id} is already being tracked")

    # Create new item
    new_item = TrackedItem(item_id=item_id, item_name=item_name)
    db.add(new_item)
    await db.commit()
    await db.refresh(new_item)

    return new_item


async def get_item_by_id(db: AsyncSession, item_id: int) -> TrackedItem | None:
    """
    Get a tracked item by its OSRS item ID.

    Args:
        db: Database session
        item_id: OSRS item ID

    Returns:
        TrackedItem if found, None otherwise
    """
    result = await db.execute(
        select(TrackedItem).where(TrackedItem.item_id == item_id)
    )
    return result.scalar_one_or_none()


async def get_all_items(db: AsyncSession) -> list[TrackedItem]:
    """
    Get all tracked items.

    Args:
        db: Database session

    Returns:
        List of all TrackedItem objects
    """
    result = await db.execute(select(TrackedItem).order_by(TrackedItem.item_name))
    return list(result.scalars().all())


async def delete_item(db: AsyncSession, item_id: int) -> None:
    """
    Remove an item from tracking.

    Note: This will cascade delete all associated price_snapshots and
    arbitrage_snapshots due to the ON DELETE CASCADE foreign key constraint.

    Args:
        db: Database session
        item_id: OSRS item ID

    Raises:
        ItemNotFoundError: If the item doesn't exist
    """
    item = await get_item_by_id(db, item_id)
    if not item:
        raise ItemNotFoundError(f"Item with ID {item_id} not found")

    await db.delete(item)
    await db.commit()
