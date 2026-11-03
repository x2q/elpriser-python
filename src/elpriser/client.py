"""The synchronous client. Standard library only (``urllib``), no dependencies."""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from datetime import date as Date
from datetime import datetime
from typing import Any, Callable, List, Mapping, Optional, Tuple, Union

from . import _core
from ._core import AnyMode, Response
from .errors import ElpriserConnectionError, ElpriserTimeoutError
from .models import (
    Day,
    DkNow,
    DkPrices,
    DkSchedule,
    PriceLevel,
    PriceSeries,
    Slot,
    Strategy,
    Supplier,
    TariffDays,
    Window,
)
from .zones import Zone

Transport = Callable[[str, Mapping[str, str], float], Response]
"""``transport(url, headers, timeout) -> Response``. Replace it to test offline or to route
through a proxy; it must raise ``TimeoutError`` or ``OSError`` for network trouble."""


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> Response:
    """The default transport: one ``GET`` through :mod:`urllib.request`."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310 - scheme checked
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            body = resp.read(_core.MAX_BODY_BYTES + 1)
            status = resp.status
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as err:
        body = err.read(_core.MAX_BODY_BYTES + 1)
        status = err.code
        hdrs = {k.lower(): v for k, v in err.headers.items()}
    except urllib.error.URLError as err:
        reason = err.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(str(reason)) from err
        raise OSError(str(reason)) from err
    if len(body) > _core.MAX_BODY_BYTES:
        raise OSError("response too large")
    return Response(status, hdrs, body)


class Elpriser:
    """Client for the elpriser.org API.

    >>> from elpriser import Elpriser, Zone
    >>> client = Elpriser()
    >>> client.now(Zone.DE)            # doctest: +SKIP
    Slot(start=..., price=0.20123, ...)

    :param base_url: API root, default ``https://elpriser.org``.
    :param timeout: seconds per attempt (connect and read), default 10.
    :param retries: extra attempts after a timeout, a connection error, 429 or 5xx (default 2).
        ``Retry-After`` is honoured (capped at 30 s), else ``backoff * 2**attempt``.
    :param backoff: base delay in seconds between retries (default 0.5).
    :param user_agent: overrides ``elpriser-python/<version>``. The API's CDN rejects the
        default ``Python-urllib`` agent, so keep a custom one (``"my-app/1.0"`` is fine).
    :param transport: replace the HTTP layer, see :data:`Transport`.
    """

    def __init__(
        self,
        *,
        base_url: str = _core.DEFAULT_BASE_URL,
        timeout: float = _core.DEFAULT_TIMEOUT,
        retries: int = _core.DEFAULT_RETRIES,
        backoff: float = _core.DEFAULT_BACKOFF,
        user_agent: Optional[str] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        _core.check_options(timeout, retries, backoff)
        self.base_url = _core.validate_base_url(base_url)
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.user_agent = user_agent or _core.DEFAULT_USER_AGENT
        self._transport: Transport = transport or urllib_transport

    def __enter__(self) -> Elpriser:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Nothing to release (every call opens its own connection); here for symmetry with the async client."""

    def __repr__(self) -> str:
        return f"Elpriser(base_url={self.base_url!r})"

    # -- HTTP -----------------------------------------------------------

    def _fetch(self, plan: _core.Plan) -> Any:
        url = _core.build_url(self.base_url, plan.path, plan.params)
        headers = _core.request_headers(self.user_agent)
        attempt = 0
        while True:
            retry_after: Optional[float] = None
            try:
                resp = self._transport(url, headers, self.timeout)
            except (socket.timeout, TimeoutError) as err:
                failure: Exception = ElpriserTimeoutError(f"timed out after {self.timeout:g}s: {url}")
                failure.__cause__ = err
            except OSError as err:
                failure = ElpriserConnectionError(f"could not reach {url}: {err}")
                failure.__cause__ = err
            else:
                if _core.is_retryable(resp.status) and attempt < self.retries:
                    failure = _core.error_for(resp, url)
                    retry_after = getattr(failure, "retry_after", None)
                else:
                    return plan.parse(_core.decode_json(resp, url))
            if attempt >= self.retries:
                raise failure
            time.sleep(_core.retry_delay(attempt, self.backoff, retry_after))
            attempt += 1

    def get_json(self, path: str, **params: Any) -> Any:
        """Escape hatch: ``GET`` any API path and return the decoded JSON (no parsing)."""
        clean = {k: str(v) for k, v in params.items() if v is not None}
        return self._fetch(_core.Plan(path, clean, lambda payload: payload))

    # -- prices (any of the 40 zones) --------------------------------------

    def series(
        self, zone: Union[Zone, str], *, mode: Optional[AnyMode] = None, gln: Optional[str] = None
    ) -> PriceSeries:
        """Fetch a zone's whole price series in one request and return it.

        Everything else (:meth:`now`, :meth:`cheapest_hours` ...) is this plus a method on the
        result - call ``series()`` once and ask it several questions.

        * Danish zones (``dk1``/``dk2``): ``/api/forecast``, DKK/kWh incl. Energinet's tariffs,
          electricity duty and VAT (``mode`` default ``inkl_alt``; ``net_`` modes need ``gln``),
          today plus nine forecast days.
        * Every other zone: ``/api/nordic``, the spot price (no VAT, duties or grid fees) in EUR/MWh
          converted to the local currency with the ECB rate; settled days plus forecast days (where
          the zone has a forecast); ``estimate`` for ``gb``/``pt``/``ie``.
        """
        return self._fetch(_core.plan_series(zone, mode, gln, feed=False))  # type: ignore[no-any-return]

    def zone_feed(self, zone: Union[Zone, str]) -> PriceSeries:
        """Like :meth:`series`, but always the plain spot-price feed ``/api/nordic`` -
        also for ``dk1``/``dk2`` (EUR/MWh, no Danish tariffs or taxes)."""
        return self._fetch(_core.plan_series(zone, None, None, feed=True))  # type: ignore[no-any-return]

    def now(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        estimate: bool = False,
        at: Optional[datetime] = None,
    ) -> Optional[Slot]:
        """The price of the current hour, or ``None`` when this hour has no price (yet).
        See :meth:`PriceSeries.now` (``estimate=True`` fills ``gb``/``pt``/``ie`` gaps)."""
        return self.series(zone, mode=mode, gln=gln).now(at, estimate=estimate)

    def next_hour(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        estimate: bool = False,
        at: Optional[datetime] = None,
    ) -> Optional[Slot]:
        """The price of the next hour (a forecast hour if tomorrow is not published yet)."""
        return self.series(zone, mode=mode, gln=gln).next_hour(at, estimate=estimate)

    def today(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        include_estimate: bool = False,
        at: Optional[datetime] = None,
    ) -> List[Slot]:
        """Today's hourly prices in the zone's local day."""
        return self.series(zone, mode=mode, gln=gln).today(at, include_estimate=include_estimate)

    def tomorrow(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        at: Optional[datetime] = None,
    ) -> List[Slot]:
        """Tomorrow's hourly prices; ``FORECAST`` hours until the day-ahead prices are published."""
        return self.series(zone, mode=mode, gln=gln).tomorrow(at)

    def cheapest_hours(
        self,
        zone: Union[Zone, str],
        n: int,
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        day: Optional[Date] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        at: Optional[datetime] = None,
        include_forecast: bool = True,
        include_estimate: bool = False,
    ) -> List[Slot]:
        """The ``n`` cheapest hours (default: within the next 24 h). See :meth:`PriceSeries.cheapest_hours`."""
        return self.series(zone, mode=mode, gln=gln).cheapest_hours(
            n,
            day=day,
            start=start,
            end=end,
            at=at,
            include_forecast=include_forecast,
            include_estimate=include_estimate,
        )

    def best_window(
        self,
        zone: Union[Zone, str],
        hours: int,
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        day: Optional[Date] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        at: Optional[datetime] = None,
        include_forecast: bool = True,
        include_estimate: bool = False,
    ) -> Optional[Window]:
        """The cheapest run of ``hours`` consecutive hours. See :meth:`PriceSeries.best_window`."""
        return self.series(zone, mode=mode, gln=gln).best_window(
            hours,
            day=day,
            start=start,
            end=end,
            at=at,
            include_forecast=include_forecast,
            include_estimate=include_estimate,
        )

    def forecast(
        self, zone: Union[Zone, str], *, mode: Optional[AnyMode] = None, gln: Optional[str] = None
    ) -> Tuple[Day, ...]:
        """Only the forecast days (``day.kind is PriceKind.FORECAST``; ``day.horizon`` where given).

        The zone feed's forecast is elpriser.org's own statistical model - not an official
        forecast - and is replaced by the settled price when that is published.
        """
        return self.series(zone, mode=mode, gln=gln).forecast_days

    def levels(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        when: Optional[Date] = None,
        at: Optional[datetime] = None,
    ) -> List[Tuple[Slot, PriceLevel]]:
        """Today's (or ``when``'s) hours with their quintile :class:`PriceLevel`."""
        return self.series(zone, mode=mode, gln=gln).levels(when, at=at)

    # -- Denmark-only endpoints (npm client parity) -----------------------

    def dk_now(
        self,
        area: Union[Zone, str] = "DK1",
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        strategy: Union[Strategy, str, None] = None,
        hours: Optional[int] = None,
        pct: Optional[int] = None,
        max_off: Optional[int] = None,
    ) -> DkNow:
        """``/api/now``: this hour's DKK/kWh and whether the schedule ``strategy`` says ON now."""
        return self._fetch(_core.plan_dk_now(area, mode, gln, strategy, hours, pct, max_off))  # type: ignore[no-any-return]

    def dk_prices(
        self,
        area: Union[Zone, str] = "DK1",
        *,
        date: Union[Date, str, None] = None,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        charges: Optional[str] = None,
    ) -> DkPrices:
        """``/api/prices``: the 24 hourly prices of one date (default today, Danish time)."""
        return self._fetch(_core.plan_dk_prices(area, date, mode, gln, charges))  # type: ignore[no-any-return]

    def dk_schedule(
        self,
        area: Union[Zone, str] = "DK1",
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        strategy: Union[Strategy, str, None] = None,
        hours: Optional[int] = None,
        pct: Optional[int] = None,
        max_off: Optional[int] = None,
        date: Union[Date, str, None] = None,
    ) -> DkSchedule:
        """``/api/schedule``: the 24 h ON/OFF plan of a strategy (e.g. ``cheapest_n`` with ``hours=5``)."""
        return self._fetch(_core.plan_dk_schedule(area, mode, gln, strategy, hours, pct, max_off, date))  # type: ignore[no-any-return]

    def shelly_tariff(
        self, area: Union[Zone, str] = "DK1", *, mode: Optional[AnyMode] = None, gln: Optional[str] = None
    ) -> TariffDays:
        """``/api/shelly/tariff``: today + tomorrow in Tibber's shape, with the API's own quintile ``level``."""
        return self._fetch(_core.plan_shelly_tariff(area, mode, gln))  # type: ignore[no-any-return]

    def supplier_lookup(self, lat: float, lng: float) -> Supplier:
        """``/api/supplierlookup``: the Danish grid company (netselskab) at a coordinate."""
        return self._fetch(_core.plan_supplier(lat, lng))  # type: ignore[no-any-return]
