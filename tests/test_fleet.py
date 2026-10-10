from datetime import timedelta

import pytest

from laytime.fleet import Fleet, parse_utc_offset


@pytest.fixture
def fleet() -> Fleet:
    vessel = {"type": "MR product tanker", "dwt": 49950, "status": "idle"}
    return Fleet.model_validate(
        {
            "operator": "Halcyon Tankers",
            "vessels": [
                {**vessel, "name": "KESTREL BAY", "imo": "9314820", "call_sign": "9HKB4"},
                {**vessel, "name": "KESTREL POINT", "imo": "9314832", "call_sign": "9HKP6"},
            ],
        }
    )


@pytest.mark.parametrize(
    "text",
    [
        "MT KESTREL BAY - ARRIVAL ANTWERP",
        "Vessel: Kestrel Bay",
        "MT KESTREL\nBAY arrived",
        "IMO 9314820",
        "call sign 9HKB4",
    ],
)
def test_finds_vessel_by_name_imo_or_call_sign(fleet, text):
    assert [v.name for v in fleet.mentioned_in(text)] == ["KESTREL BAY"]


def test_similar_names_do_not_match_each_other(fleet):
    text = "KESTREL POINT - drydock progress report no. 6 (IMO 9314832)"
    assert [v.name for v in fleet.mentioned_in(text)] == ["KESTREL POINT"]


def test_other_vessels_are_not_matched(fleet):
    assert fleet.mentioned_in("NOR TENDERED - COTONOU. Vessel: MT ALDER STAR") == []


def test_number_inside_a_longer_number_is_not_an_imo(fleet):
    assert fleet.mentioned_in("Reference 193148201") == []


@pytest.mark.parametrize(
    ("offset", "expected"),
    [("+01:00", timedelta(hours=1)), ("-03:30", timedelta(hours=-3, minutes=-30)), ("+00:00", timedelta(0))],
)
def test_parse_utc_offset(offset, expected):
    assert parse_utc_offset(offset).utcoffset(None) == expected
