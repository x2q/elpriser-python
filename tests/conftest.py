"""Shared test helpers: recorded API answers and a fake transport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit

import pytest

from elpriser import Elpriser
from elpriser._core import Response

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    """A recorded API answer (see scripts/record_fixtures.py)."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def ok(payload: Any, headers: Optional[Mapping[str, str]] = None, status: int = 200) -> Response:
    return Response(status, dict(headers or {}), json.dumps(payload).encode())


def error(status: int, message: str = "boom", headers: Optional[Mapping[str, str]] = None) -> Response:
    return Response(status, dict(headers or {}), json.dumps({"error": message}).encode())


class FakeTransport:
    """Sync transport that answers from a list (in order) or a route function, and records calls."""

    def __init__(self, *responses: Any, route: Optional[Callable[[str, Dict[str, str]], Response]] = None) -> None:
        self.queue: List[Any] = list(responses)
        self.route = route
        self.calls: List[Tuple[str, Mapping[str, str], float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        self.calls.append((url, headers, timeout))
        if self.route is not None:
            return self.route(urlsplit(url).path, dict(parse_qsl(urlsplit(url).query)))
        item = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[no-any-return]

    @property
    def urls(self) -> List[str]:
        return [c[0] for c in self.calls]


def fixture_route(path: str, params: Dict[str, str]) -> Response:
    """Serve the recorded answer that matches a request."""
    if path == "/api/nordic":
        return ok(load(f"zone_{params['zone'].replace('-', '_')}"))
    table = {
        "/api/forecast": "dk_forecast_net" if params.get("mode", "").startswith("net_") else "dk_forecast",
        "/api/now": "dk_now",
        "/api/prices": "dk_prices",
        "/api/schedule": "dk_schedule",
        "/api/shelly/tariff": "dk_shelly",
        "/api/supplierlookup": "supplier_ok",
    }
    if path in table:
        return ok(load(table[path]))
    return error(404, "no such fixture")


@pytest.fixture()
def transport() -> FakeTransport:
    return FakeTransport(route=fixture_route)


@pytest.fixture()
def client(transport: FakeTransport) -> Elpriser:
    return Elpriser(transport=transport, backoff=0)
