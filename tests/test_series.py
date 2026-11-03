"""The questions a PriceSeries answers: now/next/today/tomorrow, cheapest hours, windows, levels."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import load
from elpriser import PriceKind, PriceLevel, Zone, _parse, price_levels

UTC = timezone.utc


def zone_series(zone: Zone, name: str = ""):
    return _parse.parse_zone_feed(load(name or f"zone_{zone.value}"), zone)


def synthetic(day_prices, *, d=date(2026, 6, 10), kind="actual", zone=Zone.DE, rate=1.0, more=()):
    """A series with hand-made hourly EUR/MWh prices (None = unpriced) for one day (+ more days)."""
    days = [
        {"date": d.isoformat(), "type": kind, "prices": [{"hour": h, "eur_mwh": p} for h, p in enumerate(day_prices)]}
    ]
    days.extend(more)
    return _parse.parse_zone_feed({"zoneInfo": {"currency": "EUR", "rate": rate}, "days": days}, zone)


AT = datetime(2026, 11, 3, 7, 20, tzinfo=UTC)  # 08:20 in Berlin, 07:20 in London


# -- current / next -----------------------------------------------------


def test_now_and_next_hour():
    s = zone_series(Zone.DE)
    assert s.now(AT).hour == 8 and s.now(AT).price_mwh == 301.31
    assert s.next_hour(AT).hour == 9 and s.next_hour(AT).price_mwh == 270.94
    assert s.now(AT).kind is PriceKind.ACTUAL


def test_now_accepts_other_timezones_and_rejects_naive():
    s = zone_series(Zone.DE)
    assert s.now(AT.astimezone(timezone(timedelta(hours=-5)))) == s.now(AT)
    with pytest.raises(ValueError, match="timezone-aware"):
        s.now(datetime(2026, 11, 3, 8, 20))


def test_now_none_outside_the_data():
    s = zone_series(Zone.DE)
    assert s.now(datetime(2030, 1, 1, tzinfo=UTC)) is None
    assert s.next_hour(datetime(2030, 1, 1, tzinfo=UTC)) is None


def test_next_hour_crosses_midnight_into_the_forecast_day():
    s = zone_series(Zone.DE)
    nxt = s.next_hour(datetime(2026, 11, 3, 22, 30, tzinfo=UTC))  # 23:30 Berlin
    assert nxt.local_date == date(2026, 11, 4) and nxt.hour == 0 and nxt.kind is PriceKind.FORECAST


def test_gb_current_hour_is_unpriced_until_estimated():
    s = zone_series(Zone.GB)
    assert s.slot_at(AT).price is None
    assert s.now(AT) is None
    est = s.now(AT, estimate=True)
    assert (
        est.kind is PriceKind.ESTIMATE and est.hour == 7 and est.price_mwh == 209.85 and est.min < est.price < est.max
    )
    assert est.price == pytest.approx(0.20985)


def test_gb_latest_known_falls_back_to_the_last_settled_hour():
    s = zone_series(Zone.GB)
    last = s.latest_known(AT)
    assert last.hour == 6 and last.price_mwh == 202.06
    assert s.latest_known(AT + timedelta(hours=4)) is None  # older than max_age (3 h)
    assert s.latest_known(AT + timedelta(hours=4), max_age=timedelta(hours=6)).hour == 6


def test_pt_hour_23_estimate_fills_the_last_hour_only():
    s = zone_series(Zone.PT)
    at = datetime(2026, 11, 3, 23, 10, tzinfo=UTC)  # 23:10 Lisbon (UTC in November)
    assert s.now(at) is None
    assert s.now(at, estimate=True).price_mwh == 252.7
    assert s.now(datetime(2026, 11, 3, 22, 10, tzinfo=UTC), estimate=True).kind is PriceKind.ACTUAL


def test_estimate_never_replaces_a_settled_price():
    s = zone_series(Zone.GB)
    merged = {sl.start: sl for sl in s.all_slots(include_estimate=True)}
    plain = {sl.start: sl for sl in s.all_slots()}
    for start, slot in plain.items():
        if slot.price is not None:
            assert merged[start] == slot
    assert sum(1 for sl in merged.values() if sl.kind is PriceKind.ESTIMATE) == 17


# -- days ----------------------------------------------------------------


def test_today_and_tomorrow_in_the_zones_own_day():
    s = zone_series(Zone.DE)
    assert [sl.hour for sl in s.today(AT)] == list(range(24))
    assert {sl.local_date for sl in s.today(AT)} == {date(2026, 11, 3)}
    tomorrow = s.tomorrow(AT)
    assert {sl.local_date for sl in tomorrow} == {date(2026, 11, 4)}
    assert {sl.kind for sl in tomorrow} == {PriceKind.FORECAST}  # not published yet: a forecast, flagged


def test_today_uses_local_date_not_utc_date():
    s = zone_series(Zone.DE)
    at = datetime(2026, 11, 3, 23, 30, tzinfo=UTC)  # already Nov 4 in Berlin
    assert s.local_today(at) == date(2026, 11, 4)
    assert {sl.local_date for sl in s.today(at)} == {date(2026, 11, 4)}


def test_gb_has_no_published_tomorrow_but_forecast_days():
    s = zone_series(Zone.GB)
    assert {sl.kind for sl in s.tomorrow(AT)} == {PriceKind.FORECAST}
    assert s.day(date(2026, 11, 4)).horizon == 1


def test_xk_tomorrow_absent():
    s = zone_series(Zone.XK)
    assert s.tomorrow(datetime(2026, 11, 3, 12, tzinfo=UTC)) == []
    assert len(s.today(datetime(2026, 11, 3, 12, tzinfo=UTC))) == 24


def test_stats():
    s = synthetic([10, 30, 20, None] + [25] * 20)
    st = s.stats(date(2026, 6, 10))
    assert (st.minimum.hour, st.maximum.hour, st.count) == (0, 1, 23)
    assert st.average == pytest.approx((10 + 30 + 20 + 25 * 20) / 23 / 1000, abs=1e-5)
    assert s.stats(date(2000, 1, 1)) is None


# -- cheapest hours --------------------------------------------------------


def test_cheapest_hours_day_in_time_order():
    s = synthetic([50, 40, 10, 30, 20, 60] + [100] * 18)
    got = s.cheapest_hours(3, day=date(2026, 6, 10))
    assert [sl.hour for sl in got] == [2, 3, 4]
    assert [sl.price_mwh for sl in got] == [10, 30, 20]


def test_cheapest_hours_tie_prefers_earliest():
    s = synthetic([5, 5, 5, 5] + [9] * 20)
    assert [sl.hour for sl in s.cheapest_hours(2, day=date(2026, 6, 10))] == [0, 1]


def test_cheapest_hours_skips_unpriced_and_returns_fewer_when_short():
    s = synthetic([None] * 22 + [7, 3])
    assert [sl.hour for sl in s.cheapest_hours(5, day=date(2026, 6, 10))] == [22, 23]
    assert s.cheapest_hours(0, day=date(2026, 6, 10)) == []


def test_cheapest_hours_default_window_is_the_next_24_hours():
    two_days = [
        {"date": "2026-06-11", "type": "actual", "prices": [{"hour": h, "eur_mwh": 100 + h} for h in range(24)]}
    ]
    s = synthetic([1] * 24, more=two_days)  # Jun 10 is super cheap, Jun 11 dearer
    at = datetime(2026, 6, 10, 10, 30, tzinfo=UTC)  # 12:30 Berlin (CEST): window 12:00 .. 12:00 next day
    got = s.cheapest_hours(2, at=at)
    assert [sl.local_start.strftime("%d %H") for sl in got] == ["10 12", "10 13"]
    got = s.cheapest_hours(3, start=datetime(2026, 6, 10, 20, tzinfo=UTC), end=datetime(2026, 6, 11, 22, tzinfo=UTC))
    assert [sl.price_mwh for sl in got] == [1, 1, 100]  # 22:00 and 23:00 local on Jun 10, then 00:00 on Jun 11


def test_cheapest_hours_settled_only_excludes_forecast():
    s = zone_series(Zone.DE)
    with_fc = s.cheapest_hours(4, at=AT)
    only = s.cheapest_hours(4, at=AT, include_forecast=False)
    assert all(sl.kind is PriceKind.ACTUAL for sl in only)
    assert len(only) == 4
    assert any(sl.kind is PriceKind.FORECAST for sl in with_fc) or with_fc != only


def test_cheapest_hours_validation():
    s = synthetic([1] * 24)
    with pytest.raises(ValueError):
        s.cheapest_hours(-1)
    with pytest.raises(ValueError, match="either day or start/end"):
        s.cheapest_hours(1, day=date(2026, 6, 10), start=datetime(2026, 6, 10, tzinfo=UTC))


def test_cheapest_hours_with_estimate_flag():
    s = zone_series(Zone.GB)
    plain = s.cheapest_hours(3, day=date(2026, 11, 3))
    assert all(sl.kind is PriceKind.ACTUAL for sl in plain) and len(plain) == 3
    mixed = s.cheapest_hours(24, day=date(2026, 11, 3), include_estimate=True)
    assert len(mixed) == 24 and any(sl.kind is PriceKind.ESTIMATE for sl in mixed)


# -- best window -------------------------------------------------------------


def test_best_window_finds_cheapest_consecutive_run():
    s = synthetic([10, 90, 20, 20, 20, 90, 15, 15, 95] + [100] * 15)
    w = s.best_window(3, day=date(2026, 6, 10))
    assert (w.local_start.hour, w.local_end.hour, w.hours) == (2, 5, 3)
    assert w.average == pytest.approx(0.02)
    assert [sl.price_mwh for sl in w.slots] == [20, 20, 20]


def test_best_window_is_not_the_same_as_cheapest_hours():
    s = synthetic([1, 99, 1, 99, 1, 99, 20, 20, 20] + [200] * 15)
    assert [sl.hour for sl in s.cheapest_hours(3, day=date(2026, 6, 10))] == [0, 2, 4]
    assert s.best_window(3, day=date(2026, 6, 10)).local_start.hour == 6


def test_best_window_does_not_bridge_gaps():
    s = synthetic([1, 1, None, 1, 1] + [100] * 19)
    w = s.best_window(3, day=date(2026, 6, 10))  # 2 unpriced: 0-1 and 3-4 cannot be joined, 3-5 is the best run
    assert w.local_start.hour == 3 and w.average == pytest.approx(0.034)
    assert s.best_window(2, day=date(2026, 6, 10)).local_start.hour == 0


def test_best_window_tie_earliest_and_none_when_impossible():
    s = synthetic([4] * 24)
    assert s.best_window(5, day=date(2026, 6, 10)).local_start.hour == 0
    assert s.best_window(25, day=date(2026, 6, 10)) is None
    assert synthetic([None] * 24).best_window(1, day=date(2026, 6, 10)) is None
    with pytest.raises(ValueError):
        s.best_window(0)


def test_best_window_across_midnight():
    nxt = [
        {
            "date": "2026-06-11",
            "type": "actual",
            "prices": [{"hour": h, "eur_mwh": 1 if h < 2 else 500} for h in range(24)],
        }
    ]
    s = synthetic([500] * 22 + [1, 1], more=nxt)
    w = s.best_window(4, start=datetime(2026, 6, 10, 20, tzinfo=UTC), end=datetime(2026, 6, 12, 0, tzinfo=UTC))
    assert (w.local_start.date(), w.local_start.hour, w.local_end.hour) == (date(2026, 6, 10), 22, 2)


# -- DST ------------------------------------------------------------------------


def test_25_hour_day_doubles_hour_2():
    s = synthetic(list(range(24)), d=date(2026, 10, 25), zone=Zone.DK1)  # clocks go back in Denmark
    slots = s.day(date(2026, 10, 25)).slots
    assert len(slots) == 25
    assert [sl.hour for sl in slots[:5]] == [0, 1, 2, 2, 3]
    assert slots[2].price_mwh == slots[3].price_mwh == 2
    assert slots[3].start - slots[2].start == timedelta(hours=1)  # UTC instants: the doubled hour is a real hour
    assert slots[2].local_start.hour == slots[3].local_start.hour == 2
    assert len({sl.start for sl in slots}) == 25


def test_23_hour_day_has_no_hour_2():
    s = synthetic(list(range(24)), d=date(2026, 3, 29), zone=Zone.DK1)
    slots = s.day(date(2026, 3, 29)).slots
    assert len(slots) == 23 and 2 not in [sl.hour for sl in slots]
    assert [sl.hour for sl in slots[:4]] == [0, 1, 3, 4]
    assert slots[1].end == slots[2].start  # no gap on the timeline


def test_window_arithmetic_over_dst_uses_elapsed_time():
    s = synthetic([10] * 24, d=date(2026, 10, 25), zone=Zone.DK1)
    w = s.best_window(25, day=date(2026, 10, 25))
    assert w is not None and w.end - w.start == timedelta(hours=25)


# -- levels ----------------------------------------------------------------------


def test_price_levels_quintiles():
    prices = list(range(1, 11))
    lv = price_levels(prices)
    # the API's cut points are the sorted values at index floor(n*f): 3, 5, 7, 9 for ten prices
    assert lv == (
        [PriceLevel.VERY_CHEAP] * 3
        + [PriceLevel.CHEAP] * 2
        + [PriceLevel.NORMAL] * 2
        + [PriceLevel.EXPENSIVE] * 2
        + [PriceLevel.VERY_EXPENSIVE]
    )


def test_price_levels_missing_and_flat():
    assert price_levels([None, None]) == [PriceLevel.NORMAL, PriceLevel.NORMAL]
    assert price_levels([3, 3, 3, 3, 3]) == [PriceLevel.VERY_CHEAP] * 5  # same as the API: p <= p20
    assert price_levels([1, None, 9])[1] is PriceLevel.NORMAL
    assert price_levels([]) == []


def test_series_levels_per_local_day():
    s = zone_series(Zone.DE)
    levels = s.levels(at=AT)
    assert len(levels) == 24
    assert [lv for _, lv in levels].count(PriceLevel.VERY_CHEAP) >= 4
    cheapest = min(levels, key=lambda t: t[0].price)
    assert cheapest[1] is PriceLevel.VERY_CHEAP
    assert s.level(cheapest[0]) is PriceLevel.VERY_CHEAP
    dearest = max(levels, key=lambda t: t[0].price)
    assert s.level(dearest[0]) is PriceLevel.VERY_EXPENSIVE


def test_level_of_unpriced_slot_is_none():
    s = zone_series(Zone.GB)
    assert s.level(s.slot_at(AT)) is None


def test_dk_levels_equal_the_apis_own_tibber_levels():
    """The Danish feed's day 1 is exactly /api/shelly/tariff's `today`; our levels must agree."""
    s = _parse.parse_dk_forecast(load("dk_forecast"), Zone.DK1, "inkl_alt", None)
    api = _parse.parse_shelly_tariff(load("dk_shelly")).today
    mine = s.levels(date(2026, 11, 3))
    assert [round(sl.price, 4) for sl, _ in mine] == [t.total for t in api]
    assert [lv for _, lv in mine] == [t.level for t in api]


# -- pandas ------------------------------------------------------------------------


def test_to_dataframe():
    pd = pytest.importorskip("pandas")
    s = zone_series(Zone.GB)
    df = s.to_dataframe()
    assert df.index.name == "start" and str(df.index.tz) == "Europe/London"
    assert len(df) == len(s.all_slots(include_estimate=True)) == 24 + 24 + 7 * 24
    assert set(df["kind"]) == {"actual", "estimate", "forecast"}
    assert df["currency"].iloc[0] == "GBP" and df["zone"].iloc[0] == "gb"
    assert df["price"].notna().all()
    est = df[df["kind"] == "estimate"]
    assert len(est) == 17 and est["min"].notna().all()
    assert pd.api.types.is_integer_dtype(df["horizon"].dtype)
    assert df["horizon"].isna().sum() == 48
    plain = s.to_dataframe(include_estimate=False)
    assert plain["price"].isna().sum() == 17
    assert (plain.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()


def test_to_dataframe_dst_index_is_unique_and_ordered():
    pytest.importorskip("pandas")
    df = synthetic(list(range(24)), d=date(2026, 10, 25), zone=Zone.DK1).to_dataframe()
    assert len(df) == 25 and df.index.is_unique and df.index.is_monotonic_increasing
