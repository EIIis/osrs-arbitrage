class UserError(ValueError):
    """A safe, actionable message for a command response."""


class MarketError(RuntimeError):
    """The provider is unavailable or returned an invalid contract."""
