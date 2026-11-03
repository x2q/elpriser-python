"""Parsing of recorded API answers (tests/fixtures, recorded 2026-11-03 from the live API)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import load
from elpriser import ElpriserParseError, PriceKind, Zone, _parse


def series(zone: Zone, name: str = ""):
    return _parse.parse_zone_feed(load(name or f"zone_{zone.value.replace('-', '_')}"), zone)


def test_euro_zone_basics():
    s = series(Zone.DE)
    assert (s.currency, s.unit, s.rate, s.rate_source) == ("EUR", "EUR/MWh", 1.0, "ECB")
    assert s.source == "zone-feed"
    assert s.price_unit == "EUR/kWh"
    assert s.days[0].date == date(2026, 11, 1)
    assert [d.kind for d in s.days[:3]] == [PriceKind.ACTUAL] * 3
    assert [d.horizon for d in s.forecast_days] == [2, 3, 4, 5, 6, 7]
    assert all(len(d.slots) == 24 for d in s.days)


def test_price_is_mwh_times_rate_over_1000():
    s = series(Zone.DE)
    slot = s.day(date(2026, 11, 3)).slots[8]
    assert slot.price_mwh == 301.31
    assert slot.price == pytest.approx(0.30131)
    assert slot.hour == 8
    assert slot.kind is PriceKind.ACTUAL
    assert slot.min is None and slot.max is None


def test_forecast_hours_carry_p10_p90():
    s = series(Zone.DE)
    slot = s.day(date(2026, 11, 4)).slots[0]
    assert slot.kind is PriceKind.FORECAST
    assert (slot.price_mwh, slot.min_mwh, slot.max_mwh) == (197.16, 161.45, 276.16)
    assert (slot.min, slot.price, slot.max) == (pytest.approx(0.16145), pytest.approx(0.19716), pytest.approx(0.27616))


def test_local_currency_conversion_uses_the_ecb_rate():
    pl = series(Zone.PL)
    assert (pl.currency, pl.unit, pl.rate, pl.rate_date) == ("PLN", "EUR/MWh", 4.4185, "2026-11-02")
    raw = load("zone_pl")["days"][2]["prices"][12]["eur_mwh"]
    assert pl.day(date(2026, 11, 3)).slots[12].price == pytest.approx(raw * 4.4185 / 1000, abs=1e-5)
    se3 = series(Zone.SE3)
    assert (se3.currency, se3.rate) == ("SEK", 10.9475)
    assert se3.day(date(2026, 11, 3)).slots[3].price > se3.day(date(2026, 11, 3)).slots[3].price_mwh / 1000


def test_zone_tz_is_applied_not_utc():
    s = series(Zone.DE)
    slot = s.day(date(2026, 11, 3)).slots[8]
    assert slot.start == datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
    assert slot.start.utcoffset().total_seconds() == 0  # start/end are UTC
    assert (slot.local_start.hour, slot.local_start.utcoffset().total_seconds()) == (8, 3600)
    assert slot.end - slot.start == timedelta(hours=1)
    pt = series(Zone.PT)
    assert pt.day(date(2026, 11, 3)).slots[8].start == datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)


def test_accuracy_passthrough():
    s = series(Zone.DE)
    assert s.model == "eu-ens-v2" and s.forecast_generated == "2026-11-03" and s.trained_through == "2026-11-01"
    assert s.accuracy_for(2)["mae"] == 19.7
    assert s.is_low_confidence(2) is False
    assert s.accuracy_for(1) is None and s.is_low_confidence(1) is None
    assert series(Zone.PT).is_low_confidence(2) is True


def test_zone_without_forecast():
    s = series(Zone.XK)
    assert s.forecast_days == () and s.accuracy is None and s.trained_through is None
    assert len(s.days) == 3


def test_nordic_zone_forecast_has_no_horizon():
    s = series(Zone.SE3)
    assert len(s.forecast_days) == 8
    assert all(d.horizon is None for d in s.forecast_days)
    assert s.model is None


def test_gb_is_published_in_pounds_despite_the_field_name():
    s = series(Zone.GB)
    assert (s.unit, s.currency, s.rate) == ("GBP/MWh", "GBP", 1.0)
    first = s.day(date(2026, 11, 2)).slots[0]
    assert first.price_mwh == 191.1 and first.price == pytest.approx(0.1911)
    assert s.price_unit == "GBP/kWh"


def test_gb_today_has_unpriced_hours_and_an_estimate():
    s = series(Zone.GB)
    today = s.day(date(2026, 11, 3))
    assert [i for i, sl in enumerate(today.slots) if sl.price is None] == list(range(7, 24))
    assert all(sl.kind is PriceKind.ACTUAL for sl in today.slots)
    est = s.estimate
    assert est is not None
    assert (est.date, est.scope, est.model) == (date(2026, 11, 3), "partial-day", "gb-est-v1")
    assert est.generated_at == datetime(2026, 11, 3, 7, 12, 1, tzinfo=timezone.utc)
    assert [sl.hour for sl in est.slots] == list(range(7, 24))
    assert all(sl.kind is PriceKind.ESTIMATE and sl.min is not None and sl.max is not None for sl in est.slots)
    # days is never changed by the estimate
    assert today.slots[7].price is None
    assert [d.horizon for d in s.forecast_days] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize("zone", [Zone.PT, Zone.IE])
def test_pt_ie_estimate_is_hour_23_of_the_newest_day(zone: Zone):
    s = series(zone)
    est = s.estimate
    assert est is not None and est.model == "hour23-v1"
    assert [sl.hour for sl in est.slots] == [23]
    assert est.method in {"last-settled-hour", "last-settled-hour-plus-typical-step"}
    assert est.accuracy is not None and est.accuracy["hours"] == 1
    assert s.day(est.date).slots[23].price is None


def test_zones_without_estimate():
    assert series(Zone.DE).estimate is None


def test_dk_forecast_feed():
    s = _parse.parse_dk_forecast(load("dk_forecast"), Zone.DK1, "inkl_alt", None)
    assert (s.currency, s.unit, s.source, s.mode, s.rate) == ("DKK", "DKK/kWh", "dk-forecast", "inkl_alt", None)
    assert s.charges == {"sys": 0.072, "trans": 0.043, "afg": 0.008, "vat": 0.25}
    assert s.model["name"] == "v3" and s.forecast_generated == "2026-11-02T12:13:57.958767+00:00"
    assert [d.kind for d in s.days[:2]] == [PriceKind.ACTUAL, PriceKind.FORECAST]
    assert [d.source for d in s.days[1:]][-1] == "heuristic" and s.days[1].source == "model"
    assert s.days[0].weekday == 2 and s.days[0].date.isoweekday() % 7 == 2
    assert all(d.weekday == d.date.isoweekday() % 7 for d in s.days)
    slot = s.days[1].slots[0]
    raw = load("dk_forecast")["days"][1]["prices"][0]
    assert (slot.price, slot.min, slot.max, slot.price_mwh) == (raw["price"], raw["min"], raw["max"], None)
    assert all(sl.min is None for sl in s.days[0].slots)


def test_dk_net_mode_carries_grid_tariff():
    s = _parse.parse_dk_forecast(load("dk_forecast_net"), Zone.DK2, "net_inkl_alt", "5790000704842")
    assert (s.mode, s.gln) == ("net_inkl_alt", "5790000704842")
    day = s.days[0]
    assert day.tariff == "published" and len(day.grid_tariff) == 24 and day.grid_tariff[17] == 0.5449


def test_zone_feed_mismatch_is_rejected():
    with pytest.raises(ElpriserParseError, match="answered"):
        _parse.parse_zone_feed(load("zone_de"), Zone.FR)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "x",
        {},
        {"zoneInfo": {"currency": "EUR", "rate": 1}},
        {"zoneInfo": {"currency": "EUR", "rate": 1}, "days": []},
        {"zoneInfo": {"currency": "EUR", "rate": 0}, "days": [{"date": "2026-11-03", "prices": []}]},
        {"zoneInfo": {"currency": "EU", "rate": 1}, "days": [{"date": "2026-11-03", "prices": []}]},
        {"zoneInfo": {"currency": "EUR", "rate": 1}, "days": [{"date": "garbage", "prices": []}]},
    ],
)
def test_malformed_zone_feed(payload):
    with pytest.raises(ElpriserParseError):
        _parse.parse_zone_feed(payload, Zone.DE)


def test_malformed_hours_become_unpriced_hours():
    payload = {
        "zoneInfo": {"currency": "EUR", "rate": 1},
        "days": [
            {
                "date": "2026-11-03",
                "type": "actual",
                "prices": [
                    {"hour": 0, "eur_mwh": "12"},
                    {"hour": 1, "eur_mwh": True},
                    {"hour": 2, "eur_mwh": float("nan")},
                    {"hour": 3, "eur_mwh": 5},
                    {"hour": 99, "eur_mwh": 1},
                    "junk",
                    {"eur_mwh": 1},
                ],
            }
        ],
    }
    s = _parse.parse_zone_feed(payload, Zone.DE)
    assert [sl.price for sl in s.days[0].slots] == [None, None, None, 0.005]


def test_unknown_fields_are_ignored():
    payload = load("zone_de")
    payload["brand_new_field"] = {"x": 1}
    payload["days"][0]["extra"] = 1
    assert _parse.parse_zone_feed(payload, Zone.DE).days[0].slots[0].price is not None


def test_dk_endpoints():
    now = _parse.parse_dk_now(load("dk_now"))
    assert (now.on, now.price, now.hour, now.area, now.mode, now.strategy) == (
        False,
        2.9896,
        8,
        "DK1",
        "inkl_alt",
        "cheapest_n",
    )
    prices = _parse.parse_dk_prices(load("dk_prices"))
    assert (prices.unit, prices.charges, prices.current_hour, prices.current_price) == (
        "DKK/kWh",
        "historical",
        8,
        2.9896,
    )
    assert len(prices.prices) == 24 and prices.prices[8].price == 2.9896
    sched = _parse.parse_dk_schedule(load("dk_schedule"))
    assert (sched.strategy, sched.param, sched.on_now, sched.on_hours) == ("cheapest_n", 6, False, [0, 1, 2, 3, 4, 5])
    assert len(sched.schedule) == 24


def test_shelly_tariff_and_levels_match_client_side_calculation():
    from elpriser import PriceLevel, price_levels

    tariff = _parse.parse_shelly_tariff(load("dk_shelly"))
    assert len(tariff.today) == 24 and tariff.tomorrow == ()
    first = tariff.today[0]
    assert first.currency == "DKK" and first.level is PriceLevel.VERY_CHEAP
    assert first.starts_at.utcoffset().total_seconds() == 3600
    # our quintile algorithm reproduces the API's own levels exactly
    assert price_levels([t.total for t in tariff.today]) == [t.level for t in tariff.today]


def test_supplier():
    ok = _parse.parse_supplier(load("supplier_ok"))
    assert ok.found and ok.name and ok.address and ok.error is None
    none = _parse.parse_supplier(load("supplier_none"))
    assert not none.found and none.error == "no_address"


def test_shelly_unexpected_shape():
    with pytest.raises(ElpriserParseError):
        _parse.parse_shelly_tariff({"data": {}})


# ── Finland is on Helsinki's clock (elpriser.org cut `fi` into Helsinki days; ground truth: ENTSO-E A44, 2026-11-03) ──


def _fi_feed(days):
    return {
        "zone": "fi",
        "generated": "2026-11-03",
        "trainedThrough": None,
        "unit": "EUR/MWh",
        "zoneInfo": {"name": "FI Finland", "country": "FI", "currency": "EUR", "rate": 1, "rateDate": "2026-11-03", "rateSource": "ECB"},
        "days": [
            {"date": d, "type": "actual", "prices": [{"hour": h, "eur_mwh": v} for h, v in enumerate(vals)]}
            for d, vals in days.items()
        ],
    }


def test_finland_hour_0_is_helsinki_midnight():
    assert Zone.FI.tz == "Europe/Helsinki"
    # ENTSO-E (and Energy-Charts): the 2026-11-03 market day starts at 2026-11-02T23:00Z = 01:00 Helsinki, so the Finnish
    # day 2026-11-03 has 5.77 (the first market hour) at hour 1, and hour 0 is 22:00Z, the hour before.
    values = [4.0, 5.77, 5.11] + [float(h) for h in range(3, 24)]
    s = _parse.parse_zone_feed(_fi_feed({"2026-11-03": values}), Zone.FI)
    slots = s.day(date(2026, 11, 3)).slots
    assert slots[0].start == datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)
    assert slots[1].start == datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)
    assert (slots[1].price_mwh, slots[1].hour) == (5.77, 1)
    assert slots[2].start == datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
    assert slots[23].start == datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
    assert slots[1].start.astimezone(s.tzinfo).hour == 1


def test_finland_days_with_a_clock_change():
    spring = _parse.parse_zone_feed(_fi_feed({"2026-03-29": [float(h) for h in range(24)]}), Zone.FI)
    day = spring.day(date(2026, 3, 29)).slots
    assert len(day) == 23  # Helsinki 03:00 does not exist
    assert day[0].start == datetime(2026, 3, 28, 22, 0, tzinfo=timezone.utc)
    assert [s.hour for s in day][:5] == [0, 1, 2, 4, 5]
    assert day[3].start == datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)  # 04:00 EEST
    fall = _parse.parse_zone_feed(_fi_feed({"2026-10-25": [float(h) for h in range(24)]}), Zone.FI)
    day = fall.day(date(2026, 10, 25)).slots
    assert len(day) == 25  # Helsinki 03:00 happens twice
    assert day[0].start == datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc)
    assert day[3].start == datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc) and day[4].start == datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
    assert day[3].price_mwh == day[4].price_mwh == 3.0
