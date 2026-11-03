from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from elpriser import ZONES, UnknownZoneError, Zone

# The zone enum of https://elpriser.org/api/openapi.json (checked live in test_live.py).
API_ZONES = [
    "dk1",
    "dk2",
    "no1",
    "no2",
    "no3",
    "no4",
    "no5",
    "se1",
    "se2",
    "se3",
    "se4",
    "fi",
    "nl",
    "de",
    "fr",
    "pl",
    "it-n",
    "at",
    "be",
    "ch",
    "cz",
    "hu",
    "si",
    "es",
    "pt",
    "sk",
    "gr",
    "ro",
    "bg",
    "hr",
    "rs",
    "ee",
    "lv",
    "lt",
    "me",
    "mk",
    "al",
    "xk",
    "gb",
    "ie",
]


def test_all_forty_zones_in_api_order():
    assert [z.value for z in ZONES] == API_ZONES
    assert len(ZONES) == 40


@pytest.mark.parametrize("zone", list(Zone))
def test_every_zone_has_valid_metadata(zone: Zone):
    ZoneInfo(zone.tz)
    assert len(zone.currency) == 3
    assert len(zone.country) == 2
    assert zone.label
    assert (zone.area is not None) == (zone.group == "dk")


def test_currencies_other_than_euro():
    non_eur = {z.value: z.currency for z in Zone if z.currency != "EUR"}
    assert non_eur == {
        "dk1": "DKK",
        "dk2": "DKK",
        "no1": "NOK",
        "no2": "NOK",
        "no3": "NOK",
        "no4": "NOK",
        "no5": "NOK",
        "se1": "SEK",
        "se2": "SEK",
        "se3": "SEK",
        "se4": "SEK",
        "pl": "PLN",
        "gb": "GBP",
    }


def test_danish_zones():
    assert Zone.DK1.area == "DK1" and Zone.DK2.area == "DK2"
    assert [z for z in Zone if z.is_danish] == [Zone.DK1, Zone.DK2]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("de", Zone.DE),
        ("DE", Zone.DE),
        (" de ", Zone.DE),
        ("it-n", Zone.IT_N),
        ("IT_N", Zone.IT_N),
        ("it_n", Zone.IT_N),
        (Zone.SE3, Zone.SE3),
    ],
)
def test_parse(text, expected):
    assert Zone.parse(text) is expected


@pytest.mark.parametrize("bad", ["xx", "", "dk3", "it", None, 5])
def test_parse_rejects_unknown(bad):
    with pytest.raises(UnknownZoneError) as exc:
        Zone.parse(bad)
    assert isinstance(exc.value, ValueError)
    assert "valid zones" in str(exc.value)


def test_str_enum_compares_to_key():
    assert Zone.IT_N == "it-n"
