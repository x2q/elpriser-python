"""The sync client against a fake transport serving recorded answers."""

from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlsplit

import pytest

from conftest import FakeTransport, error, fixture_route, load, ok
from elpriser import (
    BadRequestError,
    Elpriser,
    ElpriserConnectionError,
    ElpriserHTTPError,
    ElpriserParseError,
    ElpriserTimeoutError,
    Mode,
    NoDataError,
    PriceKind,
    RateLimitedError,
    ServerError,
    Strategy,
    UnknownZoneError,
    Zone,
    __version__,
    _core,
)
from elpriser._core import Response

AT = datetime(2026, 11, 3, 7, 20, tzinfo=timezone.utc)


def query(url: str) -> dict:
    return dict(parse_qsl(urlsplit(url).query))


def test_user_agent_and_headers(client, transport):
    client.now(Zone.DE, at=AT)
    (url, headers, timeout) = transport.calls[0]
    assert headers["User-Agent"] == f"elpriser-python/{__version__}"
    assert headers["Accept"] == "application/json"
    assert timeout == 10.0
    assert urlsplit(url).netloc == "elpriser.org"


def test_custom_user_agent_timeout_and_base_url():
    t = FakeTransport(route=fixture_route)
    c = Elpriser(base_url="http://localhost:8788/", user_agent="my-app/2.0", timeout=3, transport=t)
    c.series("de")
    url, headers, timeout = t.calls[0]
    assert url == "http://localhost:8788/api/nordic?zone=de"
    assert headers["User-Agent"] == "my-app/2.0" and timeout == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"retries": -1},
        {"backoff": -1},
        {"base_url": "ftp://x"},
        {"base_url": "file:///etc"},
        {"base_url": "elpriser.org"},
    ],
)
def test_constructor_validation(kwargs):
    with pytest.raises(ValueError):
        Elpriser(**kwargs)


def test_non_danish_zone_uses_the_zone_feed(client, transport):
    s = client.series(Zone.SE3)
    assert urlsplit(transport.urls[0]).path == "/api/nordic" and query(transport.urls[0]) == {"zone": "se3"}
    assert s.currency == "SEK"


def test_danish_zone_defaults_to_tariff_aware_forecast_feed(client, transport):
    s = client.series("dk1")
    assert urlsplit(transport.urls[0]).path == "/api/forecast"
    assert query(transport.urls[0]) == {"area": "DK1", "mode": "inkl_alt"}
    assert s.source == "dk-forecast" and s.currency == "DKK"


def test_danish_mode_and_gln(client, transport):
    s = client.series(Zone.DK2, mode=Mode.NET_INKL_ALT, gln="5790000704842")
    assert query(transport.urls[0]) == {"area": "DK2", "mode": "net_inkl_alt", "gln": "5790000704842"}
    assert s.mode == "net_inkl_alt" and s.gln == "5790000704842" and s.days[0].grid_tariff
    client.series("dk1", mode="spot_ex")
    assert query(transport.urls[1])["mode"] == "spot_ex"


def test_zone_feed_for_a_danish_zone(client, transport):
    s = client.zone_feed("dk1")
    assert query(transport.urls[0]) == {"zone": "dk1"}
    assert (s.unit, s.currency, s.rate, s.source) == ("EUR/MWh", "DKK", 7.4737, "zone-feed")


@pytest.mark.parametrize(
    ("args", "kwargs", "match"),
    [
        (("de",), {"mode": "spot_ex"}, "only apply to the Danish"),
        (("de",), {"gln": "5790000704842"}, "only apply to the Danish"),
        (("dk1",), {"mode": "net_inkl_alt"}, "needs gln"),
        (("dk1",), {"mode": "bogus"}, "unknown mode"),
    ],
)
def test_invalid_parameters_fail_before_any_request(client, transport, args, kwargs, match):
    with pytest.raises(ValueError, match=match):
        client.series(*args, **kwargs)
    assert transport.calls == []


def test_unknown_zone_fails_before_any_request(client, transport):
    with pytest.raises(UnknownZoneError):
        client.now("narnia")
    assert transport.calls == []


def test_convenience_methods(client):
    assert client.now("de", at=AT).price_mwh == 301.31
    assert client.next_hour("de", at=AT).hour == 9
    assert len(client.today("de", at=AT)) == 24
    assert client.tomorrow("de", at=AT)[0].kind is PriceKind.FORECAST
    expected = sorted(client.today("de", at=AT), key=lambda x: (x.price, x.start))[:2]
    assert client.cheapest_hours("de", 2, day=date(2026, 11, 3)) == sorted(expected, key=lambda x: x.start)
    assert client.best_window("de", 3, day=date(2026, 11, 3)).hours == 3
    days = client.forecast("de")
    assert [d.horizon for d in days] == [2, 3, 4, 5, 6, 7] and all(d.is_forecast for d in days)
    assert len(client.levels("de", at=AT)) == 24


