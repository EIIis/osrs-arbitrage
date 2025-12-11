"""
Custom exception classes for the OSRS Arbitrage application.

These exceptions are raised by service layer functions and should be caught
by the API layer to return appropriate HTTP responses.
"""


class ItemAlreadyExistsError(Exception):
    """Raised when attempting to add an item that's already being tracked."""
    pass


class ItemNotFoundError(Exception):
    """Raised when attempting to access an item that doesn't exist."""
    pass


class NoDataAvailableError(Exception):
    """Raised when no data is available for the requested operation."""
    pass


class InvalidWindowError(Exception):
    """Raised when an invalid time window is specified (must be '1m' or '5m')."""
    pass
