"""Re-record tests/fixtures/*.json from the live API (read-only GETs, a dozen requests).

    python scripts/record_fixtures.py

The files are the API's answers byte-for-byte parsed and re-dumped as JSON; nothing is
edited or invented. tests/fixtures/manifest.json notes when and from where each was taken.
The tests pass a fixed ``at`` timestamp, so re-recording changes the data but not the
assertions' structure - expect to adjust the few assertions that quote concrete prices.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from elpriser import Elpriser

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
GLN = "5790000704842"  # the example GLN of https://elpriser.org/llms.txt (/api/raw/tariff)

# name -> (path, params)
REQUESTS = {
    "zone_de": ("/api/nordic", {"zone": "de"}),
    "zone_pl": ("/api/nordic", {"zone": "pl"}),
    "zone_se3": ("/api/nordic", {"zone": "se3"}),
    "zone_dk1": ("/api/nordic", {"zone": "dk1"}),
    "zone_gb": ("/api/nordic", {"zone": "gb"}),
    "zone_pt": ("/api/nordic", {"zone": "pt"}),
    "zone_ie": ("/api/nordic", {"zone": "ie"}),
    "zone_xk": ("/api/nordic", {"zone": "xk"}),
    "dk_forecast": ("/api/forecast", {"area": "DK1", "mode": "inkl_alt"}),
    "dk_forecast_net": ("/api/forecast", {"area": "DK2", "mode": "net_inkl_alt", "gln": GLN}),
    "dk_now": ("/api/now", {"area": "DK1", "mode": "inkl_alt"}),
    "dk_prices": ("/api/prices", {"area": "DK1", "mode": "inkl_alt"}),
    "dk_schedule": ("/api/schedule", {"area": "DK1", "mode": "inkl_alt", "strategy": "cheapest_n", "hours": 6}),
    "dk_shelly": ("/api/shelly/tariff", {"area": "DK1", "mode": "inkl_alt"}),
    "supplier_ok": ("/api/supplierlookup", {"lat": 55.6761, "lng": 12.5683}),
    "supplier_none": ("/api/supplierlookup", {"lat": 0.0, "lng": 0.0}),
}


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    client = Elpriser(user_agent="elpriser-python-fixture-recorder/1")
    manifest = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": {}}
    for name, (path, params) in REQUESTS.items():
        payload = client.get_json(path, **params)
        (FIXTURES / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        manifest["files"][name] = {"path": path, "params": params}
        print("recorded", name)
    (FIXTURES / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
