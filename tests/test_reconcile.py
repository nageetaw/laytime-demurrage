from datetime import datetime, timedelta, timezone

import pytest

from laytime.calculator import calculate
from laytime.models import (
    ContractObservation,
    DeductionCategory,
    DocType,
    EventObservation,
    EventType,
    Fact,
    StoppageObservation,
)
from laytime.normalise import Observations
from laytime.reconcile import reconcile

CET = timezone(timedelta(hours=1))
UTC = timezone.utc


def t(text, tz=CET):
    return datetime.fromisoformat(text).replace(tzinfo=tz)


def sent(day):
    return datetime(2026, 2, day, 12, tzinfo=UTC)


def timeline_checks(timeline):
    """Checks other than the missing contract (these tests have no contract emails)."""
    return [c for c in timeline.checks if not c.message.startswith("Contract terms not found")]


def ev(port, type_, time, source, doc_type, day, is_estimate=False):
    return EventObservation(port=port, type=type_, time=time, is_estimate=is_estimate, source=source,
                            quote=f"{type_} quote", doc_type=doc_type, sent_at=sent(day))


def stop(port, category, start, end, source, doc_type, day, reason="stoppage"):
    return StoppageObservation(port=port, category=category, reason=reason, start=start, end=end,
                               start_quote="start", end_quote="end", source=source, doc_type=doc_type,
                               sent_at=sent(day))


def contract_obs(source, doc_type, day, **terms):
    return ContractObservation(
        source=source, doc_type=doc_type, sent_at=sent(day), currency="USD",
        terms={name: Fact(value=value, source=source, quote=f"{name} quote") for name, value in terms.items()},
    )


RECAP_TERMS = dict(laytime_allowed_hours=72.0, reversible=True, basis="SHINC", turn_time_hours=6.0,
                   demurrage_rate_per_day=24000.0, pro_rata=True)


@pytest.fixture
def voyage(fleet):
    return fleet.vessels[0].current_voyage


def test_addendum_replaces_the_recap_term_and_keeps_the_old_value(voyage):
    obs = Observations(contracts=[
        contract_obs("addendum.eml", DocType.ADDENDUM, 5, demurrage_rate_per_day=25500.0),
        contract_obs("recap.eml", DocType.RECAP, 1, **RECAP_TERMS),
    ])
    timeline = reconcile(obs, voyage)
    rate = timeline.contract.demurrage_rate_per_day
    assert (rate.value, rate.source) == (25500.0, "addendum.eml")
    assert [(r.value, r.source) for r in rate.replaces] == [("24000.0", "recap.eml")]
    assert timeline.contract.laytime_allowed_hours.source == "recap.eml"
    assert [c.severity for c in timeline.checks] == ["info"]


def test_missing_contract_terms_are_a_high_check(voyage):
    obs = Observations(contracts=[contract_obs("addendum.eml", DocType.ADDENDUM, 5, demurrage_rate_per_day=25500.0)])
    timeline = reconcile(obs, voyage)
    assert timeline.contract is None
    assert timeline.checks[-1].severity == "high"


def test_same_moment_in_utc_and_local_time_corroborates(voyage):
    obs = Observations(events=[
        ev("Cotonou", EventType.NOR_TENDERED, t("2026-02-15T16:30", UTC), "master.eml", DocType.MASTER_REPORT, 15),
        ev("Cotonou", EventType.NOR_TENDERED, t("2026-02-15T17:30"), "agent.eml", DocType.ARRIVAL_NOTICE, 15),
    ])
    timeline = reconcile(obs, voyage)
    [nor] = timeline.events
    assert (nor.time.source, nor.time.also_reported_in, nor.time.replaces) == ("agent.eml", ["master.eml"], [])
    assert nor.time.value == t("2026-02-15T17:30") and nor.time.value.utcoffset() == timedelta(hours=1)
    assert timeline_checks(timeline) == []


def test_final_sof_beats_a_later_master_report_and_the_difference_is_flagged(voyage):
    obs = Observations(events=[
        ev("Cotonou", EventType.HOSES_DISCONNECTED, t("2026-02-20T03:30"), "sof.eml", DocType.SOF_FINAL, 20),
        ev("Cotonou", EventType.HOSES_DISCONNECTED, t("2026-02-20T03:00"), "master.eml", DocType.MASTER_REPORT, 21),
    ])
    timeline = reconcile(obs, voyage)
    [hoses] = timeline.events
    assert hoses.time.source == "sof.eml"
    assert [r.source for r in hoses.time.replaces] == ["master.eml"]
    [check] = timeline_checks(timeline)
    assert check.severity == "medium" and "used sof.eml" in check.message


def test_estimates_are_not_events_and_the_latest_eta_wins(voyage):
    obs = Observations(events=[
        ev("Cotonou", EventType.ETA, t("2026-02-16T05:00", UTC), "noon.eml", DocType.MASTER_REPORT, 3, True),
        ev("Cotonou", EventType.ETA, t("2026-02-15T18:00"), "lineup.eml", DocType.LINEUP, 13, True),
        ev("Cotonou", EventType.HOSES_CONNECTED, t("2026-02-18T09:30"), "notice.eml", DocType.ARRIVAL_NOTICE, 14, True),
    ])
    timeline = reconcile(obs, voyage)
    assert timeline.events == []
    assert timeline.etas["Cotonou"].source == "lineup.eml"