def test_gb_now_needs_estimate_flag(client):
    assert client.now("gb", at=AT) is None
    assert client.now("gb", at=AT, estimate=True).kind is PriceKind.ESTIMATE


def test_dk_endpoints(client, transport):
    now = client.dk_now("DK1", strategy=Strategy.CHEAPEST_N, hours=6)
    assert (now.price, now.hour) == (2.9896, 8)
    assert query(transport.urls[-1]) == {"area": "DK1", "mode": "inkl_alt", "strategy": "cheapest_n", "hours": "6"}
    prices = client.dk_prices(Zone.DK2, date=date(2026, 11, 3), charges="current")
    assert len(prices.prices) == 24
    assert query(transport.urls[-1]) == {"area": "DK2", "mode": "inkl_alt", "date": "2026-11-03", "charges": "current"}
    sched = client.dk_schedule("dk1", strategy="cheapest_n", hours=6, date="2026-11-03")
    assert sched.on_hours == [0, 1, 2, 3, 4, 5]
    assert query(transport.urls[-1])["date"] == "2026-11-03" and path(transport.urls[-1]) == "/api/schedule"
    tariff = client.shelly_tariff()
    assert len(tariff.today) == 24 and path(transport.urls[-1]) == "/api/shelly/tariff"
    sup = client.supplier_lookup(55.6761, 12.5683)
    assert sup.found and query(transport.urls[-1]) == {"lat": "55.6761", "lng": "12.5683"}


def path(url: str) -> str:
    return urlsplit(url).path


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda c: c.dk_now("DK3"), "area must be DK1 or DK2"),
        (lambda c: c.dk_now(strategy="nope"), "unknown strategy"),
        (lambda c: c.dk_now(hours=24), "hours must be"),
        (lambda c: c.dk_now(pct=0), "pct must be"),
        (lambda c: c.dk_schedule(max_off=13), "max_off must be"),
        (lambda c: c.dk_prices(date="03-11-2026"), "YYYY-MM-DD"),
        (lambda c: c.dk_prices(charges="x"), "charges must be"),
        (lambda c: c.dk_prices(mode="net_inkl_tarif"), "needs gln"),
        (lambda c: c.supplier_lookup(95, 0), "lat must be"),
        (lambda c: c.supplier_lookup(0, "x"), "lng must be"),
    ],
)
def test_dk_parameter_validation(client, transport, call, match):
    with pytest.raises(ValueError, match=match):
        call(client)
    assert transport.calls == []


def test_get_json_escape_hatch(client, transport):
    assert client.get_json("/api/now", area="DK1", mode=None)["hour"] == 8
    assert query(transport.urls[0]) == {"area": "DK1"}


def test_context_manager():
    with Elpriser(transport=FakeTransport(route=fixture_route)) as c:
        assert c.series("xk").zone is Zone.XK


# -- errors ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "exc"),
    [(400, BadRequestError), (404, NoDataError), (403, ElpriserHTTPError), (418, ElpriserHTTPError)],
)
def test_http_errors_are_not_retried(status, exc):
    t = FakeTransport(error(status, "nope"))
    c = Elpriser(transport=t, backoff=0)
    with pytest.raises(exc) as info:
        c.series("de")
    assert info.value.status == status and info.value.message == "nope" and "nord" in info.value.url
    assert len(t.calls) == 1


def test_api_error_message_is_surfaced():
    t = FakeTransport(error(400, "zone must be one of: dk1, dk2"))
    with pytest.raises(BadRequestError, match="zone must be one of"):
        Elpriser(transport=t).series("de")


def test_non_json_error_body_is_summarised():
    t = FakeTransport(Response(403, {}, b"<html>Cloudflare says no</html>"))
    with pytest.raises(ElpriserHTTPError, match="Cloudflare says no"):
        Elpriser(transport=t).series("de")


def test_retries_5xx_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr("elpriser.client.time.sleep", sleeps.append)
    t = FakeTransport(error(503), error(502), ok(load("zone_de")))
    c = Elpriser(transport=t, retries=2, backoff=0.5)
    assert c.series("de").zone is Zone.DE
    assert len(t.calls) == 3 and sleeps == [0.5, 1.0]


