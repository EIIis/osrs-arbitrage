import asyncio
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.exceptions import MarketError
from app.services.wiki import WikiClient

NOW = 1794268800
FIXTURE = json.loads((Path(__file__).parent / "fixtures/wiki_v2.json").read_text())


def client(handler):
    return WikiClient(
        "https://prices.example/api/v2/osrs",
        "osrs-test - offline fixture",
        transport=httpx.MockTransport(handler),
    )


async def test_v2_contract_decimal_nulls_and_dynamic_interval():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=FIXTURE[request.url.path.split("/")[-1]])

    wiki = client(handler)
    try:
        catalog = await wiki.catalog()
        assert catalog[1].limit is None
        assert (await wiki.latest(NOW))[453].low == 101
        averages = await wiki.averages(300, NOW)
        assert averages[453].high == Decimal("120.50")
        history = await wiki.history(453, "24h", 300, NOW)
        assert history.interval == 600 and len(history.buckets) == 2
        assert history.buckets[1].high is None
        assert requests[-1].url.params["lookback"] == "24h"
        assert "timestep" not in requests[-1].url.params
        assert requests[0].headers["User-Agent"] == "osrs-test - offline fixture"
        await wiki.history(453, "24h", 300, NOW)
        assert len(requests) == 4
    finally:
        await wiki.close()


async def test_concurrent_history_calls_are_coalesced():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json=FIXTURE["timeseries"])

    wiki = client(handler)
    try:
        results = await asyncio.gather(*(wiki.history(453, "24h", 300, NOW) for _ in range(3)))
        assert count == 1 and results[0] is results[1]
    finally:
        await wiki.close()


async def test_retry_after_and_recovery(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "2"})
            if len(calls) == 1
            else httpx.Response(200, json=FIXTURE["latest"])
        )

    sleep = AsyncMock()
    monkeypatch.setattr("app.services.wiki.asyncio.sleep", sleep)
    wiki = client(handler)
    try:
        assert (await wiki.latest(NOW))[453].high == 121
        sleep.assert_awaited_once_with(2.0)
    finally:
        await wiki.close()


async def test_long_retry_after_does_not_retry_early():
    wiki = client(lambda r: httpx.Response(429, headers={"Retry-After": "3600"}))
    try:
        with pytest.raises(MarketError, match="3600s retry delay"):
            await wiki.latest(NOW)
    finally:
        await wiki.close()


@pytest.mark.parametrize("response", [{"data": []}, {"data": {"453": {"high": -1}}}, "not-json"])
async def test_malformed_payloads_are_errors(response):
    wiki = client(lambda r: httpx.Response(200, json=response))
    try:
        with pytest.raises(MarketError):
            await wiki.latest(NOW)
    finally:
        await wiki.close()


async def test_future_or_wrong_item_history_rejected():
    value = {**FIXTURE["timeseries"], "itemId": 440}
    wiki = client(lambda r: httpx.Response(200, json=value))
    try:
        with pytest.raises(MarketError):
            await wiki.history(453, "24h", 300, NOW)
    finally:
        await wiki.close()


async def test_retry_window_blocks_followup_calls():
    handler = AsyncMock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    wiki = client(handler)
    try:
        with pytest.raises(MarketError, match="retry delay"):
            await wiki.latest(NOW)
        with pytest.raises(MarketError, match="retry window active"):
            await wiki.latest(NOW + 60)
        assert handler.await_count == 1
    finally:
        await wiki.close()
