"""Typed result objects and the price arithmetic that works on them.

Nothing here does I/O. A :class:`PriceSeries` is one parsed API answer; every
question you can ask about prices (current hour, cheapest hours, best window,
levels ...) is a method on it, so one HTTP request can answer many questions.

Money: ``Slot.price`` is always **local currency per kWh** (``PriceSeries.currency``).
For the zone feed (``/api/nordic``) the API publishes EUR/MWh (GBP/MWh for ``gb``)
plus an ECB rate; ``price_mwh`` is the number as published and
``price = price_mwh * rate / 1000``. For the Danish tariff-aware feed
(``/api/forecast``) the API publishes DKK/kWh directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from zoneinfo import ZoneInfo

from .zones import Zone

HOUR = timedelta(hours=1)
DECIMALS = 5


class PriceKind(str, Enum):
    """What a price is."""

    ACTUAL = "actual"
    """Settled day-ahead price (for ``gb``: the within-day market index)."""
    FORECAST = "forecast"
    """elpriser.org's own statistical forecast. NOT an official forecast; it is
    replaced by the settled price when that is published."""
    ESTIMATE = "estimate"
    """elpriser.org's estimate of an hour of a settled day that has no price yet
    (``gb``, ``pt``, ``ie``). Only appears when you ask for it."""


class PriceLevel(str, Enum):
    """Quintile rank of an hour among the priced hours of its local day.

    Same scale and same method as the Tibber-compatible ``level`` field of
    ``/api/shelly/tariff``: the cheapest fifth of the day is ``VERY_CHEAP`` and so
    on.
    """

    VERY_CHEAP = "VERY_CHEAP"
    CHEAP = "CHEAP"
    NORMAL = "NORMAL"
    EXPENSIVE = "EXPENSIVE"
    VERY_EXPENSIVE = "VERY_EXPENSIVE"


class Mode(str, Enum):
    """Which Danish price ``/api/forecast`` and friends return (DK1/DK2 only)."""

    SPOT_EX = "spot_ex"
    """Raw spot price, excluding VAT."""
    SPOT_INKL = "spot_inkl"
    """Spot incl. 25 % VAT."""
    INKL_ALT = "inkl_alt"
    """Spot + Energinet system and transmission tariffs + electricity duty, incl. VAT.
    Not the local grid tariff (use ``NET_INKL_ALT`` + ``gln``). The default."""
    INKL_ALT_ELVARME = "inkl_alt_elvarme"
    """``INKL_ALT`` with the reduced duty for electric heating."""
    INKL_ALT_MINUS = "inkl_alt_minus"
    """``INKL_ALT`` without the electricity duty."""
    NET_INKL_ALT = "net_inkl_alt"
    """``INKL_ALT`` plus a specific grid company's tariff (needs ``gln``)."""
    NET_INKL_ALT_ELVARME = "net_inkl_alt_elvarme"
    NET_INKL_TARIF = "net_inkl_tarif"
    """Grid tariff included but without the electricity duty (needs ``gln``)."""

    @property
    def needs_gln(self) -> bool:
        return self.value.startswith("net_")


class Strategy(str, Enum):
    """Server-side schedule strategies of ``/api/schedule`` and ``/api/now``."""

    CHEAPEST_N = "cheapest_n"
    CHEAPEST_PCT = "cheapest_pct"
    AVOID_EXPENSIVE_N = "avoid_expensive_n"
    AVOID_EXPENSIVE_PCT = "avoid_expensive_pct"
    AVOID_PEAK = "avoid_peak"
    NIGHT_CHEAP = "night_cheap"
    SMART = "smart"


