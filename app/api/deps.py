"""
API Dependencies - Shared Dependencies for Route Handlers.

Technical Explanation:
This module provides FastAPI dependency injection utilities. It includes
database session management and API key authentication. Uses Annotated
type aliases for cleaner route signatures.

Simple Explanation:
This code sets up things that many API endpoints need - like database
connections and checking if someone has the right password (API key)
to do admin stuff.
"""
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.config.settings import get_settings

settings = get_settings()


# Type alias for database session dependency
# Usage in routes: async def my_route(db: DBSession):
DBSession = Annotated[AsyncSession, Depends(get_db)]


async def verify_api_key(
    x_api_key: Annotated[str, Header(description="Admin API key for protected endpoints")]
) -> str:
    """
    Verify the X-API-Key header matches the configured admin key.

    Technical Explanation:
    Uses secrets.compare_digest for timing-safe comparison to prevent
    timing attacks. Returns 401 Unauthorized on failure (not 403 Forbidden)
    as per project requirements.

    Simple Explanation:
    Checks if the API key in the request header matches our secret key.
    If not, rejects the request. Uses a special comparison method that
    doesn't leak information about the key through timing.

    Args:
        x_api_key: API key from X-API-Key header

    Returns:
        The API key if valid

    Raises:
        HTTPException 401: If key is missing or invalid
    """
    # Timing-safe comparison prevents timing attacks
    if not secrets.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "detail": "Invalid or missing API key",
                "error_code": "UNAUTHORIZED"
            },
            headers={"WWW-Authenticate": "ApiKey"}
        )
    return x_api_key


# Type alias for admin-protected routes
# Usage in routes: async def admin_route(db: DBSession, _: AdminKey):
AdminKey = Annotated[str, Depends(verify_api_key)]
