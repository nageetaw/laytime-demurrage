from datetime import datetime, timedelta, timezone

from laytime.models import EventType, Fact, PortEvent
from laytime.pipeline import VoyageResult
from laytime.reconcile import Timeline
from laytime.report import _port_calls, position

CET = timezone(timedelta(hours=1))


def result(fleet, *events):
    timeline = Timeline(contract=None, events=list(events), stoppages=[], etas={}, checks=[])
    return VoyageResult(vessel=fleet.vessels[0], emails=[], timeline=timeline, laytime=None, checks=[],
                        as_of=datetime(2026, 2, 20, tzinfo=timezone.utc))


def ev(port, type_, day, hour):
    return PortEvent(port=port, type=type_, time=Fact(value=datetime(2026, 2, day, hour, tzinfo=CET),
                                                      source="x.eml", quote="q"))


def test_arrival_falls_back_to_anchored_when_no_eosp_was_reported(fleet):
    antwerp, cotonou = _port_calls(result(
        fleet,
        ev("Antwerp", EventType.ANCHORED, 1, 20),
        ev("Cotonou", EventType.EOSP, 15, 16), ev("Cotonou", EventType.ANCHORED, 15, 17),
    ))
    assert (antwerp.arrived_event, antwerp.arrived.value.hour) == (EventType.ANCHORED, 20)
    assert (cotonou.arrived_event, cotonou.arrived.value.hour) == (EventType.EOSP, 16)


def test_position_after_sailing_from_the_last_cargo_port(fleet):
    r = result(fleet, ev("Cotonou", EventType.EOSP, 15, 16), ev("Cotonou", EventType.SAILED, 20, 7))
    assert position(r) == "Sailed from Cotonou on 20 Feb 07:00. The voyage is complete."


def test_position_while_at_a_port(fleet):
    r = result(fleet, ev("Cotonou", EventType.EOSP, 15, 16), ev("Cotonou", EventType.NOR_TENDERED, 15, 17))
    assert position(r) == "At Cotonou: NOR tendered on 15 Feb 17:00."
