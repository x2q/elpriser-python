from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest

from conftest import FakeTransport, error, fixture_route
from elpriser import Elpriser, __version__
from elpriser.cli import main

AT = datetime(2026, 11, 3, 7, 20, tzinfo=timezone.utc)


def run(*argv, transport=None):
    out, err = io.StringIO(), io.StringIO()
    client = Elpriser(transport=transport or FakeTransport(route=fixture_route), backoff=0, retries=0)
    code = main(list(argv), client=client, now=AT, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_now_zone_de():
    code, out, err = run("now", "--zone", "DE")
    assert code == 0 and err == ""
    assert out.startswith("DE ")
    assert "2026-11-03 08:00 CET" in out and "0.3013 EUR/kWh" in out and "301.31 EUR/MWh" in out and "actual" in out


def test_now_json():
    code, out, _ = run("now", "-z", "se3", "--json")
    data = json.loads(out)
    assert code == 0 and data["currency"] == "SEK" and data["unit"] == "SEK/kWh" and data["published_unit"] == "EUR/MWh"
    assert data["price"] == pytest.approx(data["price_mwh"] * 10.9475 / 1000, abs=1e-5)


def test_default_zone_is_dk1_with_tariffs():
    code, out, _ = run("now")
    assert code == 0 and "DKK/kWh" in out and out.startswith("DK1")


def test_next_today_tomorrow():
    assert "09:00" in run("next", "-z", "de")[1]
    code, out, _ = run("today", "-z", "de")
    assert code == 0 and len(out.strip().splitlines()) == 24 and "VERY_CHEAP" in out
    code, out, _ = run("tomorrow", "-z", "de", "--json")
    rows = json.loads(out)
    assert len(rows) == 24 and {r["kind"] for r in rows} == {"forecast"} and rows[0]["min"] is not None


def test_cheapest_and_window():
    code, out, _ = run("cheapest", "-z", "de", "-n", "4", "--settled-only")
    assert code == 0 and len(out.strip().splitlines()) == 4
    code, out, _ = run("window", "-z", "de", "-n", "3", "--json")
    data = json.loads(out)
    assert code == 0 and data["hours"] == 3 and len(data["slots"]) == 3


def test_forecast_lists_forecast_days_only():
    code, out, _ = run("forecast", "-z", "de")
    assert code == 0 and len(out.strip().splitlines()) == 6 and "D+2" in out
    code, out, err = run("forecast", "-z", "xk")
    assert code == 1 and "no forecast" in err


def test_gb_now_exit_1_and_estimate_hint():
    code, out, err = run("now", "-z", "gb")
    assert code == 1 and out == "" and "--estimate" in err
    code, out, _ = run("now", "-z", "gb", "--estimate")
    assert code == 0 and "estimate" in out and "P10-P90" in out and "GBP/kWh" in out


def test_zones_listing():
    code, out, _ = run("zones")
    assert code == 0 and len(out.strip().splitlines()) == 40 and "it-n" in out


def test_usage_errors():
    assert run("now", "-z", "narnia")[0] == 2
    code, _, err = run("now", "-z", "de", "--mode", "spot_ex")
    assert code == 2 and "Danish" in err
    assert main(["bogus"], err=io.StringIO(), out=io.StringIO()) == 2


def test_version_and_help_exit_zero(capsys):
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out
    assert main(["--help"]) == 0


def test_api_error_exit_3():
    code, _, err = run("now", "-z", "de", transport=FakeTransport(error(500, "down")))
    assert code == 3 and "down" in err
