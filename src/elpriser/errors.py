"""Exceptions raised by the client. Everything derives from :class:`ElpriserError`."""

from __future__ import annotations

from typing import Optional


class ElpriserError(Exception):
    """Base class for every error this package raises on purpose."""


class UnknownZoneError(ElpriserError, ValueError):
    """The zone is not one of the zones elpriser.org serves (see :class:`elpriser.Zone`)."""


class ElpriserConnectionError(ElpriserError):
    """The request never produced an HTTP answer: DNS, TLS, refused, reset or timed out.

    Raised only after the configured retries are used up. ``__cause__`` holds the
    underlying exception.
    """


class ElpriserTimeoutError(ElpriserConnectionError):
    """The request timed out (after the configured retries)."""


class ElpriserHTTPError(ElpriserError):
    """The API answered with an HTTP error status.

    ``status`` is the code, ``message`` the API's own ``error`` text when it sent
    one, ``url`` what was requested.
    """

    def __init__(self, status: int, message: str, url: str) -> None:
        super().__init__(f"HTTP {status} from {url}: {message}")
        self.status = status
        self.message = message
        self.url = url


class BadRequestError(ElpriserHTTPError):
    """HTTP 400: the API rejected a parameter (unknown zone, mode, bad date ...)."""


class NoDataError(ElpriserHTTPError):
    """HTTP 404: the API has no prices for this request (yet)."""


class RateLimitedError(ElpriserHTTPError):
    """HTTP 429. ``retry_after`` is the server's ``Retry-After`` in seconds, if sent."""

    def __init__(self, status: int, message: str, url: str, retry_after: Optional[float]) -> None:
        super().__init__(status, message, url)
        self.retry_after = retry_after


class ServerError(ElpriserHTTPError):
    """HTTP 5xx (after the configured retries). A 503 may carry ``retry_after``."""

    def __init__(self, status: int, message: str, url: str, retry_after: Optional[float] = None) -> None:
        super().__init__(status, message, url)
        self.retry_after = retry_after


class ElpriserParseError(ElpriserError):
    """The API answered 200 but the body is not the shape this version understands."""
