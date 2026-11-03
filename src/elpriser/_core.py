"""Shared plumbing of the sync and async clients: request planning, validation,
HTTP status -> exception mapping and the retry policy. No I/O happens here."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any, Callable, Dict, Mapping, NamedTuple, Optional, Union
from urllib.parse import urlencode, urlsplit

from . import _parse
from ._version import __version__
from .errors import (
    BadRequestError,
    ElpriserHTTPError,
    ElpriserParseError,
    NoDataError,
    RateLimitedError,
    ServerError,
)
from .models import Mode, Strategy
from .zones import Zone

DEFAULT_BASE_URL = "https://elpriser.org"
DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 0.5
MAX_RETRY_DELAY = 30.0
MAX_BODY_BYTES = 20 * 1024 * 1024
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

DEFAULT_USER_AGENT = f"elpriser-python/{__version__}"

AnyMode = Union[Mode, str]


class Response(NamedTuple):
    """What a transport hands back: status, lower-cased headers, raw body."""

    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class Plan:
    """A request and how to read the answer."""

    path: str
    params: Dict[str, str]
    parse: Callable[[Any], Any] = field(compare=False)


def validate_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"base_url must be an http(s) URL, got {base_url!r}")
    return base_url.rstrip("/")


def build_url(base_url: str, path: str, params: Mapping[str, str]) -> str:
    query = urlencode(params)
    return f"{base_url}{path}" + (f"?{query}" if query else "")


def request_headers(user_agent: str) -> Dict[str, str]:
    return {"User-Agent": user_agent, "Accept": "application/json"}


# -- validation -----------------------------------------------------------


def to_mode(mode: Optional[AnyMode], gln: Optional[str]) -> Mode:
    """Validate a Danish price mode (default ``inkl_alt``) and its ``gln`` requirement."""
    try:
        chosen = Mode.INKL_ALT if mode is None else Mode(mode)
    except ValueError:
        valid = ", ".join(m.value for m in Mode)
        raise ValueError(f"unknown mode {mode!r}; valid modes: {valid}") from None
    if chosen.needs_gln and not gln:
        raise ValueError(f"mode {chosen.value!r} needs gln - the grid tariff depends on the network company")
    return chosen


def to_area(area: Union[Zone, str]) -> str:
    text = area.value if isinstance(area, Zone) else str(area)
    if text.strip().upper() not in ("DK1", "DK2"):
        raise ValueError(f"area must be DK1 or DK2, got {area!r}")
    return text.strip().upper()


def to_date_str(value: Union[_date, str, None]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, _date):
        return value.isoformat()
    try:
        return _date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from None


def _bounded(name: str, value: Optional[int], lo: int, hi: int) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{name} must be an integer {lo}..{hi}, got {value!r}")
    return value


# -- planning -------------------------------------------------------------


def plan_series(zone: Union[Zone, str], mode: Optional[AnyMode], gln: Optional[str], *, feed: bool) -> Plan:
    """What to call to get a zone's :class:`PriceSeries`.

    Danish zones default to ``/api/forecast`` (DKK/kWh incl. tariffs, ``mode``), every
    other zone - and any zone with ``feed=True`` - to ``/api/nordic`` (EUR/MWh + ECB rate).
    """
    z = Zone.parse(zone)
    if z.area is not None and not feed:
        chosen = to_mode(mode, gln)
        params = {"area": z.area, "mode": chosen.value}
        if gln:
            params["gln"] = gln
        return Plan("/api/forecast", params, lambda p: _parse.parse_dk_forecast(p, z, chosen.value, gln))
    if mode is not None or gln:
        raise ValueError(
            "mode and gln only apply to the Danish tariff-aware feed (dk1/dk2, not zone_feed()); "
            f"{z.value} has plain spot prices"
        )
    return Plan("/api/nordic", {"zone": z.value}, lambda p: _parse.parse_zone_feed(p, z))


def plan_dk_now(
    area: Union[Zone, str],
    mode: Optional[AnyMode],
    gln: Optional[str],
    strategy: Union[Strategy, str, None],
    hours: Optional[int],
    pct: Optional[int],
    max_off: Optional[int],
) -> Plan:
    params = {"area": to_area(area), "mode": to_mode(mode, gln).value}
    _schedule_params(params, gln, strategy, hours, pct, max_off)
    return Plan("/api/now", params, _parse.parse_dk_now)


def plan_dk_schedule(
    area: Union[Zone, str],
    mode: Optional[AnyMode],
    gln: Optional[str],
    strategy: Union[Strategy, str, None],
    hours: Optional[int],
    pct: Optional[int],
    max_off: Optional[int],
    date: Union[_date, str, None],
) -> Plan:
    params = {"area": to_area(area), "mode": to_mode(mode, gln).value}
    _schedule_params(params, gln, strategy, hours, pct, max_off)
    day = to_date_str(date)
    if day:
        params["date"] = day
    return Plan("/api/schedule", params, _parse.parse_dk_schedule)


def _schedule_params(
    params: Dict[str, str],
    gln: Optional[str],
    strategy: Union[Strategy, str, None],
    hours: Optional[int],
    pct: Optional[int],
    max_off: Optional[int],
) -> None:
    if gln:
        params["gln"] = gln
    if strategy is not None:
        try:
            params["strategy"] = Strategy(strategy).value
        except ValueError:
            valid = ", ".join(s.value for s in Strategy)
            raise ValueError(f"unknown strategy {strategy!r}; valid strategies: {valid}") from None
    for name, value, lo, hi in (("hours", hours, 1, 23), ("pct", pct, 1, 100), ("max_off", max_off, 1, 12)):
        checked = _bounded(name, value, lo, hi)
        if checked is not None:
            params[name] = str(checked)


def plan_dk_prices(
    area: Union[Zone, str],
    date: Union[_date, str, None],
    mode: Optional[AnyMode],
    gln: Optional[str],
    charges: Optional[str],
) -> Plan:
    params = {"area": to_area(area), "mode": to_mode(mode, gln).value}
    if gln:
        params["gln"] = gln
    day = to_date_str(date)
    if day:
        params["date"] = day
    if charges is not None:
        if charges not in ("historical", "current"):
            raise ValueError("charges must be 'historical' or 'current'")
        params["charges"] = charges
    return Plan("/api/prices", params, _parse.parse_dk_prices)


def plan_shelly_tariff(area: Union[Zone, str], mode: Optional[AnyMode], gln: Optional[str]) -> Plan:
    params = {"area": to_area(area), "mode": to_mode(mode, gln).value}
    if gln:
        params["gln"] = gln
    return Plan("/api/shelly/tariff", params, _parse.parse_shelly_tariff)


def plan_supplier(lat: float, lng: float) -> Plan:
    for name, value, bound in (("lat", lat, 90), ("lng", lng, 180)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not -bound <= value <= bound:
            raise ValueError(f"{name} must be a number within +-{bound}, got {value!r}")
    return Plan("/api/supplierlookup", {"lat": repr(float(lat)), "lng": repr(float(lng))}, _parse.parse_supplier)


# -- responses ------------------------------------------------------------


def _retry_after(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # an HTTP-date; not worth parsing, the backoff applies
    return seconds if seconds >= 0 else None


def _error_text(resp: Response) -> str:
    try:
        data = json.loads(resp.body.decode("utf-8", "replace"))
        if isinstance(data, dict):
            message = data.get("error")
            if isinstance(message, str):
                return message
    except ValueError:
        pass
    text = resp.body.decode("utf-8", "replace").strip()
    return (text[:200] or "no message") if text else "no message"


def error_for(resp: Response, url: str) -> ElpriserHTTPError:
    """The exception for a non-2xx response."""
    message = _error_text(resp)
    retry_after = _retry_after(resp.headers.get("retry-after"))
    if resp.status == 400:
        return BadRequestError(400, message, url)
    if resp.status == 404:
        return NoDataError(404, message, url)
    if resp.status == 429:
        return RateLimitedError(429, message, url, retry_after)
    if resp.status >= 500:
        return ServerError(resp.status, message, url, retry_after)
    return ElpriserHTTPError(resp.status, message, url)


def decode_json(resp: Response, url: str) -> Any:
    if not 200 <= resp.status < 300:
        raise error_for(resp, url)
    try:
        return json.loads(resp.body)
    except ValueError as err:
        raise ElpriserParseError(f"{url} did not return JSON") from err


def is_retryable(status: int) -> bool:
    return status in RETRY_STATUSES


def retry_delay(attempt: int, backoff: float, retry_after: Optional[float]) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based): the server's
    ``Retry-After`` if it sent one, else exponential backoff; capped."""
    if retry_after is not None:
        return min(retry_after, MAX_RETRY_DELAY)
    return float(min(backoff * (2**attempt), MAX_RETRY_DELAY))


def check_options(timeout: float, retries: int, backoff: float) -> None:
    if timeout <= 0:
        raise ValueError("timeout must be > 0")
    if retries < 0:
        raise ValueError("retries must be >= 0")
    if backoff < 0:
        raise ValueError("backoff must be >= 0")
