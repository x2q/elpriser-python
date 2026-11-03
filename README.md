# elpriser

Typed Python client for the [elpriser.org](https://elpriser.org) electricity price API:
**40 European price zones**, current and next hour, today and tomorrow hour by hour, cheapest
hours, best window, forecast days with the settled-versus-forecast flag, price levels, and the
Danish tariff-aware endpoints. No API key.

- Sync client with **no dependencies** (standard library only) and an async client (`httpx`, optional extra).
- Fully typed (`py.typed`), Python 3.9+.
- Timeouts, retries with `Retry-After`, a proper `User-Agent` (`elpriser-python/<version>`), clear exceptions.
- A tiny CLI: `elpriser now --zone DE`.
- Optional `to_dataframe()` for pandas.

The JavaScript/TypeScript sibling is [elpriser-client](https://github.com/x2q/elpriser-client);
the MCP server for LLM clients is [elpriser-mcp](https://github.com/x2q/elpriser-mcp).

> Not yet on PyPI. Install from GitHub for now:
> `pip install "git+https://github.com/x2q/elpriser-python"`.
> Once published it will be `pip install elpriser` (extras: `elpriser[async]`, `elpriser[pandas]`, `elpriser[all]`).

## Quick start

```python
from elpriser import Elpriser, Zone

client = Elpriser()

slot = client.now(Zone.DE)  # the current hour
print(slot.price, "EUR/kWh", slot.local_start)  # 0.30131 EUR/kWh 2026-11-03 08:00:00+01:00

for s in client.cheapest_hours("se3", 4):  # 4 cheapest hours in the next 24 h
    print(s.local_start, s.price, s.kind.value)  # kind: "actual" or "forecast"

window = client.best_window(Zone.NL, 3)  # cheapest 3 consecutive hours (EV charging, dishwasher ...)
print(window.local_start, window.average)
```

Every convenience method makes one request. To ask several questions, fetch the series once:

```python
s = client.series("pl")  # one HTTP request
s.now()
s.next_hour()
s.today()
s.tomorrow()
s.cheapest_hours(3)
s.best_window(2)
s.levels()  # [(slot, PriceLevel.CHEAP), ...] for today
s.forecast_days  # only the days that are forecasts
s.currency, s.rate, s.rate_date  # "PLN", 4.4185, "2026-11-02"
```

### Async

```python
import asyncio
from elpriser import AsyncElpriser  # pip install "elpriser[async]"


async def main():
    async with AsyncElpriser() as client:
        print(await client.now("fr"))


asyncio.run(main())
```

Same methods as the sync client, all coroutines. Pass `client=httpx.AsyncClient(...)` to bring your own.

### Command line

```console
$ elpriser now --zone DE
DE     2026-11-03 08:00 CET     0.3013 EUR/kWh  (301.31 EUR/MWh, actual)
$ elpriser cheapest -z se3 -n 3 --settled-only
$ elpriser window -z dk1 -n 3
$ elpriser forecast -z pl
$ elpriser today -z gb --estimate --json
$ elpriser zones
```

Commands: `now`, `next`, `today`, `tomorrow`, `cheapest`, `window`, `forecast`, `zones`; `--json` for scripts.
Exit status: 0 ok, 1 no price for what you asked, 2 bad usage, 3 API or network error.
`python -m elpriser ...` works too.

## What the prices are

| Zones | Endpoint | What you get |
|---|---|---|
| `dk1`, `dk2` | `/api/forecast` | **DKK/kWh as a customer pays it**: spot + Energinet's system and transmission tariffs + electricity duty + VAT (`mode="inkl_alt"`, the default). `Mode.SPOT_EX`, `Mode.NET_INKL_ALT` + `gln=` (adds one grid company's tariff) and the other modes pick a different price. Today plus nine forecast days. |
| the other 38 | `/api/nordic` | **Spot price only**: no VAT, duties or grid fees, so it is not what a consumer pays. Published in EUR/MWh (GBP/MWh for `gb`) and converted by the client to local currency per kWh with the ECB rate the API sends. |

`Slot.price` is always **local currency per kWh** (`series.currency`, e.g. `SEK`).
On the zone feed the numbers as published are kept alongside: `slot.price_mwh` (in `series.unit`, e.g. `EUR/MWh`),
`series.rate`, `series.rate_date`, `series.rate_source`. `price == price_mwh * rate / 1000`.
`client.zone_feed("dk1")` gives the plain spot feed for a Danish zone as well.

`Zone` is an enum of all 40 zones (`Zone.DE`, `Zone.IT_N`, `Zone.SE3` ...) with `.label`, `.country`, `.currency`,
`.tz`; every method also takes `"de"`, `"DE"` or `"it-n"`. The list is checked against the live
`/api/openapi.json` by the test suite.

Times: `slot.start` / `slot.end` are timezone-aware **UTC** datetimes (arithmetic across daylight saving stays correct);
`slot.local_start`, `slot.hour` and `slot.local_date` are the zone's own wall clock, which is also what "today" and
"tomorrow" mean. A 25-hour day has 25 slots, a 23-hour day 23.

### Settled, forecast, estimate

- `slot.kind is PriceKind.ACTUAL`: the settled day-ahead price.
- `PriceKind.FORECAST`: elpriser.org's **own statistical model, not an official forecast**; it is replaced by the settled
  price when that is published (about 13:00 CET for the next day). Forecast hours carry `min` / `max` (P10 / P90), and
  `day.horizon` tells how many days ahead. Per-horizon back-test numbers are in `series.accuracy`
  (`series.accuracy_for(3)`, `series.is_low_confidence(3)`). The model ranks the cheapest hours better than it hits the
  price level. So `tomorrow()` before the auction results are out returns forecast hours - check `slot.kind`, or pass
  `include_forecast=False` to `cheapest_hours()` / `best_window()` to use settled prices only.
- `PriceKind.ESTIMATE`: for `gb`, `pt` and `ie` the API may attach an `estimate` for hours of a settled day that have no
  price yet (GB: the hours of today that have not traded; PT/IE: the last hour, 23). It is never mixed in unless you ask:
  `client.now("gb", estimate=True)`, `series.today(include_estimate=True)`, `series.to_dataframe()`.
- **Great Britain is not a day-ahead price**: it is the Market Index Price (within-day), an hour is priced only once it has
  traded, and there is no tomorrow. `series.now()` is therefore `None` for `gb` unless you pass `estimate=True`;
  `series.latest_known()` returns the newest priced hour.

### Price levels

`PriceLevel` (`VERY_CHEAP` ... `VERY_EXPENSIVE`) ranks an hour among the priced hours of its local day in quintiles - the
same scale and algorithm as the `level` field of `/api/shelly/tariff`, which the test suite verifies against the live API.
Use `series.levels()`, `series.level(slot)` or `price_levels([...])`. `client.shelly_tariff()` returns the API's own levels.

### Cheapest hours and best window

```python
s.cheapest_hours(5)  # the 5 cheapest hours, in time order, scattered anywhere in the window
s.best_window(5)  # the cheapest 5 consecutive hours
s.best_window(3, day=date(2026, 11, 4), include_forecast=False)
s.cheapest_hours(2, start=t0, end=t1)  # any window; default is the next 24 hours
```

Hours without a price never count; fewer than `n` hours come back if fewer are priced.

## Denmark-only endpoints (parity with the npm client)

| npm `elpriser-client` | this package |
|---|---|
| `now()` | `client.dk_now(area, mode=, strategy=, hours=)` (or `client.now("dk1")` for the Slot) |
| `prices()` | `client.dk_prices(area, date=, mode=, gln=, charges=)` |
| `schedule()` | `client.dk_schedule(area, strategy=, hours=, pct=, max_off=, date=)` |
| `forecast()` | `client.forecast("dk1")` / `client.series("dk1").forecast_days` |
| `shellyTariff()` | `client.shelly_tariff(area)` |
| `supplierLookup()` | `client.supplier_lookup(lat, lng)` |

`Mode` and `Strategy` enums list the values the API accepts. Invalid combinations (a `net_` mode without `gln`, a `mode`
on a non-Danish zone, hours out of range) raise `ValueError` before any request.

## pandas

```python
df = client.series("gb").to_dataframe()  # pip install "elpriser[pandas]"
df[["price", "kind", "horizon"]]
```

Index `start` (timezone-aware, the zone's wall clock); columns `end`, `price`, `min`, `max`, `price_mwh`, `min_mwh`,
`max_mwh`, `kind`, `horizon`, `zone`, `currency`.

## Errors, timeouts, retries

```python
Elpriser(timeout=10, retries=2, backoff=0.5, user_agent=None, base_url="https://elpriser.org")
```

Timeouts, connection errors, 429 and 5xx are retried `retries` times (the server's `Retry-After` is honoured, capped at
30 s; otherwise `backoff * 2**attempt`). Other 4xx are not retried. Everything raised derives from `ElpriserError`:

| Exception | When |
|---|---|
| `UnknownZoneError` (also `ValueError`) | the zone is not one of the 40 |
| `BadRequestError` / `NoDataError` | HTTP 400 / 404 (`.status`, `.message` carry the API's own text) |
| `RateLimitedError` / `ServerError` | HTTP 429 / 5xx after the retries (`.retry_after`) |
| `ElpriserTimeoutError`, `ElpriserConnectionError` | no HTTP answer |
| `ElpriserParseError` | a 200 that is not the shape this version understands |

The API's CDN answers 403 to Python's default `Python-urllib` agent, so the client always sends its own; if you set
`user_agent=`, use something like `"my-app/1.0"`.

## Data and attribution

Prices come from elpriser.org, which aggregates: Energi Data Service (Energinet) for Denmark; Bundesnetzagentur |
SMARD.de via Energy-Charts.info (CC BY 4.0) for DE-LU, FR, PL, IT-North, AT, BE, CH, CZ, HU and SI; the ENTSO-E
Transparency Platform for ES, PT, SK, GR, RO, BG, HR, RS, EE, LV, LT, ME, MK, AL, XK and the Nordic/NL zones; SEMOpx for
Ireland; and Elexon BMRS for Great Britain (*Contains BMRS data (c) Elexon Limited copyright and database right*).
Sub-hourly prices are averaged to hourly means by elpriser.org. Keep the attribution if you republish the data.
See <https://elpriser.org/api> and `/api/openapi.json`. The service is free; please keep request volume reasonable
(responses are edge-cached for minutes, so polling more often than every few minutes gains nothing).

## Development

```console
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
pytest                              # offline, against recorded API answers in tests/fixtures
ELPRISER_LIVE=1 pytest tests/test_live.py   # opt-in: validates parsers, zone table and fixtures against the live API
python scripts/record_fixtures.py   # re-record the fixtures (read-only GETs)
```

## Dansk

Python-klient til elpriser.org: spotpriser for 40 europæiske prisområder, aktuel og næste time, billigste timer,
bedste sammenhængende vindue og prognose. For `dk1`/`dk2` er prisen i kr/kWh inkl. Energinets tariffer, elafgift og moms
(`mode="inkl_alt"`); tilføj `mode="net_inkl_alt", gln="..."` for dit netselskabs tariff. Uden for Danmark er det
børsprisen uden moms, afgifter og nettariffer. Prognosedage er elpriser.orgs egen model (ikke en officiel prognose) og
markeres med `kind == "forecast"`. Hurtig prøve: `pip install "git+https://github.com/x2q/elpriser-python"` og derefter
`elpriser now --zone DK1`.

## License

MIT. Data © their respective sources, see above.
