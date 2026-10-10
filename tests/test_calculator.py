from datetime import datetime

import pytest

from laytime.calculator import calculate
from laytime.fleet import PortCall
from laytime.models import Contract, DeductionCategory, EventType, Fact, PortEvent, Stoppage

CET = "+01:00"


def fact(value, source="test.eml"):
    return Fact(value=value, source=source, quote=str(value))


def at(text: str) -> datetime:
    return datetime.fromisoformat(f"{text}{CET}")


def make_contract(*, allowed=72.0, turn=6.0, rate=25500.0, exceptions_on_demurrage=True, counts=None):
    counts = counts if counts is not None else {
        DeductionCategory.SHIFTING: 0.0,
        DeductionCategory.VESSEL_EQUIPMENT: 0.0,
        DeductionCategory.WEATHER: 0.5,
        DeductionCategory.SHORE_TERMINAL: 1.0,
        DeductionCategory.WAITING_FOR_BERTH: 1.0,
    }
    return Contract(
        laytime_allowed_hours=fact(allowed),
        reversible=fact(True),
        basis=fact("SHINC"),
        turn_time_hours=fact(turn),
        demurrage_rate_per_day=fact(rate),
        currency="USD",
        pro_rata=fact(True),
        laytime_ends_on=fact(EventType.HOSES_DISCONNECTED),
        exceptions_apply_on_demurrage=fact(exceptions_on_demurrage),
        counts_as_laytime={category: fact(share) for category, share in counts.items()},
    )


def port(seq, name, function):
    return PortCall(seq=seq, port=name, country="XX", function=function, utc_offset=CET,
                    planned_arrival="2026-01-01", planned_departure="2026-01-02")


def event(port_name, type_, time):
    return PortEvent(port=port_name, type=type_, time=fact(at(time)))


def stoppage(port_name, category, start, end, reason="stoppage"):
    return Stoppage(port=port_name, category=category, reason=reason,
                    start=fact(at(start)), end=fact(at(end)))


LOAD_DISCHARGE = [port(1, "Load", "load"), port(2, "Bunkers", "bunkering"), port(3, "Discharge", "discharge")]


def test_domain_md_worked_example():
    """DOMAIN.md: 48 h allowed, 58 h used, 10 h on demurrage at USD 20,000/day = USD 8,333.33."""
    events = [
        event("Load", EventType.NOR_TENDERED, "2026-03-01T08:00"),
        event("Load", EventType.HOSES_DISCONNECTED, "2026-03-02T20:00"),
        event("Discharge", EventType.NOR_TENDERED, "2026-03-10T22:00"),
        event("Discharge", EventType.HOSES_DISCONNECTED, "2026-03-12T10:00"),
    ]
    stoppages = [stoppage("Load", DeductionCategory.VESSEL_EQUIPMENT, "2026-03-02T10:00", "2026-03-02T12:00")]
    result = calculate(make_contract(allowed=48, rate=20000), LOAD_DISCHARGE, events, stoppages, now=at("2026-03-13T00:00"))

    assert [(p.port, p.gross_hours, p.used_hours) for p in result.ports] == [("Load", 30, 28), ("Discharge", 30, 30)]
    assert result.demurrage.used_hours == 58
    assert result.demurrage.hours_on_demurrage == 10
    assert result.demurrage.amount == 8333.33
    assert result.demurrage.status == "final"


def kestrel_bay_kb_2603():
    """The KB-2603 timeline as read from the emails (answer key in ARCHITECTURE.md section 7)."""
    itinerary = [port(1, "Antwerp", "load"), port(2, "Gibraltar", "bunkering"), port(3, "Cotonou", "discharge")]
    events = [
        event("Antwerp", EventType.NOR_TENDERED, "2026-01-30T11:00"),
        event("Antwerp", EventType.ALL_FAST, "2026-01-30T10:45"),
        event("Antwerp", EventType.HOSES_DISCONNECTED, "2026-02-01T09:30"),
        event("Gibraltar", EventType.EOSP, "2026-02-05T20:00"),
        event("Cotonou", EventType.NOR_TENDERED, "2026-02-15T17:30"),
        event("Cotonou", EventType.ANCHOR_AWEIGH, "2026-02-18T06:00"),
        event("Cotonou", EventType.ALL_FAST, "2026-02-18T08:30"),
        event("Cotonou", EventType.HOSES_DISCONNECTED, "2026-02-20T03:30"),
    ]
    stoppages = [
        stoppage("Antwerp", DeductionCategory.SHORE_TERMINAL, "2026-01-31T02:00", "2026-01-31T04:00", "shore tank changeover"),
        stoppage("Antwerp", DeductionCategory.VESSEL_EQUIPMENT, "2026-01-31T15:00", "2026-01-31T17:30", "leaking manifold valve"),
        stoppage("Cotonou", DeductionCategory.WAITING_FOR_BERTH, "2026-02-15T17:30", "2026-02-18T06:00", "waiting for berth"),
        stoppage("Cotonou", DeductionCategory.WEATHER, "2026-02-18T21:00", "2026-02-19T01:00", "heavy rain"),
        stoppage("Cotonou", DeductionCategory.SHORE_TERMINAL, "2026-02-19T03:00", "2026-02-19T05:00", "shore line pressure"),
    ]
    return itinerary, events, stoppages