def test_gives_up_after_retries_with_server_error(monkeypatch):
    monkeypatch.setattr("elpriser.client.time.sleep", lambda s: None)
    t = FakeTransport(error(503, "down", {"retry-after": "7"}))
    with pytest.raises(ServerError) as info:
        Elpriser(transport=t, retries=2).series("de")
    assert len(t.calls) == 3 and info.value.status == 503 and info.value.retry_after == 7


def test_retry_after_is_honoured_and_capped(monkeypatch):
    sleeps = []
    monkeypatch.setattr("elpriser.client.time.sleep", sleeps.append)
    t = FakeTransport(
        error(429, "slow down", {"retry-after": "3"}),
        error(429, "slow down", {"retry-after": "999"}),
        ok(load("zone_de")),
    )
    Elpriser(transport=t, retries=2, backoff=0.1).series("de")
    assert sleeps == [3.0, _core.MAX_RETRY_DELAY]


def test_rate_limit_error_after_retries(monkeypatch):
    monkeypatch.setattr("elpriser.client.time.sleep", lambda s: None)
    t = FakeTransport(error(429, "slow down", {"retry-after": "2"}))
    with pytest.raises(RateLimitedError) as info:
        Elpriser(transport=t, retries=1).series("de")
    assert info.value.retry_after == 2.0 and len(t.calls) == 2


def test_retries_zero_means_one_attempt():
    t = FakeTransport(error(500))
    with pytest.raises(ServerError):
        Elpriser(transport=t, retries=0).series("de")
    assert len(t.calls) == 1


def test_timeout_is_retried_then_raised(monkeypatch):
    monkeypatch.setattr("elpriser.client.time.sleep", lambda s: None)
    t = FakeTransport(TimeoutError("read timed out"))
    with pytest.raises(ElpriserTimeoutError) as info:
        Elpriser(transport=t, retries=2, timeout=1.5).series("de")
    assert len(t.calls) == 3 and "1.5s" in str(info.value)
    assert isinstance(info.value, ElpriserConnectionError) and isinstance(info.value.__cause__, TimeoutError)


def test_connection_error_is_retried_then_raised(monkeypatch):
    monkeypatch.setattr("elpriser.client.time.sleep", lambda s: None)
    t = FakeTransport(ConnectionResetError("reset"), ConnectionResetError("reset"), ok(load("zone_de")))
    assert Elpriser(transport=t, retries=2).series("de").zone is Zone.DE
    t = FakeTransport(OSError("name resolution failed"))
    with pytest.raises(ElpriserConnectionError, match="could not reach"):
        Elpriser(transport=t, retries=1).series("de")


def test_invalid_json_is_a_parse_error():
    t = FakeTransport(Response(200, {}, b"<html>captive portal</html>"))
    with pytest.raises(ElpriserParseError, match="did not return JSON"):
        Elpriser(transport=t).series("de")


def test_wrong_shape_is_a_parse_error():
    t = FakeTransport(ok({"zone": "de", "unexpected": True}))
    with pytest.raises(ElpriserParseError):
        Elpriser(transport=t).series("de")


def test_all_errors_share_a_base():
    from elpriser import ElpriserError

    for cls in (
        BadRequestError,
        NoDataError,
        RateLimitedError,
        ServerError,
        ElpriserTimeoutError,
        ElpriserParseError,
        UnknownZoneError,
    ):
        assert issubclass(cls, ElpriserError)


def test_urllib_transport_end_to_end(tmp_path):
    """The default transport against a real local HTTP server: headers, JSON, error mapping."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["ua"] = self.headers.get("User-Agent")
            seen["path"] = self.path
            if self.path.startswith("/api/nordic?zone=de"):
                body = json.dumps(load("zone_de")).encode()
                self.send_response(200)
            else:
                body = json.dumps({"error": "zone must be one of: x"}).encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        c = Elpriser(base_url=f"http://127.0.0.1:{server.server_port}", retries=0)
        assert c.series("de").zone is Zone.DE
        assert seen["ua"] == f"elpriser-python/{__version__}" and seen["path"] == "/api/nordic?zone=de"
        with pytest.raises(BadRequestError):
            c.get_json("/api/nordic", zone="zz")
    finally:
        server.shutdown()
        server.server_close()


def test_urllib_transport_connection_refused():
    c = Elpriser(base_url="http://127.0.0.1:9", retries=0, timeout=2)
    with pytest.raises(ElpriserConnectionError):
        c.series("de")