# --------------------------------------------------------------------------
# Hours
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """One hour of price.

    ``start`` and ``end`` are timezone-aware **UTC** datetimes, so comparing,
    sorting and subtracting them is always right - also across a daylight-saving
    change, where local datetimes would silently be wrong. Use ``local_start`` /
    ``local_end`` (or ``hour`` / ``local_date``) for the zone's own wall clock.
    """

    start: datetime
    end: datetime
    price: Optional[float]
    """Local currency per kWh (``PriceSeries.currency``); ``None`` = no price (yet)."""
    kind: PriceKind = PriceKind.ACTUAL
    min: Optional[float] = None
    """P10, local currency per kWh. Only on model forecast/estimate hours."""
    max: Optional[float] = None
    """P90, local currency per kWh. Only on model forecast/estimate hours."""
    price_mwh: Optional[float] = None
    """The number as the zone feed publishes it (``PriceSeries.unit``, e.g. EUR/MWh).
    ``None`` on the Danish tariff-aware feed, which is published in DKK/kWh."""
    min_mwh: Optional[float] = None
    max_mwh: Optional[float] = None
    tz: Optional[ZoneInfo] = field(default=None, repr=False, compare=False)
    """The zone's wall clock (UTC when unknown)."""

    @property
    def local_start(self) -> datetime:
        """``start`` on the zone's wall clock. Do not subtract these across a DST change;
        use ``start`` / ``end`` for arithmetic."""
        return self.start.astimezone(self.tz or timezone.utc)

    @property
    def local_end(self) -> datetime:
        return self.end.astimezone(self.tz or timezone.utc)

    @property
    def hour(self) -> int:
        """The local wall-clock hour (0-23), as the API's ``hour`` field (on a 25-hour day the
        doubled hour 2 appears twice)."""
        return self.local_start.hour

    @property
    def local_date(self) -> date:
        return self.local_start.date()

    @property
    def is_priced(self) -> bool:
        return self.price is not None


@dataclass(frozen=True)
class Day:
    """One local calendar day as the API delivered it."""

    date: date
    kind: PriceKind
    """``ACTUAL`` for a settled day, ``FORECAST`` for a model day (ESTIMATE never
    appears here: estimates are kept apart in :attr:`PriceSeries.estimate`)."""
    slots: Tuple[Slot, ...]
    horizon: Optional[int] = None
    """Zone feed, forecast days of EU zones: days after the last settled day + 1 (2-7;
    ``gb`` starts at 1)."""
    weekday: Optional[int] = None
    """Danish feed only: ``0`` = Sunday .. ``6`` = Saturday, as the API sends it."""
    source: Optional[str] = None
    """Danish feed only: ``"model"`` or ``"heuristic"`` (weekday-average fallback) on forecast days."""
    tariff: Optional[str] = None
    """Danish ``net_`` modes: ``"published"`` or ``"last_known"``."""
    grid_tariff: Optional[Tuple[Optional[float], ...]] = None
    """Danish ``net_`` modes: the grid company's tariff for each local hour (as the API sends it:
    DKK/kWh excl. VAT; ``None`` where it publishes none)."""

    @property
    def is_forecast(self) -> bool:
        return self.kind is PriceKind.FORECAST

    @property
    def is_settled(self) -> bool:
        return self.kind is PriceKind.ACTUAL

    @property
    def is_complete(self) -> bool:
        """Every hour of the day has a price."""
        return bool(self.slots) and all(s.price is not None for s in self.slots)


@dataclass(frozen=True)
class Estimate:
    """The API's top-level ``estimate`` (``gb``, ``pt``, ``ie``).

    elpriser.org's own statistical estimate for hours of a settled day that have
    no price yet (``gb``: the hours of today that have not traded; ``pt``/``ie``:
    the last local hour, 23). ``PriceSeries.days`` is never changed by it.
    """

    date: date
    scope: str
    model: Optional[str]
    generated_at: Optional[datetime]
    slots: Tuple[Slot, ...]
    method: Optional[str] = None
    accuracy: Optional[Mapping[str, Any]] = field(default=None, compare=False)


@dataclass(frozen=True)
class DayStats:
    minimum: Slot
    maximum: Slot
    average: float
    count: int


@dataclass(frozen=True)
class Window:
    """A run of consecutive hours (see :meth:`PriceSeries.best_window`)."""

    start: datetime
    """UTC, like :attr:`Slot.start`."""
    end: datetime
    slots: Tuple[Slot, ...]
    average: float
    """Mean price per kWh over the window (local currency)."""

    @property
    def local_start(self) -> datetime:
        return self.slots[0].local_start

    @property
    def local_end(self) -> datetime:
        return self.slots[-1].local_end

    @property
    def hours(self) -> int:
        return len(self.slots)