def test_kestrel_bay_answer_key():
    itinerary, events, stoppages = kestrel_bay_kb_2603()
    result = calculate(make_contract(), itinerary, events, stoppages, now=at("2026-02-20T11:00"))

    antwerp, cotonou = result.ports
    assert (antwerp.start, antwerp.gross_hours, antwerp.used_hours) == (at("2026-01-30T17:00"), 40.5, 38.0)
    assert {d.reason: d.deducted_hours for d in antwerp.deductions} == {
        "shore tank changeover": 0, "leaking manifold valve": 2.5,
    }
    assert (cotonou.start, cotonou.gross_hours, cotonou.used_hours) == (at("2026-02-15T23:30"), 100.0, 95.5)
    assert {d.reason: d.deducted_hours for d in cotonou.deductions} == {
        "Shifting from anchorage to berth": 2.5, "waiting for berth": 0, "heavy rain": 2.0, "shore line pressure": 0,
    }

    d = result.demurrage
    assert (d.used_hours, d.hours_on_demurrage, d.amount, d.status) == (133.5, 61.5, 65343.75, "final")
    assert d.on_demurrage_from == at("2026-02-17T09:30")


def test_stoppage_is_clipped_to_the_laytime_window():
    events = [event("Load", EventType.NOR_TENDERED, "2026-03-01T08:00"),
              event("Load", EventType.HOSES_DISCONNECTED, "2026-03-01T20:00")]
    # 12:00-16:00 overlaps laytime (from 14:00) by 2 h only.
    stoppages = [stoppage("Load", DeductionCategory.VESSEL_EQUIPMENT, "2026-03-01T12:00", "2026-03-01T16:00")]
    load = calculate(make_contract(), LOAD_DISCHARGE[:1], events, stoppages, now=at("2026-03-02T00:00")).ports[0]
    assert (load.gross_hours, load.deductions[0].deducted_hours, load.used_hours) == (6, 2, 4)


def test_overlapping_deductions_are_not_double_counted():
    events = [event("Load", EventType.NOR_TENDERED, "2026-03-01T00:00"),
              event("Load", EventType.HOSES_DISCONNECTED, "2026-03-01T20:00")]
    stoppages = [
        stoppage("Load", DeductionCategory.WEATHER, "2026-03-01T10:00", "2026-03-01T14:00"),
        stoppage("Load", DeductionCategory.VESSEL_EQUIPMENT, "2026-03-01T12:00", "2026-03-01T14:00"),
    ]
    load = calculate(make_contract(), LOAD_DISCHARGE[:1], events, stoppages, now=at("2026-03-02T00:00")).ports[0]
    # 10-12 weather at half (1 h off), 12-14 vessel breakdown not counted (2 h off).
    assert load.used_hours == 14 - 3


@pytest.mark.parametrize(("exceptions_on_demurrage", "used"), [(True, 12), (False, 14)])
def test_deductions_after_demurrage_follow_the_contract(exceptions_on_demurrage, used):
    events = [event("Load", EventType.NOR_TENDERED, "2026-03-01T00:00"),
              event("Load", EventType.HOSES_DISCONNECTED, "2026-03-01T20:00")]
    # Allowance (4 h) runs out at 10:00; the breakdown happens after that.
    stoppages = [stoppage("Load", DeductionCategory.VESSEL_EQUIPMENT, "2026-03-01T12:00", "2026-03-01T14:00")]
    contract = make_contract(allowed=4, exceptions_on_demurrage=exceptions_on_demurrage)
    result = calculate(contract, LOAD_DISCHARGE[:1], events, stoppages, now=at("2026-03-02T00:00"))
    assert result.demurrage.on_demurrage_from == at("2026-03-01T10:00")
    assert result.demurrage.used_hours == used


def test_running_laytime_is_provisional():
    events = [event("Load", EventType.NOR_TENDERED, "2026-03-01T00:00")]
    result = calculate(make_contract(allowed=4), LOAD_DISCHARGE, events, [], now=at("2026-03-01T12:00"))
    assert result.ports[0].end is None
    assert result.ports[0].used_hours == 6
    assert (result.demurrage.status, result.demurrage.hours_on_demurrage) == ("provisional", 2)
    assert any("Discharge: no NOR" in c.message for c in result.checks)


def test_cause_without_a_contract_clause_counts_in_full_and_is_flagged():
    events = [event("Load", EventType.NOR_TENDERED, "2026-03-01T00:00"),
              event("Load", EventType.HOSES_DISCONNECTED, "2026-03-01T20:00")]
    stoppages = [stoppage("Load", DeductionCategory.OTHER, "2026-03-01T10:00", "2026-03-01T12:00", "strike")]
    result = calculate(make_contract(), LOAD_DISCHARGE[:1], events, stoppages, now=at("2026-03-02T00:00"))
    assert result.ports[0].used_hours == 14
    assert [c.severity for c in result.checks if "strike" in c.message] == ["medium"]


def test_no_demurrage_when_under_the_allowance():
    events = [event("Load", EventType.NOR_TENDERED, "2026-03-01T00:00"),
              event("Load", EventType.HOSES_DISCONNECTED, "2026-03-01T20:00")]
    result = calculate(make_contract(), LOAD_DISCHARGE[:1], events, [], now=at("2026-03-02T00:00"))
    assert (result.demurrage.hours_on_demurrage, result.demurrage.amount) == (0, 0)
    assert result.demurrage.on_demurrage_from is None
