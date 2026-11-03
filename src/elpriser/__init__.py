"""Typed Python client for the elpriser.org electricity price API.

40 European price zones, current/next/today/tomorrow hourly prices, cheapest hours,
best window, forecast days (settled vs forecast flagged) and the Danish tariff-aware
endpoints. Sync (stdlib only) and async (httpx).

    >>> from elpriser import Elpriser, Zone
    >>> Elpriser().now(Zone.DE)                       # doctest: +SKIP

Data: https://elpriser.org - spot prices only outside Denmark (no VAT, duties or
grid fees); forecast days are elpriser.org's own model, not an official forecast.
"""

from __future__ import annotations

from ._version import __version__
from .aclient import AsyncElpriser
from .client import Elpriser, Transport
from .errors import (
    BadRequestError,
    ElpriserConnectionError,
    ElpriserError,
    ElpriserHTTPError,
    ElpriserParseError,
    ElpriserTimeoutError,
    NoDataError,
    RateLimitedError,
    ServerError,
    UnknownZoneError,
)
from .models import (
    Day,
    DayStats,
    DkNow,
    DkPrices,
    DkSchedule,
    Estimate,
    HourPrice,
    Mode,
    PriceKind,
    PriceLevel,
    PriceSeries,
    ScheduleSlot,
    Slot,
    Strategy,
    Supplier,
    TariffDays,
    TariffPrice,
    Window,
    price_levels,
)
from .zones import ZONES, Zone

__all__ = [
    "ZONES",
    "AsyncElpriser",
    "BadRequestError",
    "Day",
    "DayStats",
    "DkNow",
    "DkPrices",
    "DkSchedule",
    "Elpriser",
    "ElpriserConnectionError",
    "ElpriserError",
    "ElpriserHTTPError",
    "ElpriserParseError",
    "ElpriserTimeoutError",
    "Estimate",
    "HourPrice",
    "Mode",
    "NoDataError",
    "PriceKind",
    "PriceLevel",
    "PriceSeries",
    "RateLimitedError",
    "ScheduleSlot",
    "ServerError",
    "Slot",
    "Strategy",
    "Supplier",
    "TariffDays",
    "TariffPrice",
    "Transport",
    "UnknownZoneError",
    "Window",
    "Zone",
    "__version__",
    "price_levels",
]