# --------------------------------------------------------------------------
# Level calculation (same algorithm as /api/shelly/tariff)
# --------------------------------------------------------------------------


def price_levels(prices: Sequence[Optional[float]]) -> List[PriceLevel]:
    """Quintile levels for one day's prices, in the same order.

    Mirrors the API: cut points are the sorted values at 20/40/60/80 % (index
    ``floor(n * f)``, clamped), ``p <= cut`` decides. A missing price is
    ``NORMAL`` (as in the API); a day without any price is all ``NORMAL``.
    """
    valid = sorted(p for p in prices if p is not None)
    if not valid:
        return [PriceLevel.NORMAL for _ in prices]

    def at(f: float) -> float:
        return valid[min(math.floor(len(valid) * f), len(valid) - 1)]

    p20, p40, p60, p80 = at(0.2), at(0.4), at(0.6), at(0.8)
    out: List[PriceLevel] = []
    for p in prices:
        if p is None:
            out.append(PriceLevel.NORMAL)
        elif p <= p20:
            out.append(PriceLevel.VERY_CHEAP)
        elif p <= p40:
            out.append(PriceLevel.CHEAP)
        elif p <= p60:
            out.append(PriceLevel.NORMAL)
        elif p <= p80:
            out.append(PriceLevel.EXPENSIVE)
        else:
            out.append(PriceLevel.VERY_EXPENSIVE)
    return out


# --------------------------------------------------------------------------
# The series
# --------------------------------------------------------------------------


