"""
Background Price Poller - Scheduled Price Fetching.

Technical Explanation:
This module uses APScheduler's AsyncIOScheduler to periodically fetch price
data from the RuneLite API. It stores price snapshots for all tracked items
and calculates arbitrage opportunities when complete price data is available.

Simple Explanation:
This code runs in the background every 5 minutes (by default) to check
current prices for all items we're tracking. It saves the prices and
calculates which items are good for making money. If the price API is
down, it keeps trying but will alert us after 5 failures in a row.
"""
import logging
import sys
from datetime import datetime
from typing import Dict, Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.db.session import AsyncSessionLocal
from app.config.settings import get_settings
from app.services import runelite_api
from app.services.runelite_api import RuneLiteAPIError
from app.services import item_service, price_service, arbitrage_service

logger = logging.getLogger(__name__)
settings = get_settings()

# Track consecutive failures for circuit breaker pattern
_consecutive_failures: int = 0
MAX_CONSECUTIVE_FAILURES: int = 5

# Scheduler instance (singleton)
scheduler: AsyncIOScheduler = AsyncIOScheduler()


async def poll_prices() -> None:
    """
    Main polling job - fetches prices for all tracked items.

    Technical Explanation:
    1. Fetches both latest (instant) and 5m price data from RuneLite API
    2. For each tracked item, stores price snapshots (even if values are null)
    3. For items with complete price data, calculates and stores arbitrage
    4. Tracks consecutive failures - after MAX_CONSECUTIVE_FAILURES, logs critical

    Simple Explanation:
    Checks current prices for all items we're watching, saves the data,
    and calculates which items are good for making money. If the price
    website is down, we note that and try again next time. After too many
    failures in a row, we sound the alarm because something is really wrong.
    """
    global _consecutive_failures

    logger.info("Starting price poll...")
    start_time = datetime.utcnow()

    try:
        # Fetch price data from API (both windows)
        logger.debug("Fetching latest and 5m prices from RuneLite API")
        prices_latest = await runelite_api.fetch_latest_prices()
        prices_5m = await runelite_api.fetch_5m_prices()

        # Create a new database session for this poll
        async with AsyncSessionLocal() as db:
            # Get all tracked items
            items, total = await item_service.list_items(db)
            logger.debug(f"Processing {total} tracked items")

            items_processed = 0
            arbitrage_latest_count = 0
            arbitrage_5m_count = 0

            for item in items:
                item_id = item.item_id

                # Process latest_price window
                arb_latest = await _process_item_window(
                    db, item_id, "latest_price", prices_latest
                )
                if arb_latest:
                    arbitrage_latest_count += 1

                # Process 5m window
                arb_5m = await _process_item_window(
                    db, item_id, "5m", prices_5m
                )
                if arb_5m:
                    arbitrage_5m_count += 1

                items_processed += 1

            # Commit all changes in one transaction
            await db.commit()

        # Success - reset failure counter
        _consecutive_failures = 0

        elapsed = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"Price poll complete: {items_processed} items processed, "
            f"arbitrage calculated for {arbitrage_latest_count} (latest) / {arbitrage_5m_count} (5m) items, "
            f"{elapsed:.2f}s elapsed"
        )

    except RuneLiteAPIError as e:
        _handle_failure(f"RuneLite API error: {e}")

    except Exception as e:
        _handle_failure(f"Unexpected error: {e}")
        logger.exception("Full traceback:")


async def _process_item_window(
    db,
    item_id: int,
    window: str,
    api_prices: Dict[str, Any]
) -> bool:
    """
    Process price data for a single item/window combination.

    Technical Explanation:
    Extracts price data from the API response using the item_id as a string key
    (the API returns string keys). Creates a price snapshot regardless of whether
    data is complete. Only creates arbitrage snapshot if both prices are available.

    Simple Explanation:
    Saves the price data for one item and one time window. If we have both the
    buy and sell prices, we also calculate the profit margin.

    Args:
        db: Database session
        item_id: OSRS item ID
        window: Time window ('latest_price' or '5m')
        api_prices: Full API response dictionary

    Returns:
        True if arbitrage was calculated, False otherwise
    """
    # API returns item IDs as string keys
    item_data = api_prices.get(str(item_id), {})

    # Extract and normalize price data
    price_data = runelite_api.extract_price_data(item_data, window)

    # If no data from API, create empty snapshot
    if price_data is None:
        price_data = {
            "avg_high_price": None,
            "avg_low_price": None,
            "high_price_volume": None,
            "low_price_volume": None,
        }

    # Always store price snapshot (even with null values)
    await price_service.create_price_snapshot(db, item_id, window, price_data)

    # Calculate arbitrage only if we have complete price data
    avg_high = price_data.get("avg_high_price")
    avg_low = price_data.get("avg_low_price")

    if avg_high is not None and avg_low is not None:
        await arbitrage_service.calculate_and_store_arbitrage(
            db,
            item_id=item_id,
            window=window,
            avg_high_price=avg_high,
            avg_low_price=avg_low,
            high_volume=price_data.get("high_price_volume"),
            low_volume=price_data.get("low_price_volume")
        )
        return True

    return False


