"""
Arbitrage API Routes - Profit Opportunity Endpoints.

Technical Explanation:
Provides endpoints for querying arbitrage opportunities and history.
All endpoints are public - no authentication required.

Simple Explanation:
These URLs show you where the money-making opportunities are in the Grand Exchange.
You can see the best items to flip right now, or check the history for a specific item.
"""
import logging
from datetime import datetime
from fastapi import APIRouter, Query

from app.api.deps import DBSession
from app.services import arbitrage_service, item_service
from app.schemas.arbitrage import (
    ArbitrageTopResponse,
    ArbitrageOpportunityResponse,
    ArbitrageHistoryResponse,
    ArbitrageSnapshotResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/arbitrage", tags=["arbitrage"])


@router.get(
    "/top",
    response_model=ArbitrageTopResponse,
    summary="Get top arbitrage opportunities",
    description="Get the most profitable arbitrage opportunities sorted by percent margin."
)
async def get_top_opportunities(
    db: DBSession,
    window: str = Query(
        "5m",
        description="Time window for price data ('latest_price' or '5m', 5m recommended for stability)",
        pattern="^(latest_price|5m)$"
    ),
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="Maximum number of opportunities to return"
    ),
    min_margin: float = Query(
        0.0,
        description="Minimum percent margin filter (use 0 to include negative margins)"
    )
):
    """
    Get top arbitrage opportunities.

    Public endpoint - shows the best money-making opportunities right now.

    Results are sorted by percent margin (highest first). Use min_margin
    to filter out low-profit items. The 5m window is recommended because
    it's more stable than latest_price data.

    Example: ?window=5m&limit=10&min_margin=1.0
    Returns top 10 items with at least 1% profit margin using 5-minute averages.
    """
    results = await arbitrage_service.get_top_opportunities(
        db, window=window, limit=limit, min_margin=min_margin
    )

    # Convert results to response schema
    opportunities = []
    for snapshot, item in results:
        # Build the response by combining snapshot data with item name
        opp = ArbitrageOpportunityResponse(
            id=snapshot.id,
            item_id=snapshot.item_id,
            item_name=item.item_name,
            window=snapshot.window,
            avg_high_price=snapshot.avg_high_price,
            avg_low_price=snapshot.avg_low_price,
            spread=snapshot.spread,
            tax=snapshot.tax,
            margin_after_tax=snapshot.margin_after_tax,
            percent_margin=snapshot.percent_margin,
            high_volume=snapshot.high_volume,
            low_volume=snapshot.low_volume,
            computed_at=snapshot.computed_at
        )
        opportunities.append(opp)

    return ArbitrageTopResponse(
        opportunities=opportunities,
        count=len(opportunities),
        window=window,
        generated_at=datetime.utcnow()
    )


@router.get(
    "/{item_id}",
    response_model=ArbitrageHistoryResponse,
    summary="Get item arbitrage history",
    description="Get historical arbitrage calculations for a specific item."
)
async def get_arbitrage_history(
    item_id: int,
    db: DBSession,
    window: str = Query(
        "5m",
        description="Time window for calculations ('latest_price' or '5m')",
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
    Get arbitrage history for a specific item.

    Public endpoint.

    Shows how an item's profitability has changed over time. Useful for
    spotting trends - is this item getting more or less profitable?
    """
    # Verify item exists (raises ItemNotFoundError if not)
    item = await item_service.get_item(db, item_id)

    # Get arbitrage history
    snapshots = await arbitrage_service.get_arbitrage_history(
        db, item_id, window, limit
    )

    return ArbitrageHistoryResponse(
        item_id=item_id,
        item_name=item.item_name,
        window=window,
        snapshots=[ArbitrageSnapshotResponse.model_validate(s) for s in snapshots],
        total=len(snapshots)
    )
