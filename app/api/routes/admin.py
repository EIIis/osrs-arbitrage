"""
Admin API Routes - Administrative Operations.

Technical Explanation:
Provides protected endpoints for administrative tasks like triggering
manual price refreshes. All endpoints require X-API-Key authentication.

Simple Explanation:
These are special URLs for managing the system. You need the admin API key
to use them. Currently, the main feature is forcing an immediate price update
instead of waiting for the next scheduled one.
"""
import logging
from typing import Callable, Optional

from fastapi import APIRouter, status

from app.api.deps import AdminKey

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Will hold reference to scheduler's poll function
# Injected by main.py during startup to avoid circular imports
_poll_prices_func: Optional[Callable] = None


def set_poll_function(func: Callable) -> None:
    """
    Set the poll function reference.

    Technical Explanation:
    Called by main.py during startup to inject the poll_prices function
    from the scheduler module. This avoids circular imports since admin
    routes don't need to import the scheduler directly.

    Simple Explanation:
    The main app tells this module what function to call when someone
    wants to refresh prices manually.

    Args:
        func: The async poll_prices function from price_poller
    """
    global _poll_prices_func
    _poll_prices_func = func
    logger.debug("Poll function injected into admin routes")


@router.post(
    "/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger price refresh",
    description="Manually trigger an immediate price fetch from the RuneLite API."
)
async def trigger_refresh(_api_key: AdminKey):
    """
    Trigger a manual price refresh.

    Admin endpoint - requires X-API-Key header.

    Runs the price polling job immediately instead of waiting for the
    next scheduled interval. Returns 202 Accepted because the refresh
    happens asynchronously - the response comes back before the refresh
    is complete.

    Use this when you want fresh data right now, or after adding new items.
    """
    if _poll_prices_func is None:
        logger.error("Poll function not initialized - scheduler may not have started")
        return {
            "status": "error",
            "detail": "Scheduler not initialized",
            "error_code": "SCHEDULER_NOT_READY"
        }

    logger.info("Manual price refresh triggered via admin API")

    # Run the poll directly (it's an async function)
    # Note: This runs synchronously within the request, but returns 202
    # to indicate the action was accepted. For true async, we could use
    # BackgroundTasks, but direct execution is simpler for now.
    try:
        await _poll_prices_func()
        return {
            "status": "success",
            "detail": "Price refresh completed successfully"
        }
    except Exception as e:
        logger.error(f"Manual refresh failed: {e}")
        return {
            "status": "error",
            "detail": f"Refresh failed: {str(e)}",
            "error_code": "REFRESH_FAILED"
        }
