"""
OSRS Arbitrage API - Main FastAPI Application.

Technical Explanation:
This is the entry point for the FastAPI application. It configures middleware,
registers routes, sets up lifecycle handlers for the background scheduler,
and provides a global exception handler for custom exceptions.

Simple Explanation:
This is where everything comes together. It starts the web server, connects
all the different parts (routes, scheduler, database), and handles setup/cleanup
when the server starts and stops.
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.api.routes import items_router, arbitrage_router, admin_router
from app.api.routes.admin import set_poll_function
from app.scheduler import (
    poll_prices,
    start_scheduler,
    stop_scheduler,
    get_scheduler_status
)
from app.core.exceptions import OSRSArbitrageError

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown events.

    Technical Explanation:
    Uses the modern FastAPI lifespan pattern (replaces deprecated @app.on_event).
    The code before 'yield' runs on startup, after 'yield' runs on shutdown.
    This ensures proper resource cleanup even if the server crashes.

    Simple Explanation:
    This code runs when the server starts (before yield) and when it stops
    (after yield). It starts the background price checker and makes sure
    to stop it cleanly when shutting down.
    """
    # === STARTUP ===
    logger.info("=" * 50)
    logger.info("Starting OSRS Arbitrage API...")
    logger.info("=" * 50)

    # Inject poll function into admin routes (avoids circular imports)
    set_poll_function(poll_prices)
    logger.debug("Injected poll_prices function into admin routes")

    # Start the background scheduler
    start_scheduler()

    # Run initial price poll on startup
    logger.info("Running initial price poll...")
    try:
        await poll_prices()
        logger.info("Initial price poll completed successfully")
    except Exception as e:
        # Don't fail startup if initial poll fails - scheduler will retry
        logger.error(f"Initial price poll failed (will retry on schedule): {e}")

    logger.info("OSRS Arbitrage API is ready to accept requests")

    yield  # Server runs here, handling requests

    # === SHUTDOWN ===
    logger.info("Shutting down OSRS Arbitrage API...")
    stop_scheduler()
    logger.info("Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="OSRS Arbitrage API",
    description=(
        "API for tracking Old School RuneScape Grand Exchange arbitrage opportunities.\n\n"
        "## Features\n"
        "- Track item prices from RuneLite API\n"
        "- Calculate profit margins after GE tax\n"
        "- Find the best flipping opportunities\n"
        "- View historical price and arbitrage data\n\n"
        "## Authentication\n"
        "- Public endpoints: No authentication required\n"
        "- Admin endpoints: Require `X-API-Key` header"
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)


# === MIDDLEWARE ===

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === EXCEPTION HANDLERS ===

@app.exception_handler(OSRSArbitrageError)
async def osrs_exception_handler(request: Request, exc: OSRSArbitrageError):
    """
    Global exception handler for custom application exceptions.

    Technical Explanation:
    Catches all exceptions that inherit from OSRSArbitrageError and converts
    them to consistent JSON responses using the exception's error_code and
    status_code attributes.

    Simple Explanation:
    When something goes wrong (like item not found), this makes sure the
    error message is returned in a consistent format that clients can
    easily understand and handle.
    """
    logger.warning(f"Application error: {exc.error_code} - {exc.message}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.message,
            "error_code": exc.error_code
        }
    )


# === ROUTES ===

# Include API routers with versioned prefix
app.include_router(items_router, prefix=settings.API_V1_PREFIX)
app.include_router(arbitrage_router, prefix=settings.API_V1_PREFIX)
app.include_router(admin_router, prefix=settings.API_V1_PREFIX)


# === UTILITY ENDPOINTS ===

@app.get("/", tags=["root"])
async def root():
    """
    Root endpoint with API information.

    Returns basic info about the API and links to documentation.
    """
    return {
        "name": "OSRS Arbitrage API",
        "version": "1.0.0",
        "description": "Track Grand Exchange arbitrage opportunities",
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc"
        },
        "health": "/health"
    }


@app.get("/health", tags=["health"])
async def health_check():
    """
    Health check endpoint.

    Technical Explanation:
    Returns server status including scheduler health. Useful for load balancers
    and monitoring systems to verify the service is operational.

    Simple Explanation:
    A quick way to check if the server is running and healthy. Also shows
    the status of the background price checker.
    """
    scheduler_status = get_scheduler_status()

    # Determine overall health
    is_healthy = scheduler_status["running"]
    has_failures = scheduler_status["consecutive_failures"] > 0

    if scheduler_status["consecutive_failures"] >= scheduler_status["max_failures_before_alert"]:
        status = "critical"
    elif has_failures:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "scheduler": scheduler_status
    }
