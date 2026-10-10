import pytest

from laytime.fleet import Fleet


@pytest.fixture
def fleet() -> Fleet:
    """KESTREL BAY on a voyage, and KESTREL POINT, a sister ship with a similar name, in drydock."""
    vessel = {"type": "MR product tanker", "dwt": 49950}
    voyage = {
        "voyage_no": "KB-2603",
        "charterer": "Calderon Energy Trading SA",
        "itinerary": [
            {"seq": 1, "port": "Antwerp", "country": "BE", "function": "load", "utc_offset": "+01:00",
             "planned_arrival": "2026-01-30", "planned_departure": "2026-02-01"},
            {"seq": 2, "port": "Cotonou", "country": "BJ", "function": "discharge", "utc_offset": "+01:00",
             "planned_arrival": "2026-02-16", "planned_departure": "2026-02-19"},
        ],
    }
    return Fleet.model_validate(
        {
            "operator": "Halcyon Tankers",
            "vessels": [
                {**vessel, "name": "KESTREL BAY", "imo": "9314820", "call_sign": "9HKB4",
                 "status": "on_voyage", "current_voyage": voyage},
                {**vessel, "name": "KESTREL POINT", "imo": "9314832", "call_sign": "9HKP6",
                 "status": "drydock"},
            ],
        }
    )
