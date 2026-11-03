"""The price zones elpriser.org serves.

The list mirrors the ``zone`` enum of https://elpriser.org/api/openapi.json (40
zones). ``tz`` is the wall clock the API's ``hour`` field (0-23) is expressed in:
the 13 Nordic/NL zones follow their own wall clock (Copenhagen's for DK, NO, SE and
NL; Helsinki's for Finland: since elpriser.org cut ``fi`` into Helsinki days, hour 0
of a Finnish day is 22:00 UTC in winter), every other zone is on its own country's
clock. Until then ``fi`` was indexed by the Copenhagen hour, which 0.1.0 of this package
labelled correctly; from 0.1.1 on it expects the Helsinki days (older versions place every
Finnish hour one hour early once the API has changed).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Union

from .errors import UnknownZoneError


@dataclass(frozen=True)
class _ZoneData:
    label: str
    country: str
    currency: str
    tz: str
    group: str  # "dk" | "nordic" | "eu"
    area: Optional[str] = None  # "DK1" / "DK2" for the Danish zones


_CPH = "Europe/Copenhagen"


def _d(label: str, country: str, currency: str, tz: str, group: str, area: Optional[str] = None) -> _ZoneData:
    return _ZoneData(label, country, currency, tz, group, area)


_DATA: Dict[str, _ZoneData] = {
    "dk1": _d("DK1 Vestdanmark", "DK", "DKK", _CPH, "dk", "DK1"),
    "dk2": _d("DK2 Østdanmark", "DK", "DKK", _CPH, "dk", "DK2"),
    "no1": _d("NO1 Oslo", "NO", "NOK", _CPH, "nordic"),
    "no2": _d("NO2 Kristiansand", "NO", "NOK", _CPH, "nordic"),
    "no3": _d("NO3 Trondheim", "NO", "NOK", _CPH, "nordic"),
    "no4": _d("NO4 Tromsø", "NO", "NOK", _CPH, "nordic"),
    "no5": _d("NO5 Bergen", "NO", "NOK", _CPH, "nordic"),
    "se1": _d("SE1 Luleå", "SE", "SEK", _CPH, "nordic"),
    "se2": _d("SE2 Sundsvall", "SE", "SEK", _CPH, "nordic"),
    "se3": _d("SE3 Stockholm", "SE", "SEK", _CPH, "nordic"),
    "se4": _d("SE4 Malmö", "SE", "SEK", _CPH, "nordic"),
    "fi": _d("FI Finland", "FI", "EUR", "Europe/Helsinki", "nordic"),
    "nl": _d("NL Nederland", "NL", "EUR", _CPH, "nordic"),
    "de": _d("DE-LU Germany and Luxembourg", "DE", "EUR", "Europe/Berlin", "eu"),
    "fr": _d("FR France", "FR", "EUR", "Europe/Paris", "eu"),
    "pl": _d("PL Poland", "PL", "PLN", "Europe/Warsaw", "eu"),
    "it-n": _d("IT-North", "IT", "EUR", "Europe/Rome", "eu"),
    "at": _d("AT Austria", "AT", "EUR", "Europe/Vienna", "eu"),
    "be": _d("BE Belgium", "BE", "EUR", "Europe/Brussels", "eu"),
    "ch": _d("CH Switzerland", "CH", "EUR", "Europe/Zurich", "eu"),
    "cz": _d("CZ Czechia", "CZ", "EUR", "Europe/Prague", "eu"),
    "hu": _d("HU Hungary", "HU", "EUR", "Europe/Budapest", "eu"),
    "si": _d("SI Slovenia", "SI", "EUR", "Europe/Ljubljana", "eu"),
    "es": _d("ES Spain", "ES", "EUR", "Europe/Madrid", "eu"),
    "pt": _d("PT Portugal", "PT", "EUR", "Europe/Lisbon", "eu"),
    "sk": _d("SK Slovakia", "SK", "EUR", "Europe/Bratislava", "eu"),
    "gr": _d("GR Greece", "GR", "EUR", "Europe/Athens", "eu"),
    "ro": _d("RO Romania", "RO", "EUR", "Europe/Bucharest", "eu"),
    "bg": _d("BG Bulgaria", "BG", "EUR", "Europe/Sofia", "eu"),
    "hr": _d("HR Croatia", "HR", "EUR", "Europe/Zagreb", "eu"),
    "rs": _d("RS Serbia", "RS", "EUR", "Europe/Belgrade", "eu"),
    "ee": _d("EE Estonia", "EE", "EUR", "Europe/Tallinn", "eu"),
    "lv": _d("LV Latvia", "LV", "EUR", "Europe/Riga", "eu"),
    "lt": _d("LT Lithuania", "LT", "EUR", "Europe/Vilnius", "eu"),
    "me": _d("ME Montenegro", "ME", "EUR", "Europe/Podgorica", "eu"),
    "mk": _d("MK North Macedonia", "MK", "EUR", "Europe/Skopje", "eu"),
    "al": _d("AL Albania", "AL", "EUR", "Europe/Tirane", "eu"),
    "xk": _d("XK Kosovo", "XK", "EUR", "Europe/Belgrade", "eu"),
    "gb": _d("GB Great Britain", "GB", "GBP", "Europe/London", "eu"),
    "ie": _d("IE Ireland (SEM)", "IE", "EUR", "Europe/Dublin", "eu"),
}


class Zone(str, Enum):
    """A bidding zone. The value is the key the API uses (``Zone.IT_N.value == "it-n"``).

    Being a ``str`` enum, a member compares equal to its key, and every client
    method also accepts a plain string (``"de"``, ``"DE"``, ``"it-n"``, ``"IT_N"``).
    """

    DK1 = "dk1"
    DK2 = "dk2"
    NO1 = "no1"
    NO2 = "no2"
    NO3 = "no3"
    NO4 = "no4"
    NO5 = "no5"
    SE1 = "se1"
    SE2 = "se2"
    SE3 = "se3"
    SE4 = "se4"
    FI = "fi"
    NL = "nl"
    DE = "de"
    FR = "fr"
    PL = "pl"
    IT_N = "it-n"
    AT = "at"
    BE = "be"
    CH = "ch"
    CZ = "cz"
    HU = "hu"
    SI = "si"
    ES = "es"
    PT = "pt"
    SK = "sk"
    GR = "gr"
    RO = "ro"
    BG = "bg"
    HR = "hr"
    RS = "rs"
    EE = "ee"
    LV = "lv"
    LT = "lt"
    ME = "me"
    MK = "mk"
    AL = "al"
    XK = "xk"
    GB = "gb"
    IE = "ie"

    @property
    def label(self) -> str:
        """Human-readable name as the API reports it (``zoneInfo.name``)."""
        return _DATA[self.value].label

    @property
    def country(self) -> str:
        """ISO-like country code (``XK`` for Kosovo)."""
        return _DATA[self.value].country

    @property
    def currency(self) -> str:
        """The local currency the API converts to (``zoneInfo.currency``)."""
        return _DATA[self.value].currency

    @property
    def tz(self) -> str:
        """IANA name of the wall clock the API's ``hour`` field is expressed in."""
        return _DATA[self.value].tz

    @property
    def area(self) -> Optional[str]:
        """``"DK1"`` / ``"DK2"`` for the two Danish zones (they also have the tariff-aware
        endpoints), ``None`` for every other zone."""
        return _DATA[self.value].area

    @property
    def is_danish(self) -> bool:
        return self.area is not None

    @property
    def group(self) -> str:
        """``"dk"``, ``"nordic"`` (NO/SE/FI/NL) or ``"eu"``."""
        return _DATA[self.value].group

    @classmethod
    def parse(cls, value: Union[Zone, str]) -> Zone:
        """Turn ``"de"``, ``"DE"``, ``"it_n"`` or a :class:`Zone` into a :class:`Zone`.

        Raises :class:`~elpriser.UnknownZoneError` (a ``ValueError``) otherwise.
        """
        if isinstance(value, Zone):
            return value
        if isinstance(value, str):
            key = value.strip().lower().replace("_", "-")
            try:
                return cls(key)
            except ValueError:
                pass
        valid = ", ".join(z.value for z in cls)
        raise UnknownZoneError(f"unknown zone {value!r}; valid zones: {valid}")


ZONES = tuple(Zone)
"""All zones, in the API's order."""