def test_stoppage_corrected_by_final_sof_and_one_missing_from_it(voyage):
    rain = dict(port="Cotonou", category=DeductionCategory.WEATHER, reason="heavy rain")
    obs = Observations(stoppages=[
        stop(start=t("2026-02-18T21:00"), end=t("2026-02-19T00:30"), source="interim.eml",
             doc_type=DocType.SOF_INTERIM, day=19, **rain),
        stop("Cotonou", DeductionCategory.OTHER, t("2026-02-19T06:00"), t("2026-02-19T07:00"), "interim.eml",
             DocType.SOF_INTERIM, 19, reason="ghost"),
        stop(start=t("2026-02-18T21:00"), end=t("2026-02-19T01:00"), source="final.eml",
             doc_type=DocType.SOF_FINAL, day=20, **rain),
    ])
    timeline = reconcile(obs, voyage)
    [s] = timeline.stoppages
    assert (s.end.value, s.end.source) == (t("2026-02-19T01:00"), "final.eml")
    assert [r.source for r in s.end.replaces] == ["interim.eml"]
    assert s.start.also_reported_in == ["interim.eml"]
    messages = [c.message for c in timeline.checks]
    assert any("heavy rain' end" in m for m in messages)
    assert any("'ghost' in interim.eml is not in final.eml" in m for m in messages)


def test_kb_2603_from_email_observations_to_demurrage(voyage):
    """Reconcile + calculator on what the emails report, including the UTC master reports,
    the interim SOF and the addendum. Expected: USD 65,343.75 (ARCHITECTURE.md section 7)."""
    F, I, M, A = DocType.SOF_FINAL, DocType.SOF_INTERIM, DocType.MASTER_REPORT, DocType.ARRIVAL_NOTICE
    events = [
        ev("Antwerp", EventType.NOR_TENDERED, t("2026-01-30T11:00"), "email_03", A, 1),
        ev("Antwerp", EventType.NOR_TENDERED, t("2026-01-30T11:00"), "email_06", F, 2),
        ev("Antwerp", EventType.HOSES_DISCONNECTED, t("2026-02-01T09:30"), "email_06", F, 2),
        ev("Cotonou", EventType.NOR_TENDERED, t("2026-02-15T16:30", UTC), "email_15", M, 15),
        ev("Cotonou", EventType.NOR_TENDERED, t("2026-02-15T17:30"), "email_16", A, 15),
        ev("Cotonou", EventType.NOR_TENDERED, t("2026-02-15T17:30"), "email_19", F, 20),
        ev("Cotonou", EventType.ANCHOR_AWEIGH, t("2026-02-18T06:00"), "email_17", I, 19),
        ev("Cotonou", EventType.ANCHOR_AWEIGH, t("2026-02-18T06:00"), "email_19", F, 20),
        ev("Cotonou", EventType.ALL_FAST, t("2026-02-18T08:30"), "email_19", F, 20),
        ev("Cotonou", EventType.HOSES_DISCONNECTED, t("2026-02-20T02:30", UTC), "email_18", M, 20),
        ev("Cotonou", EventType.HOSES_DISCONNECTED, t("2026-02-20T03:30"), "email_19", F, 20),
    ]
    stoppages = [
        stop("Antwerp", DeductionCategory.SHORE_TERMINAL, t("2026-01-31T02:00"), t("2026-01-31T04:00"), "email_06", F, 2),
        stop("Antwerp", DeductionCategory.VESSEL_EQUIPMENT, t("2026-01-31T15:00"), t("2026-01-31T17:30"), "email_06", F, 2),
        stop("Cotonou", DeductionCategory.WAITING_FOR_BERTH, t("2026-02-15T17:30"), t("2026-02-18T06:00"), "email_19", F, 20),
        stop("Cotonou", DeductionCategory.WEATHER, t("2026-02-18T21:00"), t("2026-02-19T00:30"), "email_17", I, 19),
        stop("Cotonou", DeductionCategory.WEATHER, t("2026-02-18T21:00"), t("2026-02-19T01:00"), "email_19", F, 20),
        stop("Cotonou", DeductionCategory.SHORE_TERMINAL, t("2026-02-19T03:00"), t("2026-02-19T05:00"), "email_19", F, 20),
    ]
    recap = contract_obs("email_01", DocType.RECAP, 1, **RECAP_TERMS)
    recap.counts_as_laytime = {
        category: Fact(value=share, source="email_01", quote="clause")
        for category, share in [(DeductionCategory.SHIFTING, 0.0), (DeductionCategory.VESSEL_EQUIPMENT, 0.0),
                                (DeductionCategory.WEATHER, 0.5), (DeductionCategory.SHORE_TERMINAL, 1.0),
                                (DeductionCategory.WAITING_FOR_BERTH, 1.0)]
    }
    addendum = contract_obs("email_05", DocType.ADDENDUM, 2, demurrage_rate_per_day=25500.0)
    timeline = reconcile(Observations(contracts=[recap, addendum], events=events, stoppages=stoppages), voyage)

    result = calculate(timeline.contract, voyage.itinerary, timeline.events, timeline.stoppages,
                       now=t("2026-02-20T11:00"))
    assert [p.used_hours for p in result.ports] == [38.0, 95.5]
    assert (result.demurrage.amount, result.demurrage.status) == (65343.75, "final")
    cotonou_hoses = next(e for e in timeline.events if e.port == "Cotonou" and e.type == EventType.HOSES_DISCONNECTED)
    assert (cotonou_hoses.time.source, cotonou_hoses.time.also_reported_in) == ("email_19", ["email_18"])
