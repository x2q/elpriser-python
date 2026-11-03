"""Turn raw API JSON into the objects of :mod:`elpriser.models`. Pure functions, no I/O.

The parsers are strict about the structure (a missing ``days`` list or ``zoneInfo``
is an :class:`ElpriserParseError`) and lenient about everything else: unknown
fields are ignored, and a malformed hour becomes an unpriced hour rather than an
error.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from .errors import ElpriserParseError
from .models import (
    DECIMALS,
    HOUR,
    Day,
    DkNow,
    DkPrices,
    DkSchedule,
    Estimate,
    HourPrice,
    PriceKind,
    PriceLevel,
    PriceSeries,
    ScheduleSlot,
    Slot,
    Supplier,
    TariffDays,
    TariffPrice,
)
from .zones import Zone


def _num(value: Any) -> Optional[float]:
    """A finite number or ``None``. Booleans and strings are not prices."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, DECIMALS)


def _need_mapping(payload: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ElpriserParseError(f"{what}: response is not a JSON object")
    return payload


def _parse_dt(value: Any) -> Optional[datetime]:
    text = _str(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_day_starts(day: date, tz: ZoneInfo) -> List[datetime]:
    """UTC start of every hour of the local calendar day (23, 24 or 25 of them)."""
    start = datetime.combine(day, time(0), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(timezone.utc)
    count = round((end - start) / HOUR)
    return [start + i * HOUR for i in range(count)]


def expand_day(
    day: date,
    tz: ZoneInfo,
    rows: Iterable[Any],
    *,
    price_key: str,
    min_key: str,
    max_key: str,
    scale: float,
    kind: PriceKind,
    mwh: bool,
) -> List[Slot]:
    """One API day (rows keyed by local hour) as UTC-anchored hours.

    Daylight saving: on a 25-hour day the API carries one value for the doubled local
    hour 2, and both 02:00 hours get it; on a 23-hour day there is no hour 2 and no slot.
    A row with a null price still yields a slot (``price=None``) so the day keeps its shape.
    """
    by_hour: Dict[int, Mapping[str, Any]] = {}
    for row in rows:
        hour = row.get("hour") if isinstance(row, Mapping) else None
        if isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 23:
            by_hour[hour] = row
    slots: List[Slot] = []
    for start_utc in local_day_starts(day, tz):
        row = by_hour.get(start_utc.astimezone(tz).hour)
        if row is None:
            continue
        value = _num(row.get(price_key))
        low = _num(row.get(min_key)) if value is not None else None
        high = _num(row.get(max_key)) if value is not None else None
        slots.append(
            Slot(
                start=start_utc,
                end=start_utc + HOUR,
                price=_round(None if value is None else value * scale),
                kind=kind,
                min=_round(None if low is None else low * scale),
                max=_round(None if high is None else high * scale),
                price_mwh=value if mwh else None,
                min_mwh=low if mwh else None,
                max_mwh=high if mwh else None,
                tz=tz,
            )
        )
    return slots


def _grid_tariff(value: Any) -> Optional[Tuple[Optional[float], ...]]:
    if not isinstance(value, list):
        return None
    return tuple(_num(v) for v in value)


def _parse_days(
    payload: Mapping[str, Any], tz: ZoneInfo, *, price_key: str, min_key: str, max_key: str, scale: float, mwh: bool
) -> Tuple[Day, ...]:
    days = payload.get("days")
    if not isinstance(days, list) or not days:
        raise ElpriserParseError("response has no days")
    out: Dict[date, Day] = {}
    for entry in days:
        if not isinstance(entry, Mapping):
            continue
        try:
            the_date = date.fromisoformat(str(entry.get("date")))
        except ValueError:
            continue
        rows = entry.get("prices")
        if not isinstance(rows, list):
            continue
        kind = PriceKind.FORECAST if entry.get("type") == "forecast" else PriceKind.ACTUAL
        horizon = entry.get("horizon")
        weekday = entry.get("weekday")
        out[the_date] = Day(
            date=the_date,
            kind=kind,
            slots=tuple(
                expand_day(
                    the_date,
                    tz,
                    rows,
                    price_key=price_key,
                    min_key=min_key,
                    max_key=max_key,
                    scale=scale,
                    kind=kind,
                    mwh=mwh,
                )
            ),
            horizon=horizon if isinstance(horizon, int) and not isinstance(horizon, bool) else None,
            weekday=weekday if isinstance(weekday, int) and not isinstance(weekday, bool) else None,
            source=_str(entry.get("source")),
            tariff=_str(entry.get("tariff")),
            grid_tariff=_grid_tariff(entry.get("grid_tariff")),
        )
    if not out:
        raise ElpriserParseError("response has no usable days")
    return tuple(out[d] for d in sorted(out))


def _parse_estimate(raw: Any, tz: ZoneInfo, scale: float) -> Optional[Estimate]:
    if not isinstance(raw, Mapping):
        return None
    try:
        the_date = date.fromisoformat(str(raw.get("date")))
    except ValueError:
        return None
    rows = raw.get("prices")
    if not isinstance(rows, list):
        return None
    slots = expand_day(
        the_date,
        tz,
        rows,
        price_key="eur_mwh",
        min_key="min_eur_mwh",
        max_key="max_eur_mwh",
        scale=scale,
        kind=PriceKind.ESTIMATE,
        mwh=True,
    )
    accuracy = raw.get("accuracy")
    return Estimate(
        date=the_date,
        scope=_str(raw.get("scope")) or "partial-day",
        model=_str(raw.get("model")),
        generated_at=_parse_dt(raw.get("generatedAt")),
        slots=tuple(slots),
        method=_str(raw.get("method")),
        accuracy=accuracy if isinstance(accuracy, Mapping) else None,
    )


def parse_zone_feed(payload: Any, zone: Zone) -> PriceSeries:
    """``GET /api/nordic?zone=<zone>``: hourly ``eur_mwh`` plus ``zoneInfo`` with the ECB rate.

    Despite the field name ``eur_mwh`` the unit is whatever ``unit`` says (``GBP/MWh`` for ``gb``);
    local currency per kWh is ``value * zoneInfo.rate / 1000`` (``rate`` is 1 for ``gb``).
    """
    data = _need_mapping(payload, "zone feed")
    echoed = data.get("zone")
    if isinstance(echoed, str) and echoed.lower() != zone.value:
        raise ElpriserParseError(f"asked for zone {zone.value!r} but the API answered {echoed!r}")
    info = data.get("zoneInfo")
    if not isinstance(info, Mapping):
        raise ElpriserParseError("response has no zoneInfo")
    currency = info.get("currency")
    if not isinstance(currency, str) or len(currency) != 3:
        raise ElpriserParseError("zoneInfo has no currency")
    rate = _num(info.get("rate"))
    if rate is None or rate <= 0:
        raise ElpriserParseError("zoneInfo has no usable rate")
    tz = ZoneInfo(zone.tz)
    scale = rate / 1000
    days = _parse_days(
        data, tz, price_key="eur_mwh", min_key="min_eur_mwh", max_key="max_eur_mwh", scale=scale, mwh=True
    )
    unit = _str(data.get("unit")) or "EUR/MWh"
    accuracy = data.get("accuracy")
    return PriceSeries(
        zone=zone,
        currency=currency.upper(),
        unit=unit,
        days=days,
        estimate=_parse_estimate(data.get("estimate"), tz, scale),
        source="zone-feed",
        rate=rate,
        rate_date=_str(info.get("rateDate")),
        rate_source=_str(info.get("rateSource")),
        generated=_str(data.get("generated")),
        trained_through=_str(data.get("trainedThrough")),
        model=_str(data.get("model")),
        forecast_generated=_str(data.get("forecastGenerated")),
        accuracy=accuracy if isinstance(accuracy, Mapping) else None,
        raw=data,
    )


def parse_dk_forecast(payload: Any, zone: Zone, mode: str, gln: Optional[str]) -> PriceSeries:
    """``GET /api/forecast?area=DK1|DK2&mode=...``: DKK/kWh in the requested mode, today + 9 days."""
    data = _need_mapping(payload, "forecast")
    if zone.area is None:
        raise ValueError(f"{zone.value} is not a Danish zone")
    tz = ZoneInfo(zone.tz)
    days = _parse_days(data, tz, price_key="price", min_key="min", max_key="max", scale=1.0, mwh=False)
    model = data.get("model")
    charges = data.get("charges")
    return PriceSeries(
        zone=zone,
        currency="DKK",
        unit="DKK/kWh",
        days=days,
        source="dk-forecast",
        mode=_str(data.get("mode")) or mode,
        gln=gln,
        generated=_str(data.get("generated")),
        model=dict(model) if isinstance(model, Mapping) else None,
        forecast_generated=_str(model.get("generatedAt")) if isinstance(model, Mapping) else None,
        charges=charges if isinstance(charges, Mapping) else None,
        raw=data,
    )


# -- Danish single-purpose endpoints ---------------------------------------


def _need(data: Mapping[str, Any], key: str, what: str) -> Any:
    if key not in data:
        raise ElpriserParseError(f"{what}: missing {key!r}")
    return data[key]


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_dk_now(payload: Any) -> DkNow:
    data = _need_mapping(payload, "now")
    hour = _int(_need(data, "hour", "now"))
    if hour is None:
        raise ElpriserParseError("now: hour is not an integer")
    return DkNow(
        on=bool(data.get("on")),
        price=_num(data.get("price")),
        hour=hour,
        area=str(data.get("area")),
        mode=str(data.get("mode")),
        strategy=str(data.get("strategy")),
    )


def _hour_prices(rows: Any, what: str) -> Tuple[HourPrice, ...]:
    if not isinstance(rows, list):
        raise ElpriserParseError(f"{what}: prices is not a list")
    out: List[HourPrice] = []
    for row in rows:
        if isinstance(row, Mapping) and _int(row.get("hour")) is not None:
            out.append(HourPrice(hour=row["hour"], price=_num(row.get("price"))))
    return tuple(out)


def parse_dk_prices(payload: Any) -> DkPrices:
    data = _need_mapping(payload, "prices")
    return DkPrices(
        area=str(data.get("area")),
        mode=str(data.get("mode")),
        date=str(data.get("date")),
        unit=_str(data.get("unit")) or "DKK/kWh",
        charges=_str(data.get("charges")),
        prices=_hour_prices(data.get("prices"), "prices"),
        current_hour=_int(data.get("current_hour")),
        current_price=_num(data.get("current_price")),
    )


def parse_dk_schedule(payload: Any) -> DkSchedule:
    data = _need_mapping(payload, "schedule")
    rows = data.get("schedule")
    if not isinstance(rows, list):
        raise ElpriserParseError("schedule: schedule is not a list")
    slots = tuple(
        ScheduleSlot(hour=row["hour"], price=_num(row.get("price")), on=bool(row.get("on")))
        for row in rows
        if isinstance(row, Mapping) and _int(row.get("hour")) is not None
    )
    return DkSchedule(
        area=str(data.get("area")),
        mode=str(data.get("mode")),
        strategy=str(data.get("strategy")),
        param=_int(data.get("param")),
        date=str(data.get("date")),
        on_now=bool(data.get("on_now")),
        schedule=slots,
    )


def _level(value: Any) -> Optional[PriceLevel]:
    try:
        return PriceLevel(value)
    except ValueError:
        return None


def parse_shelly_tariff(payload: Any) -> TariffDays:
    """``GET /api/shelly/tariff``: the Tibber-shaped answer, ``today`` and ``tomorrow``."""
    data = _need_mapping(payload, "shelly tariff")
    try:
        info = data["data"]["viewer"]["homes"][0]["currentSubscription"]["priceInfo"]
    except (KeyError, IndexError, TypeError) as err:
        raise ElpriserParseError("shelly tariff: unexpected shape") from err

    def day(rows: Any) -> Tuple[TariffPrice, ...]:
        out: List[TariffPrice] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            starts = _parse_dt(row.get("startsAt"))
            if starts is None:
                continue
            out.append(
                TariffPrice(
                    total=_num(row.get("total")),
                    starts_at=starts,
                    currency=_str(row.get("currency")) or "DKK",
                    level=_level(row.get("level")),
                )
            )
        return tuple(out)

    return TariffDays(today=day(info.get("today")), tomorrow=day(info.get("tomorrow")))


def parse_supplier(payload: Any) -> Supplier:
    data = _need_mapping(payload, "supplier lookup")
    return Supplier(address=_str(data.get("address")), name=_str(data.get("name")), error=_str(data.get("error")))
