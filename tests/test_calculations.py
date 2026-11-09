from decimal import Decimal

import pytest

from app.core.exceptions import UserError
from app.domain.calculations import amount, net, parse_time, profit, target_for_roi, tax


@pytest.mark.parametrize(
    ("price", "expected"),
    [(49, 0), (50, 1), (99, 1), (100, 2), (249999999, 4999999), (250000000, 5000000), (3000000000, 5000000)],
)
def test_tax_rounding_and_cap(price, expected):
    assert tax(price, 453) == expected


def test_exemptions_and_per_item_tax():
    assert tax(5000, 379) == 0
    assert tax(100000000, 13190) == 0
    assert profit(40, 49, 100, 453) == 900  # not tax on total proceeds


@pytest.mark.parametrize("buy", [1, 49, 50, 100, 100000, 3000000000])
@pytest.mark.parametrize("percent", [Decimal(0), Decimal(".5"), Decimal(5)])
def test_target_is_smallest_price_achieving_goal(buy, percent):
    target = target_for_roi(buy, percent, 453)
    required = Decimal(buy) * (1 + percent / 100)
    assert net(target, 453) >= required
    assert target == 0 or net(target - 1, 453) < required


@pytest.mark.parametrize(
    ("value", "expected"), [("1.5k", 1500), ("2m", 2000000), ("1,234", 1234), ("3.1b", 3100000000)]
)
def test_amounts(value, expected):
    assert amount(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "1.2", "NaN", "1e99", "10x", "99999999999999999999999999999"])
def test_reject_invalid_amounts(value):
    with pytest.raises(UserError):
        amount(value)


def test_time_validation():
    now = 1794268800
    assert parse_time(None, now, "America/Chicago") == now
    assert parse_time("2026-11-09T00:00:00-06:00", now, "America/Chicago") == 1794204000
    with pytest.raises(UserError):
        parse_time("2099-01-01", now, "America/Chicago")
