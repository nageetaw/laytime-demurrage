from datetime import datetime, timedelta, timezone

import pytest

from laytime.extract import (
    ContractAnswer,
    DeductionRuleAnswer,
    Duration,
    EventAnswer,
    EventsAnswer,
    Extraction,
    Quoted,
    StoppageAnswer,
)
from laytime.models import DeductionCategory, DocType, Email, EventType
from laytime.normalise import normalise, quote_found, resolve_time

CET = timezone(timedelta(hours=1))


@pytest.fixture
def voyage(fleet):
    return fleet.vessels[0].current_voyage


def make_email(text, sent="2026-02-20T09:15:00+00:00", file="a.eml"):
    return Email(file=file, message_id=f"<{file}>", sent_at=datetime.fromisoformat(sent), sender="x",
                 subject="KESTREL BAY", authored_text=text)


def event(type_, local_time, time_zone="local", quote="", port="Cotonou", is_estimate=False):
    return EventAnswer(port=port, type=type_, local_time=datetime.fromisoformat(local_time),
                       time_zone=time_zone, is_estimate=is_estimate, quote=quote)


def run(voyage, email, events=(), stoppages=(), contract=None, doc_type=DocType.SOF_FINAL):
    answer = None if contract else EventsAnswer(events=list(events), stoppages=list(stoppages))
    return normalise([Extraction(email=email, doc_type=doc_type, contract=contract, events=answer)], voyage)


def test_master_utc_and_agent_local_times_resolve_to_the_same_moment(voyage):
    port = voyage.itinerary[1]
    master, _ = resolve_time(datetime(2026, 2, 15, 16, 30), "UTC", port)
    agent, _ = resolve_time(datetime(2026, 2, 15, 17, 30), "local", port)
    assert master == agent
    assert agent.utcoffset() == timedelta(hours=1)


def test_stated_offset_that_differs_from_the_port_is_flagged(voyage):
    time, warning = resolve_time(datetime(2026, 2, 15, 17, 30), "+02:00", voyage.itinerary[1])
    assert time.utcoffset() == timedelta(hours=2)
    assert warning == "Cotonou: email states UTC+02:00 but the port is UTC+01:00"


def test_quote_matching_ignores_spacing_and_case():
    email = make_email("Hoses disconnected                             01 Feb 2026   09:30")
    assert quote_found("hoses disconnected 01 Feb 2026 09:30", email)
    assert not quote_found("Hoses disconnected 01 Feb 2026 09:45", email)
    assert not quote_found("", email)


def test_events_are_resolved_and_cited(voyage):
    email = make_email("HOSES DISCONNECTED    20/0230")
    obs = run(voyage, email, [event(EventType.HOSES_DISCONNECTED, "2026-02-20T02:30", "UTC",
                                    quote="HOSES DISCONNECTED 20/0230")])
    [e] = obs.events
    assert e.time == datetime(2026, 2, 20, 3, 30, tzinfo=CET)
    assert (e.port, e.source, e.doc_type) == ("Cotonou", "a.eml", DocType.SOF_FINAL)
    assert obs.checks == []


def test_invented_quote_is_flagged_but_the_fact_is_kept(voyage):
    obs = run(voyage, make_email("NOR TENDERED 15/1630"),
              [event(EventType.NOR_TENDERED, "2026-02-15T16:30", "UTC", quote="NOR tendered at 16:30 UTC")])
    assert len(obs.events) == 1
    assert ["not found" in c.message for c in obs.checks] == [True]


def test_actual_event_after_the_email_was_sent_is_ignored(voyage):
    # A wrong year or month shows up as an "actual" event in the future.
    email = make_email("HOSES DISCONNECTED 20/0230", sent="2026-01-20T09:00:00+00:00")
    obs = run(voyage, email, [event(EventType.HOSES_DISCONNECTED, "2026-02-20T02:30", "UTC",
                                    quote="HOSES DISCONNECTED 20/0230")])
    assert obs.events == []
    assert "after the email was sent" in obs.checks[0].message


def test_estimates_may_be_in_the_future(voyage):
    email = make_email("ETA COTONOU 16/0500 UTC AGW WP", sent="2026-02-03T12:05:00+00:00")
    obs = run(voyage, email, [event(EventType.ETA, "2026-02-16T05:00", "UTC",
                                    quote="ETA COTONOU 16/0500 UTC", is_estimate=True)])
    assert obs.events[0].is_estimate


def test_ports_outside_the_itinerary_are_ignored(voyage):
    obs = run(voyage, make_email("PROCEEDING LOME OPL"),
              [event(EventType.ETA, "2026-02-21T12:00", port="Lome", is_estimate=True, quote="LOME")])
    assert obs.events == []


def test_stoppage_is_resolved_and_impossible_periods_dropped(voyage):
    email = make_email("18/02/2026 21:00 Discharge stopped - heavy rain\n19/02/2026 01:00 Discharge resumed")
    good = StoppageAnswer(port="Cotonou", category=DeductionCategory.WEATHER, reason="heavy rain",
                          start=datetime(2026, 2, 18, 21), end=datetime(2026, 2, 19, 1), time_zone="local",
                          start_quote="18/02/2026 21:00 Discharge stopped - heavy rain",
                          end_quote="19/02/2026 01:00 Discharge resumed")
    backwards = good.model_copy(update={"start": datetime(2026, 2, 19, 2)})
    obs = run(voyage, email, stoppages=[good, backwards])
    [s] = obs.stoppages
    assert (s.start, s.end) == (datetime(2026, 2, 18, 21, tzinfo=CET), datetime(2026, 2, 19, 1, tzinfo=CET))
    assert ["impossible period" in c.message for c in obs.checks] == [True]


def test_contract_durations_are_converted_by_code():
    email = make_email("LAYTIME : 3 DAYS SHINC\nNOR : LAYTIME TO COMMENCE 6 HOURS AFTER NOR TENDERED\n"
                       "DEMURRAGE : USD 24,000 PER DAY PRO RATA\nWEATHER TO COUNT AS HALF LAYTIME")
    answer = ContractAnswer(
        laytime_allowed=Duration(value=3, unit="days", quote="LAYTIME : 3 DAYS SHINC"),
        reversible=None,
        basis=Quoted[str](value="SHINC", quote="3 DAYS SHINC"),
        turn_time=Duration(value=6, unit="hours", quote="LAYTIME TO COMMENCE 6 HOURS AFTER NOR TENDERED"),
        demurrage_rate_per_day=Quoted[float](value=24000, quote="USD 24,000 PER DAY PRO RATA"),
        currency="USD",
        pro_rata=None,
        laytime_ends_on=None,
        exceptions_apply_on_demurrage=None,
        deduction_rules=[DeductionRuleAnswer(category=DeductionCategory.WEATHER, counts_as_laytime=0.5,
                                             quote="WEATHER TO COUNT AS HALF LAYTIME")],
    )
    obs = run(None, email, contract=answer, doc_type=DocType.RECAP)
    [c] = obs.contracts
    assert c.terms["laytime_allowed_hours"].value == 72
    assert c.terms["turn_time_hours"].value == 6
    assert c.currency == "USD"
    assert c.counts_as_laytime[DeductionCategory.WEATHER].value == 0.5
    assert "reversible" not in c.terms
    assert obs.checks == []
