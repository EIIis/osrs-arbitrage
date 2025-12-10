"""
RuneLite x OSRS Wiki Real-time Prices API Client.

API Documentation: https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices

Technical Explanation:
This module provides an async HTTP client for fetching Grand Exchange price data
from the RuneLite API. It handles error cases, timeouts, and null data gracefully.

Simple Explanation:
This code talks to the RuneLite website to get current prices for OSRS items.
It's like checking a store's website for product prices, but for game items.
"""
import httpx
import logging
from typing import Optional, Dict, Any
from app.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RuneLiteAPIError(Exception):
    """Custom exception for RuneLite API errors."""
    pass


async def fetch_latest_prices() -> Dict[int, Dict[str, Any]]:
    """
    Fetch latest price snapshots for all items.

    Returns:
        Dictionary mapping item_id -> price data
        Example: {453: {"high": 180, "highTime": 1234567890, "low": 175, ...}}

    Raises:
        RuneLiteAPIError: If API request fails
    """
    url = f"{settings.RUNELITE_API_BASE_URL}/latest"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.debug(f"Fetching latest prices from {url}")
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()

            # The API returns {"data": {item_id: {...}, ...}}
            if "data" in data:
                return data["data"]
            else:
                logger.error(f"Unexpected API response format: {data}")
                return {}

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching latest prices: {e.response.status_code}")
        raise RuneLiteAPIError(f"HTTP {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Request error fetching latest prices: {str(e)}")
        raise RuneLiteAPIError(f"Request failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error fetching latest prices: {str(e)}")
        raise RuneLiteAPIError(f"Unexpected error: {str(e)}")


async def fetch_1m_prices() -> Dict[int, Dict[str, Any]]:
    """
    Fetch 1-minute average prices for all items.

    Returns:
        Dictionary mapping item_id -> price data
        Example: {453: {"avgHighPrice": 180, "highPriceVolume": 100, "avgLowPrice": 175, ...}}

    Raises:
        RuneLiteAPIError: If API request fails
    """
    url = f"{settings.RUNELITE_API_BASE_URL}/1m"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.debug(f"Fetching 1m prices from {url}")
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()

            # The API returns {"data": {item_id: {...}, ...}}
            if "data" in data:
                return data["data"]
            else:
                logger.error(f"Unexpected API response format: {data}")
                return {}

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching 1m prices: {e.response.status_code}")
        raise RuneLiteAPIError(f"HTTP {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Request error fetching 1m prices: {str(e)}")
        raise RuneLiteAPIError(f"Request failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error fetching 1m prices: {str(e)}")
        raise RuneLiteAPIError(f"Unexpected error: {str(e)}")


async def fetch_5m_prices() -> Dict[int, Dict[str, Any]]:
    """
    Fetch 5-minute average prices for all items.

    Returns:
        Dictionary mapping item_id -> price data
        Example: {453: {"avgHighPrice": 180, "highPriceVolume": 100, "avgLowPrice": 175, ...}}

    Raises:
        RuneLiteAPIError: If API request fails
    """
    url = f"{settings.RUNELITE_API_BASE_URL}/5m"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.debug(f"Fetching 5m prices from {url}")
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()

            # The API returns {"data": {item_id: {...}, ...}}
            if "data" in data:
                return data["data"]
            else:
                logger.error(f"Unexpected API response format: {data}")
                return {}

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching 5m prices: {e.response.status_code}")
        raise RuneLiteAPIError(f"HTTP {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Request error fetching 5m prices: {str(e)}")
        raise RuneLiteAPIError(f"Request failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error fetching 5m prices: {str(e)}")
        raise RuneLiteAPIError(f"Unexpected error: {str(e)}")


def extract_price_data(item_data: Dict[str, Any], window: str) -> Optional[Dict[str, Optional[int]]]:
    """
    Extract price data from API response for a specific item and window.

    Technical Explanation:
    Normalizes the API response format into a consistent structure. Handles
    null/missing values gracefully by returning None for missing fields.

    Simple Explanation:
    Takes the messy data from the API and organizes it into a clean format
    that our database can understand. If some prices are missing, that's okay.

    Args:
        item_data: Price data from API for a single item
        window: '1m' or '5m'

    Returns:
        Dictionary with normalized price data, or None if data is invalid
    """
    if not item_data:
        return None

    try:
        # Extract prices based on window type
        if window == "1m" or window == "5m":
            return {
                "avg_high_price": item_data.get("avgHighPrice"),
                "avg_low_price": item_data.get("avgLowPrice"),
                "high_price_volume": item_data.get("highPriceVolume"),
                "low_price_volume": item_data.get("lowPriceVolume"),
            }
        else:
            logger.warning(f"Unknown window type: {window}")
            return None

    except Exception as e:
        logger.error(f"Error extracting price data: {str(e)}")
        return None
