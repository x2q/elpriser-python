"""Command line: ``elpriser now --zone DE``.

Exit status: 0 ok, 1 no price for what was asked, 2 bad usage, 3 API or network error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, TextIO

from . import __version__, _core
from .client import Elpriser
from .errors import ElpriserError, UnknownZoneError
from .models import Day, PriceSeries, Slot, Window
from .zones import ZONES, Zone

EXIT_OK, EXIT_NO_DATA, EXIT_USAGE, EXIT_ERROR = 0, 1, 2, 3


def _slot_dict(slot: Slot, series: PriceSeries) -> Dict[str, Any]:
    return {
        "start": slot.local_start.isoformat(),
        "end": slot.local_end.isoformat(),
        "price": slot.price,
        "currency": series.currency,
        "unit": series.price_unit,
        "kind": slot.kind.value,
        "min": slot.min,
        "max": slot.max,
        "price_mwh": slot.price_mwh,
        "published_unit": series.unit,
    }


def _fmt_price(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.4f}"


def _slot_line(slot: Slot, series: PriceSeries, *, with_zone: bool = False) -> str:
    stamp = slot.local_start.strftime("%Y-%m-%d %H:%M %Z")
    parts = [
        f"{series.zone.value.upper():<5}" if with_zone else "",
        stamp,
        f"{_fmt_price(slot.price):>9} {series.price_unit}",
    ]
    notes = [slot.kind.value]
    if slot.price_mwh is not None:
        notes.insert(0, f"{slot.price_mwh:g} {series.unit}")
    if slot.min is not None and slot.max is not None:
        notes.append(f"P10-P90 {_fmt_price(slot.min)}..{_fmt_price(slot.max)}")
    return "  ".join(p for p in parts if p) + f"  ({', '.join(notes)})"


def _emit(out: TextIO, as_json: bool, payload: Any, lines: List[str]) -> None:
    if as_json:
        out.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        out.write("\n".join(lines) + "\n")


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-z", "--zone", default="DK1", help="price zone, e.g. DE, SE3, it-n (default: DK1; see `elpriser zones`)"
    )
    common.add_argument("--mode", help="Danish zones only: spot_ex, spot_inkl, inkl_alt (default), net_inkl_alt ...")
    common.add_argument("--gln", help="Danish zones only: grid company GLN for the net_ modes")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument(
        "--estimate", action="store_true", help="fill unpriced hours with elpriser.org's estimate (gb, pt, ie)"
    )
    common.add_argument(
        "--timeout", type=float, default=_core.DEFAULT_TIMEOUT, help="seconds per request (default: %(default)s)"
    )
    common.add_argument("--base-url", default=_core.DEFAULT_BASE_URL, help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(
        prog="elpriser",
        description="Electricity prices from elpriser.org: 40 European zones, no API key.",
        epilog="Outside Denmark the price is the spot price (no VAT, duties or grid fees). Forecast hours are "
        "elpriser.org's own model, not an official forecast.",
    )
    parser.add_argument("--version", action="version", version=f"elpriser-python {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("now", parents=[common], help="price of the current hour")
    sub.add_parser("next", parents=[common], help="price of the next hour")
    sub.add_parser("today", parents=[common], help="today's hourly prices")
    sub.add_parser("tomorrow", parents=[common], help="tomorrow's hourly prices (forecast until published)")
    cheapest = sub.add_parser("cheapest", parents=[common], help="the N cheapest hours in the next 24 hours")
    cheapest.add_argument("-n", "--hours", type=int, default=3, help="how many hours (default: %(default)s)")
    cheapest.add_argument("--settled-only", action="store_true", help="ignore forecast hours")
    window = sub.add_parser("window", parents=[common], help="cheapest run of N consecutive hours in the next 24 hours")
    window.add_argument("-n", "--hours", type=int, default=3, help="window length (default: %(default)s)")
    window.add_argument("--settled-only", action="store_true", help="ignore forecast hours")
    sub.add_parser("forecast", parents=[common], help="the forecast days (not settled prices)")
    sub.add_parser("zones", help="list the zones")
    return parser


def _zones_command(out: TextIO, as_json: bool = False) -> int:
    rows = [
        {"zone": z.value, "name": z.label, "country": z.country, "currency": z.currency, "timezone": z.tz}
        for z in ZONES
    ]
    _emit(out, as_json, rows, [f"{r['zone']:<5} {r['currency']}  {r['name']}" for r in rows])
    return EXIT_OK


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    client: Optional[Elpriser] = None,
    now: Optional[datetime] = None,
    out: Optional[TextIO] = None,
    err: Optional[TextIO] = None,
) -> int:
    """Run the CLI. ``client``, ``now``, ``out`` and ``err`` exist for tests."""
    out = out or sys.stdout
    err = err or sys.stderr
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code) if isinstance(exit_.code, int) else EXIT_USAGE
    if args.command == "zones":
        return _zones_command(out)

    try:
        zone = Zone.parse(args.zone)
        api = client or Elpriser(base_url=args.base_url, timeout=args.timeout)
        series = api.series(zone, mode=args.mode, gln=args.gln)
    except (UnknownZoneError, ValueError) as exc:
        err.write(f"elpriser: {exc}\n")
        return EXIT_USAGE
    except ElpriserError as exc:
        err.write(f"elpriser: {exc}\n")
        return EXIT_ERROR

    est = args.estimate
    cmd = args.command
    if cmd in ("now", "next"):
        slot = series.now(now, estimate=est) if cmd == "now" else series.next_hour(now, estimate=est)
        if slot is None:
            err.write(
                f"elpriser: no price for the {'current' if cmd == 'now' else 'next'} hour in {zone.value}"
                + (" yet (try --estimate)" if zone.value in ("gb", "pt", "ie") and not est else "")
                + "\n"
            )
            return EXIT_NO_DATA
        _emit(out, args.json, _slot_dict(slot, series), [_slot_line(slot, series, with_zone=True)])
        return EXIT_OK
    if cmd in ("today", "tomorrow"):
        slots = (
            series.today(now, include_estimate=est) if cmd == "today" else series.tomorrow(now, include_estimate=est)
        )
        if not slots:
            err.write(f"elpriser: no prices for {cmd} in {zone.value}\n")
            return EXIT_NO_DATA
        levels = {s.start: lv.value for s, lv in series.levels(slots[0].local_date)}
        _emit(
            out,
            args.json,
            [dict(_slot_dict(s, series), level=levels.get(s.start)) for s in slots],
            [
                _slot_line(s, series) + (f"  {levels[s.start]}" if s.start in levels and s.price is not None else "")
                for s in slots
            ],
        )
        return EXIT_OK
    if cmd == "cheapest":
        picked = series.cheapest_hours(args.hours, at=now, include_forecast=not args.settled_only, include_estimate=est)
        if not picked:
            err.write(f"elpriser: no priced hours for {zone.value} in the next 24 hours\n")
            return EXIT_NO_DATA
        _emit(out, args.json, [_slot_dict(s, series) for s in picked], [_slot_line(s, series) for s in picked])
        return EXIT_OK
    if cmd == "window":
        win: Optional[Window] = series.best_window(
            args.hours, at=now, include_forecast=not args.settled_only, include_estimate=est
        )
        if win is None:
            err.write(f"elpriser: no {args.hours}-hour run of priced hours for {zone.value} in the next 24 hours\n")
            return EXIT_NO_DATA
        payload = {
            "start": win.local_start.isoformat(),
            "end": win.local_end.isoformat(),
            "hours": win.hours,
            "average": win.average,
            "unit": series.price_unit,
            "slots": [_slot_dict(s, series) for s in win.slots],
        }
        head = f"{win.local_start.strftime('%Y-%m-%d %H:%M')} - {win.local_end.strftime('%H:%M %Z')}  avg {_fmt_price(win.average)} {series.price_unit}"
        _emit(out, args.json, payload, [head] + ["  " + _slot_line(s, series) for s in win.slots])
        return EXIT_OK
    if cmd == "forecast":
        days: Sequence[Day] = series.forecast_days
        if not days:
            err.write(f"elpriser: {zone.value} has no forecast days in this answer\n")
            return EXIT_NO_DATA
        rows = []
        lines = []
        for d in days:
            priced = [s.price for s in d.slots if s.price is not None]
            avg = round(sum(priced) / len(priced), 5) if priced else None
            rows.append(
                {
                    "date": d.date.isoformat(),
                    "kind": d.kind.value,
                    "horizon": d.horizon,
                    "average": avg,
                    "unit": series.price_unit,
                    "hours": [_slot_dict(s, series) for s in d.slots],
                }
            )
            lines.append(
                f"{d.date.isoformat()}  forecast  {f'D+{d.horizon}' if d.horizon else '    '}  avg {_fmt_price(avg)} {series.price_unit}"
                f"  min {_fmt_price(min(priced) if priced else None)}  max {_fmt_price(max(priced) if priced else None)}"
            )
        _emit(out, args.json, rows, lines)
        return EXIT_OK
    return EXIT_USAGE  # pragma: no cover - argparse already rejects unknown commands
