"""
Items API Routes - CRUD Endpoints for Tracked Items.

Technical Explanation:
Provides REST endpoints for managing tracked items. Public endpoints for
listing and viewing history, admin-protected endpoints for adding/removing items.

Simple Explanation:
These are the URLs you use to see what items we're tracking, add new items
to track, or stop tracking items. Some actions need an API key (admin only).
"""
import logging
from fastapi import APIRouter, Query, status

from app.api.deps import DBSession, AdminKey
from app.services import item_service, price_service
from app.schemas.tracked_item import (
    TrackedItemCreate,
    TrackedItemResponse,
    TrackedItemListResponse
)
from app.schemas.price_snapshot import PriceHistoryResponse, PriceSnapshotResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/items", tags=["items"])


@router.get(
    "",
    response_model=TrackedItemListResponse,
    summary="List tracked items",
    description="Get all items currently being tracked for arbitrage opportunities."
)
async def list_items(db: DBSession):
    """
    List all tracked items.

    Public endpoint - no authentication required.

    Returns a list of all items we're monitoring with their basic info.
    """
    items, total = await item_service.list_items(db)
    return TrackedItemListResponse(
        items=[TrackedItemResponse.model_validate(item) for item in items],
        total=total
    )


@router.post(
    "",
    response_model=TrackedItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add item to track",
    description="Add a new OSRS item to track for arbitrage opportunities."
)
async def add_item(
    item_data: TrackedItemCreate,
    db: DBSession,
    _api_key: AdminKey
):
    """
    Add a new item to track.

    Admin endpoint - requires X-API-Key header.

    Adds an item to our tracking list. The item_id should be the OSRS
    game item ID (you can find these on the wiki).
    """
    logger.info(f"Adding item {item_data.item_id} ({item_data.item_name})")

    item = await item_service.add_item(
        db,
        item_id=item_data.item_id,
        item_name=item_data.item_name
    )
    return TrackedItemResponse.model_validate(item)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove tracked item",
    description="Stop tracking an item and delete all its price/arbitrage history."
)
async def delete_item(
    item_id: int,
    db: DBSession,
    _api_key: AdminKey
):
    """
    Delete a tracked item and all its data.

    Admin endpoint - requires X-API-Key header.

    This permanently removes the item and ALL its historical data.
    This action cannot be undone.
    """
    logger.info(f"Deleting item {item_id}")
    await item_service.delete_item(db, item_id)
    # 204 No Content - no body returned


@router.get(
    "/{item_id}/history",
    response_model=PriceHistoryResponse,
    summary="Get item price history",
    description="Get historical price snapshots for a specific item."
)
async def get_item_history(
    item_id: int,
    db: DBSession,
    window: str = Query(
        "5m",
        description="Time window for price data ('latest_price' or '5m')",
        pattern="^(latest_price|5m)$"
    ),
    limit: int = Query(
        100,
        ge=1,
        le=1000,
        description="Maximum number of snapshots to return"
    )
):
    """
    Get price history for a specific item.

    Public endpoint.

    Returns historical price snapshots ordered by most recent first.
    Use the 'window' parameter to choose between latest (instant) or 5-minute averages.
    """
    # First verify item exists (raises ItemNotFoundError if not)
    item = await item_service.get_item(db, item_id)

    # Get price history
    snapshots = await price_service.get_price_history(db, item_id, window, limit)

    return PriceHistoryResponse(
        item_id=item_id,
        item_name=item.item_name,
        window=window,
        snapshots=[PriceSnapshotResponse.model_validate(s) for s in snapshots],
        total=len(snapshots)
    )