def _utc(moment: Optional[datetime]) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return moment.astimezone(timezone.utc)


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class PriceSeries:
    """Everything one API call returned for a zone, plus the questions you ask of it."""

    zone: Zone
    currency: str
    """ISO 4217 code the prices are in (``zoneInfo.currency``; DKK on the Danish feed)."""
    unit: str
    """What the API published: ``"EUR/MWh"``, ``"GBP/MWh"`` (``gb``) or ``"DKK/kWh"``."""
    days: Tuple[Day, ...]
    estimate: Optional[Estimate] = None
    source: str = "zone-feed"
    """``"zone-feed"`` (``/api/nordic``) or ``"dk-forecast"`` (``/api/forecast``)."""
    mode: Optional[str] = None
    """Danish feed: the price mode that was asked for."""
    gln: Optional[str] = None
    rate: Optional[float] = None
    """ECB reference rate EUR -> ``currency`` (``zoneInfo.rate``; 1 for EUR zones and for ``gb``,
    whose prices are published in GBP). ``None`` on the Danish feed."""
    rate_date: Optional[str] = None
    rate_source: Optional[str] = None
    generated: Optional[str] = None
    trained_through: Optional[str] = None
    model: Any = None
    forecast_generated: Optional[str] = None
    accuracy: Optional[Mapping[str, Any]] = field(default=None, compare=False)
    """The API's back-test statistics for the forecast days, unchanged (``byHorizon`` ...)."""
    charges: Optional[Mapping[str, Any]] = field(default=None, compare=False)
    """Danish feed: ``{"sys", "trans", "afg", "vat"}`` as the API sends them."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # -- basics -------------------------------------------------------------

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.zone.tz)

    @property
    def price_unit(self) -> str:
        """Unit of :attr:`Slot.price`."""
        return f"{self.currency}/kWh"

    def all_slots(self, *, include_estimate: bool = False) -> List[Slot]:
        """Every hour in time order. With ``include_estimate`` an estimated hour
        replaces the unpriced settled hour it belongs to (kind ``ESTIMATE``)."""
        by_start: Dict[datetime, Slot] = {}
        for day in self.days:
            for slot in day.slots:
                by_start[slot.start] = slot
        if include_estimate and self.estimate is not None:
            for est in self.estimate.slots:
                current = by_start.get(est.start)
                if current is not None and current.price is None and est.price is not None:
                    by_start[est.start] = est
        return [by_start[k] for k in sorted(by_start)]

    def day(self, when: date) -> Optional[Day]:
        """The :class:`Day` for a local date, if the API delivered it."""
        for d in self.days:
            if d.date == when:
                return d
        return None

    @property
    def settled_days(self) -> Tuple[Day, ...]:
        return tuple(d for d in self.days if d.is_settled)

    @property
    def forecast_days(self) -> Tuple[Day, ...]:
        """The days that are elpriser.org's forecast, not settled prices."""
        return tuple(d for d in self.days if d.is_forecast)

    def local_today(self, at: Optional[datetime] = None) -> date:
        return _utc(at).astimezone(self.tzinfo).date()

    # -- current / next -----------------------------------------------------

    def slot_at(self, at: Optional[datetime] = None, *, include_estimate: bool = False) -> Optional[Slot]:
        """The hour containing ``at`` (default: now), priced or not."""
        moment = _utc(at)
        for slot in self.all_slots(include_estimate=include_estimate):
            if slot.start <= moment < slot.end:
                return slot
        return None

    def now(self, at: Optional[datetime] = None, *, estimate: bool = False) -> Optional[Slot]:
        """The priced hour containing ``at`` (default: now), or ``None``.

        ``None`` means there is no price for this hour (yet) - e.g. ``gb``, where an
        hour is only priced once it is over. With ``estimate=True`` such an hour is
        filled with elpriser.org's estimate when the API sent one (``slot.kind`` is
        then ``ESTIMATE``). A forecast hour is returned as is (``kind`` says so).
        """
        slot = self.slot_at(at, include_estimate=estimate)
        return slot if slot is not None and slot.price is not None else None

    def next_hour(self, at: Optional[datetime] = None, *, estimate: bool = False) -> Optional[Slot]:
        """The priced hour that starts at the next hour boundary, or ``None``."""
        moment = _floor_hour(_utc(at)) + HOUR
        for slot in self.all_slots(include_estimate=estimate):
            if slot.start == moment:
                return slot if slot.price is not None else None
        return None

    def latest_known(self, at: Optional[datetime] = None, *, max_age: timedelta = timedelta(hours=3)) -> Optional[Slot]:
        """The newest priced hour that ended no more than ``max_age`` before ``at``.

        For ``gb``, whose current hour is never priced yet.
        """
        moment = _utc(at)
        past = [s for s in self.all_slots() if s.price is not None and s.end <= moment and moment - s.end <= max_age]
        return past[-1] if past else None

    # -- days ---------------------------------------------------------------

    def day_slots(self, when: date, *, include_estimate: bool = False) -> List[Slot]:
        """The hours of a local calendar day (empty if the API has none for it)."""
        return [s for s in self.all_slots(include_estimate=include_estimate) if s.local_date == when]

    def today(self, at: Optional[datetime] = None, *, include_estimate: bool = False) -> List[Slot]:
        """Today's hours (the zone's local day). Check ``slot.kind``: ``FORECAST`` hours are
        elpriser.org's own model, not settled prices."""
        return self.day_slots(self.local_today(at), include_estimate=include_estimate)

    def tomorrow(self, at: Optional[datetime] = None, *, include_estimate: bool = False) -> List[Slot]:
        """Tomorrow's hours. Until the day-ahead prices are published (about 13:00 CET) these
        are ``FORECAST`` hours (or absent, for ``gb`` and ``xk``); the kind tells which."""
        return self.day_slots(self.local_today(at) + timedelta(days=1), include_estimate=include_estimate)

    def stats(self, when: date) -> Optional[DayStats]:
        """Lowest, highest and mean price of a local day (``None`` if nothing is priced)."""
        priced = [s for s in self.day_slots(when) if s.price is not None]
        if not priced:
            return None
        return DayStats(
            minimum=min(priced, key=lambda s: (s.price, s.start)),
            maximum=max(priced, key=lambda s: (s.price, -s.start.timestamp())),
            average=round(sum(s.price for s in priced if s.price is not None) / len(priced), DECIMALS),
            count=len(priced),
        )

    # -- levels -------------------------------------------------------------

    def levels(self, when: Optional[date] = None, *, at: Optional[datetime] = None) -> List[Tuple[Slot, PriceLevel]]:
        """Every hour of a local day (default: today) with its :class:`PriceLevel`."""
        slots = self.day_slots(when if when is not None else self.local_today(at))
        return list(zip(slots, price_levels([s.price for s in slots])))

    def level(self, slot: Slot) -> Optional[PriceLevel]:
        """The level of ``slot`` among the hours of its own local day, ``None`` if unpriced."""
        if slot.price is None:
            return None
        for s, lv in self.levels(slot.local_date):
            if s.start == slot.start:
                return lv
        return None

    # -- cheapest hours / best window ---------------------------------------

    def _candidates(
        self,
        *,
        day: Optional[date],
        start: Optional[datetime],
        end: Optional[datetime],
        at: Optional[datetime],
        include_forecast: bool,
        include_estimate: bool,
    ) -> List[Slot]:
        slots = self.all_slots(include_estimate=include_estimate)
        if day is not None:
            if start is not None or end is not None:
                raise ValueError("pass either day or start/end, not both")
            chosen = [s for s in slots if s.local_date == day]
        else:
            lo = _utc(start) if start is not None else _floor_hour(_utc(at))
            hi = _utc(end) if end is not None else lo + timedelta(hours=24)
            chosen = [s for s in slots if s.start >= lo and s.end <= hi]
        chosen = [s for s in chosen if s.price is not None]
        if not include_forecast:
            chosen = [s for s in chosen if s.kind is not PriceKind.FORECAST]
        return chosen

    def cheapest_hours(
        self,
        n: int,
        *,
        day: Optional[date] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        at: Optional[datetime] = None,
        include_forecast: bool = True,
        include_estimate: bool = False,
    ) -> List[Slot]:
        """The ``n`` cheapest priced hours, in time order (earliest wins a tie).

        Window: a local ``day``, or ``start``..``end``; by default the next 24 hours
        counted from the current hour. ``include_forecast=False`` restricts it to
        settled prices. Fewer than ``n`` hours come back when fewer are priced.
        """
        if n < 0:
            raise ValueError("n must be >= 0")
        pool = self._candidates(
            day=day, start=start, end=end, at=at, include_forecast=include_forecast, include_estimate=include_estimate
        )
        chosen = sorted(pool, key=lambda s: (s.price, s.start))[:n]
        return sorted(chosen, key=lambda s: s.start)

    def best_window(
        self,
        hours: int,
        *,
        day: Optional[date] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        at: Optional[datetime] = None,
        include_forecast: bool = True,
        include_estimate: bool = False,
    ) -> Optional[Window]:
        """The cheapest run of ``hours`` consecutive priced hours (lowest mean; earliest wins a tie).

        Same window rules as :meth:`cheapest_hours`. ``None`` if no such run exists.
        """
        if hours < 1:
            raise ValueError("hours must be >= 1")
        pool = self._candidates(
            day=day, start=start, end=end, at=at, include_forecast=include_forecast, include_estimate=include_estimate
        )
        best: Optional[Tuple[float, int]] = None
        for i in range(len(pool) - hours + 1):
            run = pool[i : i + hours]
            if any(run[j + 1].start != run[j].end for j in range(hours - 1)):
                continue
            total = sum(s.price for s in run if s.price is not None)
            if best is None or total < best[0] - 1e-12:
                best = (total, i)
        if best is None:
            return None
        run = pool[best[1] : best[1] + hours]
        return Window(
            start=run[0].start,
            end=run[-1].end,
            slots=tuple(run),
            average=round(best[0] / hours, DECIMALS),
        )

    # -- accuracy -----------------------------------------------------------

    def accuracy_for(self, horizon: int) -> Optional[Mapping[str, Any]]:
        """The API's back-test numbers for forecast day ``D+horizon`` (zone feed), if it sent any."""
        by_h = (self.accuracy or {}).get("byHorizon")
        if isinstance(by_h, Mapping):
            entry = by_h.get(str(horizon))
            if isinstance(entry, Mapping):
                return entry
        return None

    def is_low_confidence(self, horizon: int) -> Optional[bool]:
        """The API's ``low`` flag for ``horizon`` (``None`` when it sent no accuracy)."""
        entry = self.accuracy_for(horizon)
        if entry is None or "low" not in entry:
            return None
        return bool(entry["low"])

    # -- pandas -------------------------------------------------------------

    def to_dataframe(self, *, include_estimate: bool = True) -> Any:
        """The series as a :class:`pandas.DataFrame` (needs ``pip install elpriser[pandas]``).

        Index ``start`` (timezone-aware, converted to the zone's wall clock; instants are unique also on a 25-hour day); columns ``end``, ``price``
        (local currency/kWh), ``min``, ``max``, ``price_mwh``, ``min_mwh``, ``max_mwh``,
        ``kind`` (``actual``/``forecast``/``estimate``), ``horizon``, ``zone``, ``currency``.
        Estimated hours replace unpriced settled hours when ``include_estimate`` (the
        default); ``kind`` says which rows they are.
        """
        try:
            import pandas as pd
        except ImportError as err:  # pragma: no cover - exercised in the no-pandas CI job
            raise ImportError("to_dataframe() needs pandas: pip install 'elpriser[pandas]'") from err
        horizons = {s.start: d.horizon for d in self.days for s in d.slots}
        slots = self.all_slots(include_estimate=include_estimate)
        tz = self.zone.tz
        index = pd.DatetimeIndex([pd.Timestamp(s.start) for s in slots], name="start")
        index = index.tz_convert(tz) if len(slots) else index.tz_localize(None)
        frame = pd.DataFrame(
            {
                "end": [pd.Timestamp(s.end) for s in slots],
                "price": [s.price for s in slots],
                "min": [s.min for s in slots],
                "max": [s.max for s in slots],
                "price_mwh": [s.price_mwh for s in slots],
                "min_mwh": [s.min_mwh for s in slots],
                "max_mwh": [s.max_mwh for s in slots],
                "kind": [s.kind.value for s in slots],
                "horizon": [horizons.get(s.start) for s in slots],
                "zone": [self.zone.value] * len(slots),
                "currency": [self.currency] * len(slots),
            },
            index=index,
        )
        if len(slots):
            frame["end"] = pd.DatetimeIndex(frame["end"]).tz_convert(tz)
        for col in ("price", "min", "max", "price_mwh", "min_mwh", "max_mwh"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame["horizon"] = frame["horizon"].astype("Int64")
        return frame


# --------------------------------------------------------------------------
# Danish endpoints (as the API returns them)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DkNow:
    """``/api/now``: this hour's price and whether a schedule strategy says ON."""

    on: bool
    price: Optional[float]
    """DKK/kWh in the requested :class:`Mode`."""
    hour: int
    area: str
    mode: str
    strategy: str


@dataclass(frozen=True)
class HourPrice:
    hour: int
    price: Optional[float]


@dataclass(frozen=True)
class DkPrices:
    """``/api/prices`` for one date."""

    area: str
    mode: str
    date: str
    unit: str
    charges: Optional[str]
    """``"historical"`` or ``"current"``: which tax/tariff rates a past date is priced with."""
    prices: Tuple[HourPrice, ...]
    current_hour: Optional[int] = None
    current_price: Optional[float] = None


@dataclass(frozen=True)
class ScheduleSlot:
    hour: int
    price: Optional[float]
    on: bool


@dataclass(frozen=True)
class DkSchedule:
    """``/api/schedule``: a 24 h ON/OFF plan for a strategy."""

    area: str
    mode: str
    strategy: str
    param: Optional[int]
    date: str
    on_now: bool
    schedule: Tuple[ScheduleSlot, ...]

    @property
    def on_hours(self) -> List[int]:
        return [s.hour for s in self.schedule if s.on]


@dataclass(frozen=True)
class TariffPrice:
    """One hour of ``/api/shelly/tariff`` (Tibber-compatible)."""

    total: Optional[float]
    starts_at: datetime
    currency: str
    level: Optional[PriceLevel]
    """The API's own level (quintile of the day), e.g. ``PriceLevel.VERY_CHEAP``."""


@dataclass(frozen=True)
class TariffDays:
    today: Tuple[TariffPrice, ...]
    tomorrow: Tuple[TariffPrice, ...]


@dataclass(frozen=True)
class Supplier:
    """``/api/supplierlookup``: the grid company (netselskab) at an address."""

    address: Optional[str]
    name: Optional[str]
    error: Optional[str] = None
    """The API's own reason when it found nothing, e.g. ``"no_address"``."""

    @property
    def found(self) -> bool:
        return self.name is not None


AnyZone = Union[Zone, str]
