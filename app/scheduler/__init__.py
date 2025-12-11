"""
Scheduler Package.

This module exports the scheduler functions for use in main.py.
"""
from app.scheduler.price_poller import (
    poll_prices,
    start_scheduler,
    stop_scheduler,
    get_scheduler_status,
    reset_failure_counter
)

__all__ = [
    "poll_prices",
    "start_scheduler",
    "stop_scheduler",
    "get_scheduler_status",
    "reset_failure_counter"
]
