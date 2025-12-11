"""
Custom Exceptions for OSRS Arbitrage Application.

Technical Explanation:
This module defines a hierarchy of custom exceptions used throughout the application.
Each exception has an error_code and status_code attribute that maps to HTTP responses.
The base class OSRSArbitrageError allows for consistent exception handling in routes.

Simple Explanation:
These are special error types that tell the API what went wrong and what HTTP
status code to return. Instead of generic errors, we use specific ones like
"item not found" or "invalid window" so the client knows exactly what happened.
"""


class OSRSArbitrageError(Exception):
    """
    Base exception for all application errors.

    Technical: Provides error_code and status_code attributes for automatic
    HTTP response generation in the global exception handler.

    Simple: The parent class that all our custom errors inherit from.
    """
    error_code: str = "UNKNOWN_ERROR"
    status_code: int = 500

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class ItemAlreadyExistsError(OSRSArbitrageError):
    """
    Raised when attempting to add an item that already exists.

    Technical: Maps to HTTP 409 Conflict status code.
    Simple: You tried to add an item we're already tracking.
    """
    error_code = "ITEM_ALREADY_EXISTS"
    status_code = 409


class ItemNotFoundError(OSRSArbitrageError):
    """
    Raised when a requested item doesn't exist in the database.

    Technical: Maps to HTTP 404 Not Found status code.
    Simple: The item you're looking for isn't being tracked.
    """
    error_code = "ITEM_NOT_FOUND"
    status_code = 404


class NoDataAvailableError(OSRSArbitrageError):
    """
    Raised when no price or arbitrage data is available.

    Technical: Maps to HTTP 404 Not Found status code.
    Simple: We don't have any price data for this item yet.
    """
    error_code = "NO_DATA_AVAILABLE"
    status_code = 404


class InvalidWindowError(OSRSArbitrageError):
    """
    Raised when an invalid time window is provided.

    Technical: Maps to HTTP 400 Bad Request status code.
    Valid windows are '1m' (1 minute) and '5m' (5 minutes).

    Simple: You gave us a time window we don't support. Use '1m' or '5m'.
    """
    error_code = "INVALID_WINDOW"
    status_code = 400
