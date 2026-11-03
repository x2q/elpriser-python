"""The async client (httpx, MockTransport) - same behaviour as the sync one."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl

import pytest

from conftest import fixture_route, load

httpx = pytest.importorskip("httpx")

from elpriser import (  # noqa: E402
    AsyncElpriser,
    BadRequestError,
    ElpriserConnectionError,
    ElpriserParseError,
    ElpriserTimeoutError,
    PriceKind,
    ServerError,
    Zone,
    __version__,
)

AT = datetime(2026, 11, 3, 7, 20, tzinfo=timezone.utc)


def make(handler, **kwargs):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncElpriser(client=http, backoff=0, **kwargs), http


def fixture_handler(calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        r = fixture_route(request.url.path, dict(parse_qsl(request.url.query.decode())))
        return httpx.Response(r.status, headers=dict(r.headers), content=r.body)

    return handler


async def test_series_and_queries():
    calls: list = []
    c, http = make(fixture_handler(calls))
    s = await c.series("de")
    assert s.zone is Zone.DE and s.currency == "EUR"
    assert calls[0].headers["user-agent"] == f"elpriser-python/{__version__}"
    assert calls[0].headers["accept"] == "application/json"
    assert (await c.now("de", at=AT)).price_mwh == 301.31
    assert (await c.next_hour("de", at=AT)).hour == 9
    assert len(await c.today("de", at=AT)) == 24
    assert (await c.tomorrow("de", at=AT))[0].kind is PriceKind.FORECAST
    assert len(await c.cheapest_hours("de", 3, at=AT)) == 3
    assert (await c.best_window("de", 2, at=AT)).hours == 2
    assert [d.horizon for d in await c.forecast("de")] == [2, 3, 4, 5, 6, 7]
    assert len(await c.levels("de", at=AT)) == 24
    assert (await c.now("gb", at=AT, estimate=True)).kind is PriceKind.ESTIMATE
    assert (await c.zone_feed("dk1")).source == "zone-feed"
    assert (await c.series("dk1")).source == "dk-forecast"
    await http.aclose()


async def test_dk_endpoints():
    c, http = make(fixture_handler())
    assert (await c.dk_now()).price == 2.9896
    assert len((await c.dk_prices()).prices) == 24
    assert (await c.dk_schedule(strategy="cheapest_n", hours=6)).on_hours == [0, 1, 2, 3, 4, 5]
    assert len((await c.shelly_tariff()).today) == 24
    assert (await c.supplier_lookup(55.6761, 12.5683)).found
    assert (await c.get_json("/api/now", area="DK1"))["hour"] == 8
    await http.aclose()


async def test_validation_happens_before_any_request():
    calls: list = []
    c, http = make(fixture_handler(calls))
    with pytest.raises(ValueError):
        await c.series("de", mode="spot_ex")
    with pytest.raises(ValueError):
        await c.dk_now(hours=99)
    assert calls == []
    await http.aclose()


async def test_http_error_mapping():
    c, http = make(lambda r: httpx.Response(400, json={"error": "zone must be one of: x"}))
    with pytest.raises(BadRequestError, match="zone must be one of"):
        await c.series("de")
    await http.aclose()


async def test_retry_then_success(monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("elpriser.aclient.asyncio.sleep", fake_sleep)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, json={"error": "down"}, headers={"Retry-After": "4"})
        return httpx.Response(200, json=load("zone_de"))

    c, http = make(handler, retries=2)
    assert (await c.series("de")).zone is Zone.DE
    assert len(attempts) == 2 and sleeps == [4.0]
    await http.aclose()


async def test_server_error_after_retries(monkeypatch):
    async def fake_sleep(s):
        pass

    monkeypatch.setattr("elpriser.aclient.asyncio.sleep", fake_sleep)
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(500, json={"error": "x"})

    c, http = make(handler, retries=2)
    with pytest.raises(ServerError):
        await c.series("de")
    assert len(attempts) == 3
    await http.aclose()


async def test_timeout_and_connection_errors(monkeypatch):
    async def fake_sleep(s):
        pass

    monkeypatch.setattr("elpriser.aclient.asyncio.sleep", fake_sleep)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    c, http = make(slow, retries=1)
    with pytest.raises(ElpriserTimeoutError):
        await c.series("de")
    await http.aclose()
    c, http = make(down, retries=1)
    with pytest.raises(ElpriserConnectionError, match="could not reach"):
        await c.series("de")
    await http.aclose()


async def test_non_json_body():
    c, http = make(lambda r: httpx.Response(200, content=b"nope"))
    with pytest.raises(ElpriserParseError):
        await c.series("de")
    await http.aclose()


async def test_context_manager_closes_only_what_it_owns():
    http = httpx.AsyncClient(transport=httpx.MockTransport(fixture_handler()))
    async with AsyncElpriser(client=http) as c:
        await c.series("xk")
    assert not http.is_closed  # caller's client stays open
    await http.aclose()
    async with AsyncElpriser() as own:
        inner = own._client
    assert inner.is_closed


async def test_option_validation():
    with pytest.raises(ValueError):
        AsyncElpriser(timeout=0)
    with pytest.raises(ValueError):
        AsyncElpriser(base_url="gopher://x")


def test_json_roundtrip_of_default_user_agent():
    assert json.dumps(f"elpriser-python/{__version__}")
