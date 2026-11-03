"""The asynchronous client, built on httpx (``pip install "elpriser[async]"``)."""

from __future__ import annotations

import asyncio
from datetime import date as Date
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

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

if TYPE_CHECKING:  # pragma: no cover
    import httpx


class AsyncElpriser:
    """Asynchronous client for the elpriser.org API - same methods as :class:`~elpriser.Elpriser`,
    every one of them a coroutine.

    Needs httpx: ``pip install "elpriser[async]"``.

    >>> async with AsyncElpriser() as client:       # doctest: +SKIP
    ...     slot = await client.now("de")

    :param base_url: API root, default ``https://elpriser.org``.
    :param timeout: seconds per attempt, default 10.
    :param retries: extra attempts after a timeout, a connection error, 429 or 5xx (default 2).
        ``Retry-After`` is honoured (capped at 30 s), else ``backoff * 2**attempt``.
    :param backoff: base delay in seconds between retries (default 0.5).
    :param user_agent: overrides ``elpriser-python/<version>``.
    :param client: your own :class:`httpx.AsyncClient` (e.g. with a mock transport or a proxy). It is
        not closed by :meth:`aclose`.
    """

    def __init__(
        self,
        *,
        base_url: str = _core.DEFAULT_BASE_URL,
        timeout: float = _core.DEFAULT_TIMEOUT,
        retries: int = _core.DEFAULT_RETRIES,
        backoff: float = _core.DEFAULT_BACKOFF,
        user_agent: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        try:
            import httpx
        except ImportError as err:
            raise ImportError("AsyncElpriser needs httpx: pip install 'elpriser[async]'") from err
        _core.check_options(timeout, retries, backoff)
        self.base_url = _core.validate_base_url(base_url)
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.user_agent = user_agent or _core.DEFAULT_USER_AGENT
        self._httpx = httpx
        self._owns_client = client is None
        self._client: httpx.AsyncClient = client if client is not None else httpx.AsyncClient(follow_redirects=True)

    async def __aenter__(self) -> AsyncElpriser:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client (only if this object created it)."""
        if self._owns_client:
            await self._client.aclose()

    def __repr__(self) -> str:
        return f"AsyncElpriser(base_url={self.base_url!r})"

    # -- HTTP -----------------------------------------------------------

    async def _send(self, url: str, headers: Dict[str, str]) -> Response:
        resp = await self._client.get(url, headers=headers, timeout=self.timeout)
        body = resp.content
        if len(body) > _core.MAX_BODY_BYTES:
            raise OSError("response too large")
        return Response(resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, body)

    async def _fetch(self, plan: _core.Plan) -> Any:
        httpx = self._httpx
        url = _core.build_url(self.base_url, plan.path, plan.params)
        headers = _core.request_headers(self.user_agent)
        attempt = 0
        while True:
            retry_after: Optional[float] = None
            try:
                resp = await self._send(url, headers)
            except httpx.TimeoutException as err:
                failure: Exception = ElpriserTimeoutError(f"timed out after {self.timeout:g}s: {url}")
                failure.__cause__ = err
            except (httpx.TransportError, OSError) as err:
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
            await asyncio.sleep(_core.retry_delay(attempt, self.backoff, retry_after))
            attempt += 1

    async def get_json(self, path: str, **params: Any) -> Any:
        """Escape hatch: ``GET`` any API path and return the decoded JSON (no parsing)."""
        clean = {k: str(v) for k, v in params.items() if v is not None}
        return await self._fetch(_core.Plan(path, clean, lambda payload: payload))

    # -- prices (any of the 40 zones) --------------------------------------

    async def series(
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
        return await self._fetch(_core.plan_series(zone, mode, gln, feed=False))  # type: ignore[no-any-return]

    async def zone_feed(self, zone: Union[Zone, str]) -> PriceSeries:
        """Like :meth:`series`, but always the plain spot-price feed ``/api/nordic`` -
        also for ``dk1``/``dk2`` (EUR/MWh, no Danish tariffs or taxes)."""
        return await self._fetch(_core.plan_series(zone, None, None, feed=True))  # type: ignore[no-any-return]

    async def now(
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
        series = await self.series(zone, mode=mode, gln=gln)
        return series.now(at, estimate=estimate)

    async def next_hour(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        estimate: bool = False,
        at: Optional[datetime] = None,
    ) -> Optional[Slot]:
        """The price of the next hour (a forecast hour if tomorrow is not published yet)."""
        series = await self.series(zone, mode=mode, gln=gln)
        return series.next_hour(at, estimate=estimate)

    async def today(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        include_estimate: bool = False,
        at: Optional[datetime] = None,
    ) -> List[Slot]:
        """Today's hourly prices in the zone's local day."""
        series = await self.series(zone, mode=mode, gln=gln)
        return series.today(at, include_estimate=include_estimate)

    async def tomorrow(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        at: Optional[datetime] = None,
    ) -> List[Slot]:
        """Tomorrow's hourly prices; ``FORECAST`` hours until the day-ahead prices are published."""
        series = await self.series(zone, mode=mode, gln=gln)
        return series.tomorrow(at)

    async def cheapest_hours(
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
        series = await self.series(zone, mode=mode, gln=gln)
        return series.cheapest_hours(
            n,
            day=day,
            start=start,
            end=end,
            at=at,
            include_forecast=include_forecast,
            include_estimate=include_estimate,
        )

    async def best_window(
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
        series = await self.series(zone, mode=mode, gln=gln)
        return series.best_window(
            hours,
            day=day,
            start=start,
            end=end,
            at=at,
            include_forecast=include_forecast,
            include_estimate=include_estimate,
        )

    async def forecast(
        self, zone: Union[Zone, str], *, mode: Optional[AnyMode] = None, gln: Optional[str] = None
    ) -> Tuple[Day, ...]:
        """Only the forecast days (``day.kind is PriceKind.FORECAST``; ``day.horizon`` where given).

        The zone feed's forecast is elpriser.org's own statistical model - not an official
        forecast - and is replaced by the settled price when that is published.
        """
        series = await self.series(zone, mode=mode, gln=gln)
        return series.forecast_days

    async def levels(
        self,
        zone: Union[Zone, str],
        *,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        when: Optional[Date] = None,
        at: Optional[datetime] = None,
    ) -> List[Tuple[Slot, PriceLevel]]:
        """Today's (or ``when``'s) hours with their quintile :class:`PriceLevel`."""
        series = await self.series(zone, mode=mode, gln=gln)
        return series.levels(when, at=at)

    # -- Denmark-only endpoints (npm client parity) -----------------------

    async def dk_now(
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
        return await self._fetch(_core.plan_dk_now(area, mode, gln, strategy, hours, pct, max_off))  # type: ignore[no-any-return]

    async def dk_prices(
        self,
        area: Union[Zone, str] = "DK1",
        *,
        date: Union[Date, str, None] = None,
        mode: Optional[AnyMode] = None,
        gln: Optional[str] = None,
        charges: Optional[str] = None,
    ) -> DkPrices:
        """``/api/prices``: the 24 hourly prices of one date (default today, Danish time)."""
        return await self._fetch(_core.plan_dk_prices(area, date, mode, gln, charges))  # type: ignore[no-any-return]

    async def dk_schedule(
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
        return await self._fetch(_core.plan_dk_schedule(area, mode, gln, strategy, hours, pct, max_off, date))  # type: ignore[no-any-return]

    async def shelly_tariff(
        self, area: Union[Zone, str] = "DK1", *, mode: Optional[AnyMode] = None, gln: Optional[str] = None
    ) -> TariffDays:
        """``/api/shelly/tariff``: today + tomorrow in Tibber's shape, with the API's own quintile ``level``."""
        return await self._fetch(_core.plan_shelly_tariff(area, mode, gln))  # type: ignore[no-any-return]

    async def supplier_lookup(self, lat: float, lng: float) -> Supplier:
        """``/api/supplierlookup``: the Danish grid company (netselskab) at a coordinate."""
        return await self._fetch(_core.plan_supplier(lat, lng))  # type: ignore[no-any-return]