def _handle_failure(error_message: str) -> None:
    """
    Handle a poll failure - track consecutive failures and alert if needed.

    Technical Explanation:
    Increments the failure counter and checks against MAX_CONSECUTIVE_FAILURES.
    When the threshold is reached, logs a CRITICAL message. The application
    continues running but administrators should be alerted (via log monitoring).

    Simple Explanation:
    Keeps track of how many times the price check has failed in a row.
    After 5 failures, logs a critical error because something is seriously wrong
    and someone needs to look into it.

    Args:
        error_message: Description of what went wrong
    """
    global _consecutive_failures
    _consecutive_failures += 1

    logger.error(f"Poll failure ({_consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}): {error_message}")

    if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        logger.critical(
            f"ALERT: {MAX_CONSECUTIVE_FAILURES} consecutive poll failures! "
            f"The RuneLite API may be down or there's a configuration issue. "
            f"Manual intervention required. Last error: {error_message}"
        )
        # Note: We log critical but don't exit - the scheduler continues trying.
        # In production, this log should trigger an alert via log monitoring.
        # Uncomment the following to actually exit on repeated failures:
        # sys.exit(1)


def start_scheduler() -> None:
    """
    Start the background scheduler.

    Technical Explanation:
    Adds the poll_prices job to the scheduler with an interval trigger.
    The interval is configured via POLL_INTERVAL_MINUTES in settings.
    Uses replace_existing=True to handle restarts gracefully.

    Simple Explanation:
    Starts the background timer that runs the price checker every few minutes.
    """
    scheduler.add_job(
        poll_prices,
        trigger=IntervalTrigger(minutes=settings.POLL_INTERVAL_MINUTES),
        id="price_poller",
        name="Price Polling Job",
        replace_existing=True
    )
    scheduler.start()
    logger.info(
        f"Scheduler started - polling every {settings.POLL_INTERVAL_MINUTES} minutes"
    )


def stop_scheduler() -> None:
    """
    Stop the background scheduler gracefully.

    Technical Explanation:
    Calls shutdown with wait=True to allow running jobs to complete.
    Safe to call even if scheduler isn't running.

    Simple Explanation:
    Stops the background timer cleanly, letting any in-progress work finish.
    """
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped gracefully")


def get_scheduler_status() -> Dict[str, Any]:
    """
    Get current scheduler status for health checks.

    Technical Explanation:
    Returns a dictionary with scheduler state, failure count, and job info.
    Useful for the /health endpoint to expose scheduler health.

    Simple Explanation:
    Returns information about whether the scheduler is running and how it's doing.

    Returns:
        Dictionary with scheduler status information
    """
    jobs_info = []
    for job in scheduler.get_jobs():
        jobs_info.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None
        })

    return {
        "running": scheduler.running,
        "consecutive_failures": _consecutive_failures,
        "max_failures_before_alert": MAX_CONSECUTIVE_FAILURES,
        "poll_interval_minutes": settings.POLL_INTERVAL_MINUTES,
        "jobs": jobs_info
    }


def reset_failure_counter() -> None:
    """
    Reset the consecutive failure counter.

    Technical Explanation:
    Utility function to manually reset the failure counter after
    resolving an issue. Can be called from admin endpoints.

    Simple Explanation:
    Clears the failure count - useful after fixing whatever was causing errors.
    """
    global _consecutive_failures
    _consecutive_failures = 0
    logger.info("Failure counter reset to 0")
