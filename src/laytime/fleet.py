"""Halcyon's fleet: loading `fleet.json` and finding which vessels an email mentions."""

import json
import re
from datetime import date, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

CARGO_FUNCTIONS = ("load", "discharge")


class PortCall(BaseModel):
    seq: int
    port: str
    country: str
    function: str  # load, discharge, or other (bunkering, canal transit, ...)
    utc_offset: str  # "+01:00"
    planned_arrival: date
    planned_departure: date

    @property
    def is_cargo(self) -> bool:
        return self.function in CARGO_FUNCTIONS

    @property
    def tz(self) -> timezone:
        return parse_utc_offset(self.utc_offset)


class Voyage(BaseModel):
    voyage_no: str
    charterer: str
    itinerary: list[PortCall]


class Vessel(BaseModel):
    name: str
    imo: str
    call_sign: str
    type: str
    dwt: int
    status: str
    current_voyage: Voyage | None = None

    def is_mentioned_in(self, text: str) -> bool:
        """Match the name, IMO or call sign as whole words, case-insensitive.

        Whole-word matching keeps "KESTREL BAY" from matching "KESTREL POINT" and
        tolerates any spacing or line break between the words of the name.
        """
        name = r"\s+".join(re.escape(word) for word in self.name.split())
        pattern = rf"\b(?:{name}|{re.escape(self.imo)}|{re.escape(self.call_sign)})\b"
        return re.search(pattern, text, re.IGNORECASE) is not None


class Fleet(BaseModel):
    operator: str
    vessels: list[Vessel]

    def mentioned_in(self, text: str) -> list[Vessel]:
        return [v for v in self.vessels if v.is_mentioned_in(text)]

    def on_voyage(self) -> list[Vessel]:
        return [v for v in self.vessels if v.current_voyage is not None]


def load_fleet(path: Path) -> Fleet:
    return Fleet.model_validate(json.loads(path.read_text()))


def parse_utc_offset(offset: str) -> timezone:
    """'+01:00' -> timezone(+1h)."""
    sign = -1 if offset.startswith("-") else 1
    hours, minutes = offset.lstrip("+-").split(":")
    return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
