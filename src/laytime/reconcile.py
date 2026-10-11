"""Merge what different emails say into one contract and one timeline.

Contract terms: a later email replaces an earlier one (an addendum amends the recap).
Port events and stoppages: the more authoritative document wins, by document type and then by
when it was sent. The other emails are kept as corroborating or superseded sources, and any
disagreement becomes a check.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from laytime.fleet import Voyage
from laytime.models import (
    Check,
    Contract,
    ContractObservation,
    DeductionCategory,
    DocType,
    EventObservation,
    EventType,
    Fact,
    PortEvent,
    Stoppage,
    StoppageObservation,
    Superseded,
)
from laytime.normalise import Observations

log = logging.getLogger(__name__)

PRIORITY = {
    DocType.SOF_FINAL: 5,
    DocType.SOF_INTERIM: 4,
    DocType.ARRIVAL_NOTICE: 3,
    DocType.PORT_CALL_REPORT: 3,
    DocType.MASTER_REPORT: 2,
    DocType.LINEUP: 1,
}
SAME_TIME = timedelta(minutes=1)
REQUIRED_TERMS = (
    "laytime_allowed_hours", "reversible", "basis", "turn_time_hours", "demurrage_rate_per_day", "pro_rata",
)
# Events that happen once per port call. Others (e.g. pilot on board, inbound and outbound)
# can legitimately repeat and are not needed for laytime or the position.
SINGLE_EVENTS = {
    EventType.EOSP, EventType.ANCHORED, EventType.NOR_TENDERED, EventType.ANCHOR_AWEIGH, EventType.ALL_FAST,
    EventType.HOSES_CONNECTED, EventType.COMMENCED_CARGO, EventType.COMPLETED_CARGO,
    EventType.HOSES_DISCONNECTED, EventType.SAILED,
}


@dataclass
class Timeline:
    contract: Contract | None
    events: list[PortEvent]
    stoppages: list[Stoppage]
    etas: dict[str, Fact[datetime]]  # latest ETA per port
    checks: list[Check]


def reconcile(obs: Observations, voyage: Voyage) -> Timeline:
    checks: list[Check] = []
    zones = {p.port: p.tz for p in voyage.itinerary}
    timeline = Timeline(
        contract=_contract(obs.contracts, checks),
        events=_events(obs.events, zones, checks),
        stoppages=_stoppages(obs.stoppages, zones, checks),
        etas=_etas(obs.events, zones),
        checks=checks,
    )
    log.info(
        "reconciled",
        extra={"stage": "reconcile", "contract": timeline.contract is not None, "events": len(timeline.events),
               "stoppages": len(timeline.stoppages), "checks": len(checks)},
    )
    return timeline


def _rank(o: EventObservation | StoppageObservation) -> tuple:
    return PRIORITY.get(o.doc_type, 0), o.sent_at, o.source


def _contract(observations: list[ContractObservation], checks: list[Check]) -> Contract | None:
    terms: dict[str, Fact] = {}
    counts: dict[DeductionCategory, Fact] = {}
    currency = None
    for obs in sorted(observations, key=lambda c: (c.sent_at, c.source)):
        for name, fact in obs.terms.items():
            terms[name] = _amend(terms.get(name), fact, name, checks)
        for category, fact in obs.counts_as_laytime.items():
            counts[category] = _amend(counts.get(category), fact, f"rule {category}", checks)
        currency = obs.currency or currency

    missing = [name for name in REQUIRED_TERMS if name not in terms] + ([] if currency else ["currency"])
    if missing:
        checks.append(Check(severity="high", message=f"Contract terms not found: {', '.join(missing)}; "
                            "laytime and demurrage not calculated"))
        return None
    return Contract.model_validate({
        **{name: fact.model_dump() for name, fact in terms.items()},
        "currency": currency,
        "counts_as_laytime": {category: fact.model_dump() for category, fact in counts.items()},
    })


def _amend(old: Fact | None, new: Fact, what: str, checks: list[Check]) -> Fact:
    """A later statement of a contract term replaces the earlier one."""
    if old is None:
        return new
    if old.value == new.value:
        return old.model_copy(update={"also_reported_in": [*old.also_reported_in, new.source]})
    checks.append(Check(
        severity="info",
        message=f"Contract {what} changed from {old.value} ({old.source}) to {new.value} ({new.source})",
        sources=[old.source, new.source],
    ))
    replaced = Superseded(value=str(old.value), source=old.source, quote=old.quote)
    return new.model_copy(update={"replaces": [*old.replaces, replaced]})


def _events(observations: list[EventObservation], zones: dict[str, timezone], checks: list[Check]) -> list[PortEvent]:
    groups: dict[tuple[str, EventType], list[EventObservation]] = defaultdict(list)
    for o in observations:
        if not o.is_estimate and o.type in SINGLE_EVENTS:
            groups[(o.port, o.type)].append(o)

    events = []
    for (port, type_), group in groups.items():
        best, *others = sorted(group, key=_rank, reverse=True)
        tz = zones[port]
        fact = Fact(value=best.time.astimezone(tz), source=best.source, quote=best.quote)
        for other in others:
            _compare(fact, other.time, other.source, other.quote, f"{port} {type_}", best.doc_type, tz, checks)
        events.append(PortEvent(port=port, type=type_, time=fact))
    return sorted(events, key=lambda e: e.time.value)


def _stoppages(
    observations: list[StoppageObservation], zones: dict[str, timezone], checks: list[Check]
) -> list[Stoppage]:
    """Per port, the most authoritative document that lists stoppages is the list used."""
    by_port: dict[str, list[StoppageObservation]] = defaultdict(list)
    for o in observations:
        by_port[o.port].append(o)

    stoppages = []
    for port, group in by_port.items():
        tz = zones[port]
        best = max(group, key=_rank)
        used = [
            Stoppage(
                port=port, category=o.category, reason=o.reason,
                start=Fact(value=o.start.astimezone(tz), source=o.source, quote=o.start_quote),
                end=Fact(value=o.end.astimezone(tz), source=o.source, quote=o.end_quote),
            )
            for o in group if o.source == best.source
        ]
        for o in (o for o in group if o.source != best.source):
            match = next(
                (s for s in used if s.category == o.category and o.start < s.end.value and s.start.value < o.end),
                None,
            )
            if match is None:
                checks.append(Check(
                    severity="medium",
                    message=f"{port}: stoppage '{o.reason}' in {o.source} is not in {best.source}; not used",
                    sources=[o.source, best.source],
                ))
                continue
            label = f"{port} stoppage '{match.reason}'"
            _compare(match.start, o.start, o.source, o.start_quote, f"{label} start", best.doc_type, tz, checks)
            _compare(match.end, o.end, o.source, o.end_quote, f"{label} end", best.doc_type, tz, checks)
        stoppages += used
    return sorted(stoppages, key=lambda s: s.start.value)


def _compare(
    fact: Fact, time: datetime, source: str, quote: str, label: str, best_type: DocType, tz: timezone,
    checks: list[Check],
) -> None:
    """Record another email's time for the same thing: agreeing, or replaced by the chosen one."""
    if abs(time - fact.value) <= SAME_TIME:
        if source != fact.source and source not in fact.also_reported_in:
            fact.also_reported_in.append(source)
        return
    other = time.astimezone(tz)
    fact.replaces.append(Superseded(value=other.isoformat(), source=source, quote=quote))
    checks.append(Check(
        severity="medium",
        message=f"{label}: {source} says {_short(other)}, {fact.source} says {_short(fact.value)}; "
        f"used {fact.source} ({best_type})",
        sources=[source, fact.source],
    ))


def _etas(observations: list[EventObservation], zones: dict[str, timezone]) -> dict[str, Fact[datetime]]:
    """The most recently sent ETA for each port."""
    latest: dict[str, Fact[datetime]] = {}
    for o in sorted((o for o in observations if o.type == EventType.ETA), key=lambda o: (o.sent_at, o.source)):
        latest[o.port] = Fact(value=o.time.astimezone(zones[o.port]), source=o.source, quote=o.quote)
    return latest


def _short(t: datetime) -> str:
    return t.strftime("%d %b %H:%M")
