"""Opt-in checks against the real https://elpriser.org API.

    ELPRISER_LIVE=1 pytest tests/test_live.py -v

Skipped otherwise (CI and a plain ``pytest`` never touch the network). They use only
read-only GETs (about 50 requests, which the API's edge cache absorbs) and assert what
the API documents, not today's prices. They are what keeps the zone table, the parsers
and the recorded fixtures honest.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from elpriser import (
    AsyncElpriser,
    BadRequestError,
    Elpriser,
    PriceKind,
    PriceLevel,
    Zone,
    price_levels,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("ELPRISER_LIVE") != "1", reason="set ELPRISER_LIVE=1 to talk to elpriser.org"),
]


@pytest.fixture(scope="module")
def api() -> Elpriser:
    return Elpriser(user_agent="elpriser-python-tests/live", retries=3)


@pytest.fixture(scope="module")
def openapi(api: Elpriser) -> dict:
    return api.get_json("/api/openapi.json")  # type: ignore[no-any-return]


def test_zone_enum_equals_the_apis_zone_list(openapi):
    enum = openapi["paths"]["/api/nordic"]["get"]["parameters"][0]["schema"]["enum"]
    assert [z.value for z in Zone] == enum


def test_modes_and_strategies_equal_the_apis(openapi):
    from elpriser import Mode, Strategy

    params = {p["name"]: p for p in openapi["paths"]["/api/schedule"]["get"]["parameters"]}
    assert [m.value for m in Mode] == params["mode"]["schema"]["enum"]
    assert [s.value for s in Strategy] == params["strategy"]["schema"]["enum"]


@pytest.mark.parametrize("zone", list(Zone), ids=lambda z: z.value)
def test_every_zone_feed_parses_and_matches_the_zone_table(api, zone):
    s = api.zone_feed(zone)
    assert s.zone is zone
    assert s.currency == zone.currency
    assert s.rate and s.rate > 0 and s.rate_source == "ECB" and s.rate_date
    assert s.unit == ("GBP/MWh" if zone is Zone.GB else "EUR/MWh")
    assert (s.rate == 1.0) == (zone.currency in {"EUR", "GBP"})
    assert s.days and all(23 <= len(d.slots) <= 25 for d in s.days)
    # the zone's own local today is in the answer, so the tz table puts "now" in the right day
    assert s.day(s.local_today()) is not None, f"{zone.value}: no data for local today"
    priced = [sl for sl in s.all_slots() if sl.price is not None]
    assert priced and all(-1.0 < sl.price < 5.0 for sl in priced)  # per kWh in local currency: sanity of the scale
    for sl in priced:
        assert sl.price == pytest.approx(sl.price_mwh * s.rate / 1000, abs=1e-5)
    # forecast days follow the settled ones without a hole
    settled = [d.date for d in s.settled_days]
    forecast = [d.date for d in s.forecast_days]
    assert all(f > max(settled) for f in forecast)
    assert forecast == [max(settled) + timedelta(days=i) for i in range(1, len(forecast) + 1)] or zone.group != "eu"
    for d in s.forecast_days:
        assert all(sl.kind is PriceKind.FORECAST for sl in d.slots)
        if sl := next((x for x in d.slots if x.price is not None), None):
            assert sl.min is not None and sl.max is not None and sl.min <= sl.price <= sl.max


@pytest.mark.parametrize("zone", [Zone.GB, Zone.PT, Zone.IE], ids=lambda z: z.value)
def test_estimate_only_covers_hours_that_have_no_price(api, zone):
    s = api.zone_feed(zone)
    if s.estimate is None:
        pytest.skip("the API serves no estimate right now (none needed, or older than 3 hours)")
    day = s.day(s.estimate.date)
    assert day is not None
    unpriced = {sl.start for sl in day.slots if sl.price is None}
    assert unpriced and {sl.start for sl in s.estimate.slots} <= unpriced
    assert all(sl.kind is PriceKind.ESTIMATE and sl.min <= sl.price <= sl.max for sl in s.estimate.slots)
    assert s.estimate.generated_at is not None


def test_non_estimate_zone_has_no_estimate(api):
    assert api.zone_feed("de").estimate is None


def test_forecast_horizons_and_accuracy(api):
    s = api.series("de")
    assert s.forecast_days
    assert all(d.horizon is not None and 1 <= d.horizon <= 7 for d in s.forecast_days)
    for d in s.forecast_days:
        assert s.accuracy_for(d.horizon) is not None
        assert isinstance(s.is_low_confidence(d.horizon), bool)


def test_kosovo_has_settled_prices_only(api):
    assert api.series("xk").forecast_days == ()


def test_danish_feed_matches_api_now_and_prices(api):
    for _ in range(2):  # retry once if the hour changed between the two requests
        s = api.series("dk1")
        now = api.dk_now("DK1")
        slot = s.now()
        if slot is not None and slot.hour == now.hour:
            break
    assert slot is not None and slot.price == pytest.approx(now.price, abs=1e-4)
    assert slot.kind is PriceKind.ACTUAL
    prices = api.dk_prices("DK1", date=slot.local_date)
    today = s.day(slot.local_date).slots
    assert [round(sl.price, 4) for sl in today] == [round(p.price, 4) for p in prices.prices][: len(today)]
    assert s.mode == "inkl_alt" and s.charges is not None and s.currency == "DKK"


def test_cheapest_hours_agree_with_the_servers_cheapest_n_schedule(api):
    s = api.series("dk1")
    sched = api.dk_schedule("DK1", strategy="cheapest_n", hours=6)
    mine = s.cheapest_hours(6, day=s.local_today())
    assert sorted(round(sl.price, 4) for sl in mine) == sorted(sc.price for sc in sched.schedule if sc.on)
    assert len(sched.on_hours) == 6


def test_levels_equal_the_apis_tibber_levels(api):
    tariff = api.shelly_tariff("DK1")
    assert len(tariff.today) in (23, 24, 25)
    totals = [t.total for t in tariff.today]
    assert price_levels(totals) == [t.level for t in tariff.today]
    assert PriceLevel.VERY_CHEAP in {t.level for t in tariff.today}


def test_net_mode_needs_the_gln_and_returns_grid_tariffs(api):
    s = api.series("dk2", mode="net_inkl_alt", gln="5790000704842")
    assert s.days[0].grid_tariff is not None and len(s.days[0].grid_tariff) == 24
    assert s.days[0].tariff in {"published", "last_known"}


def test_supplier_lookup(api):
    assert api.supplier_lookup(55.6761, 12.5683).found
    none = api.supplier_lookup(0, 0)
    assert not none.found and none.error


def test_api_errors_map_to_exceptions(api):
    with pytest.raises(BadRequestError, match="zone must be one of"):
        api.get_json("/api/nordic", zone="zz")
    with pytest.raises(BadRequestError):
        api.get_json("/api/prices", area="DK1", date="not-a-date")


def test_default_user_agent_is_accepted():
    c = Elpriser()  # elpriser-python/<version>; the CDN rejects only Python-urllib's default
    assert c.series("de").zone is Zone.DE


def test_to_dataframe_on_live_data(api):
    pd = pytest.importorskip("pandas")
    df = api.series("de").to_dataframe()
    assert df.index.is_unique and df.index.is_monotonic_increasing and str(df.index.tz) == "Europe/Berlin"
    assert pd.api.types.is_float_dtype(df["price"].dtype)


async def test_async_client_live():
    pytest.importorskip("httpx")
    async with AsyncElpriser(user_agent="elpriser-python-tests/live") as c:
        s = await c.series("se3")
        assert s.currency == "SEK" and s.unit == "EUR/MWh"
        assert await c.cheapest_hours("se3", 3)
        assert (await c.dk_now()).hour in range(24)
