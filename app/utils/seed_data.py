"""
Utility script to seed initial tracked items into the database.

Run this after running alembic migrations to add the 5 initial items.

Usage:
    python -m app.utils.seed_data
"""
import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.tracked_item import TrackedItem


# Initial items to track
INITIAL_ITEMS = [
    {"item_id": 453, "item_name": "Coal"},
    {"item_id": 440, "item_name": "Iron ore"},
    {"item_id": 2351, "item_name": "Iron bar"},
    {"item_id": 447, "item_name": "Mithril ore"},
    {"item_id": 2359, "item_name": "Mithril bar"},
]


async def seed_initial_items():
    """Add initial tracked items if they don't exist."""
    async with AsyncSessionLocal() as db:
        for item_data in INITIAL_ITEMS:
            # Check if item already exists
            result = await db.execute(
                select(TrackedItem).where(TrackedItem.item_id == item_data["item_id"])
            )
            existing_item = result.scalar_one_or_none()

            if not existing_item:
                # Create new tracked item
                item = TrackedItem(
                    item_id=item_data["item_id"],
                    item_name=item_data["item_name"]
                )
                db.add(item)
                print(f"Added: {item_data['item_name']} (ID: {item_data['item_id']})")
            else:
                print(f"Already exists: {item_data['item_name']} (ID: {item_data['item_id']})")

        await db.commit()
        print("\nInitial data seeding complete!")


if __name__ == "__main__":
    asyncio.run(seed_initial_items())
