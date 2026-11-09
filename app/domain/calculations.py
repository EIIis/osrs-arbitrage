"""Exact GP accounting. The tax exemption list lives in a reviewed data file."""

import json
import re
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.exceptions import UserError

TAX_EXEMPT_IDS = frozenset(
    json.loads((Path(__file__).with_name("tax_exemptions.json")).read_text())["item_ids"]
)
MAX_INTEGER = 2**63 - 1


def amount(text: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmb]?)\s*", text.replace(",", "").lower())
    if not match:
        raise UserError("Use a positive whole amount, such as 1250, 1.5k, or 2m.")
    try:
        value = Decimal(match[1]) * {"": 1, "k": 1000, "m": 10**6, "b": 10**9}[match[2]]
        if not value.is_finite() or value != int(value) or not 0 < value <= MAX_INTEGER:
            raise UserError("Amount must be a positive whole number within 64-bit storage limits.")
        return int(value)
    except InvalidOperation as exc:
        raise UserError("Invalid amount.") from exc


def ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def tax(price: int, item_id: int) -> int:
    return 0 if item_id in TAX_EXEMPT_IDS else min(price // 50, 5_000_000)


def net(price: int, item_id: int) -> int:
    return price - tax(price, item_id)


def profit(buy: int, sell: int, quantity: int, item_id: int) -> int:
    return (net(sell, item_id) - buy) * quantity


def target_for_roi(buy: int, percent: Decimal, item_id: int) -> int:
    if not percent.is_finite() or percent < 0:
        raise UserError("Profit target must be a finite nonnegative percentage.")
    required = ceil(Decimal(buy) * (1 + percent / 100))
    if required > MAX_INTEGER:
        raise UserError("Target exceeds supported storage limits.")
    lo, hi = required, min(required + 5_000_000, MAX_INTEGER)
    if net(hi, item_id) < required:
        raise UserError("Target exceeds supported storage limits.")
    while lo < hi:
        mid = (lo + hi) // 2
        if net(mid, item_id) >= required:
            hi = mid
        else:
            lo = mid + 1
    return lo


def parse_time(value: str | None, now: int, zone: str) -> int:
    if not value:
        return now
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(zone))
        result = int(parsed.timestamp())
    except ValueError as exc:
        raise UserError("Use an ISO date/time, for example 2026-11-09T14:30-06:00.") from exc
    if result > now:
        raise UserError("A completed trade cannot have a future time.")
    return result
