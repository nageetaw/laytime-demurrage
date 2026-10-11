"""Write the two outputs: result.json for machines and brief.md for people.

The brief is a template filled from result.json by code, so it cannot contain a number that
is not in result.json.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from laytime.models import Check, Contract, Demurrage, EmailTriage, EventType, Fact, PortLaytime
from laytime.pipeline import VoyageResult
from laytime.settings import Settings

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}
# Arrival is the end of sea passage; when an email reports none, the first sign the vessel was there.
ARRIVAL_EVENTS = (EventType.EOSP, EventType.ANCHORED, EventType.ALL_FAST)
EVENT_LABELS = {
    EventType.EOSP: "arrived (end of sea passage)",
    EventType.ANCHORED: "anchored",
    EventType.NOR_TENDERED: "NOR tendered",
    EventType.ANCHOR_AWEIGH: "anchor aweigh",
    EventType.ALL_FAST: "all fast at berth",
    EventType.HOSES_CONNECTED: "hoses connected",
    EventType.COMMENCED_CARGO: "cargo operations started",
    EventType.COMPLETED_CARGO: "cargo operations completed",
    EventType.HOSES_DISCONNECTED: "hoses disconnected",
    EventType.SAILED: "sailed",
}


class Money(BaseModel):
    amount: float
    currency: str


class Summary(BaseModel):
    vessel: str
    voyage: str
    charterer: str
    status: Literal["final", "provisional", "incomplete"]
    as_of: AwareDatetime
    position: str
    laytime_allowed_hours: float | None
    laytime_used_hours: float | None
    hours_on_demurrage: float | None
    on_demurrage_from: AwareDatetime | None
    demurrage_owed: Money | None
    checks_to_review: int


class PortCallSummary(BaseModel):
    seq: int
    port: str
    function: str
    arrived: Fact[AwareDatetime] | None
    arrived_event: EventType | None  # eosp, or anchored / all_fast when no EOSP was reported
    sailed: Fact[AwareDatetime] | None
    eta: Fact[AwareDatetime] | None  # latest ETA, only while the vessel has not arrived


class Result(BaseModel):
    summary: Summary
    contract: Contract | None
    laytime: list[PortLaytime]
    demurrage: Demurrage | None
    port_calls: list[PortCallSummary]
    emails: list[EmailTriage]
    checks: list[Check]
    run: dict


def build(r: VoyageResult, settings: Settings, usage: dict) -> Result:
    voyage = r.vessel.current_voyage
    demurrage = r.laytime.demurrage if r.laytime else None
    checks = sorted(r.checks, key=lambda c: SEVERITY_ORDER[c.severity])
    return Result(
        summary=Summary(
            vessel=r.vessel.name,
            voyage=voyage.voyage_no,
            charterer=voyage.charterer,
            status=demurrage.status if demurrage else "incomplete",
            as_of=r.as_of,
            position=position(r),
            laytime_allowed_hours=demurrage.allowed_hours if demurrage else None,
            laytime_used_hours=demurrage.used_hours if demurrage else None,
            hours_on_demurrage=demurrage.hours_on_demurrage if demurrage else None,
            on_demurrage_from=demurrage.on_demurrage_from if demurrage else None,
            demurrage_owed=Money(amount=demurrage.amount, currency=demurrage.currency) if demurrage else None,
            checks_to_review=len(checks),
        ),
        contract=r.timeline.contract,
        laytime=r.laytime.ports if r.laytime else [],
        demurrage=demurrage,
        port_calls=_port_calls(r),
        emails=r.emails,
        checks=checks,
        run={
            "models": {"triage": settings.triage_model, "extract": settings.extract_model},
            "emails_processed": len(r.emails),
            **usage,
        },
    )


def write(result: Result, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(result.model_dump_json(indent=2) + "\n")
    (folder / "brief.md").write_text(render_brief(result))
    log.info("report written", extra={"stage": "report", "voyage": result.summary.voyage, "folder": str(folder)})
    return folder


def position(r: VoyageResult) -> str:
    """Where the vessel is, from its latest actual event and the latest ETA for its next port."""
    itinerary = r.vessel.current_voyage.itinerary
    events = r.timeline.events
    if not events:
        first = itinerary[0]
        eta = r.timeline.etas.get(first.port)
        return f"No port events yet; heading to {first.port}" + (f", ETA {_t(eta.value)}." if eta else ".")
    last = max(events, key=lambda e: e.time.value)
    call = next(p for p in itinerary if p.port == last.port)
    if last.type != EventType.SAILED:
        return f"At {last.port}: {EVENT_LABELS[last.type]} on {_t(last.time.value)}."
    later = [p for p in itinerary if p.seq > call.seq]
    if not any(p.is_cargo for p in later):
        return f"Sailed from {last.port} on {_t(last.time.value)}. The voyage is complete."
    eta = r.timeline.etas.get(later[0].port)
    return (f"Sailed from {last.port} on {_t(last.time.value)}, heading to {later[0].port}"
            + (f", ETA {_t(eta.value)}." if eta else "."))


def _port_calls(r: VoyageResult) -> list[PortCallSummary]:
    found = {(e.port, e.type): e.time for e in r.timeline.events}
    calls = []
    for p in r.vessel.current_voyage.itinerary:
        arrival_type = next((t for t in ARRIVAL_EVENTS if (p.port, t) in found), None)
        arrived = found[(p.port, arrival_type)] if arrival_type else None
        calls.append(PortCallSummary(
            seq=p.seq, port=p.port, function=p.function, arrived=arrived, arrived_event=arrival_type,
            sailed=found.get((p.port, EventType.SAILED)),
            eta=None if arrived else r.timeline.etas.get(p.port),
        ))
    return calls


def render_brief(result: Result) -> str:
    s = result.summary
    route = " → ".join(c.port for c in result.port_calls if c.function in ("load", "discharge"))
    lines = [
        f"# {s.vessel} · Voyage {s.voyage} · {s.status.upper()}",
        f"Charterer: {s.charterer} · {route} · as of {_t(s.as_of)} UTC",
        "",
        f"**Where is the vessel?** {s.position}",
        "",
    ]
    d = result.demurrage
    if d is None:
        lines += ["## Demurrage: not calculated", "The contract terms could not be found; see the checks below.", ""]
    else:
        on_demurrage = f" from {_t(d.on_demurrage_from)}" if d.on_demurrage_from else ""
        lines += [
            f"## Demurrage owed: {d.currency} {d.amount:,.2f}",
            f"Laytime used {d.used_hours:g} h of {d.allowed_hours:g} h allowed → {d.hours_on_demurrage:g} h on "
            f"demurrage{on_demurrage}, at {d.currency} {d.rate_per_day:,.0f}/day.",
            "",
            "| Port | NOR tendered | Laytime start | Laytime end | Deducted | Used |",
            "|---|---|---|---|---|---|",
        ]
        for p in result.laytime:
            deducted = "; ".join(f"{x.deducted_hours:g} h {x.reason}" for x in p.deductions if x.deducted_hours) or "none"
            end = _t(p.end.value) if p.end else f"running ({_t(p.counted_until)})"
            lines.append(f"| {p.port} ({p.function}) | {_t(p.nor_tendered.value)} | {_t(p.start)} | {end} "
                         f"| {deducted} | {p.used_hours:g} h |")
        lines.append("")

    lines += ["## Port calls", "| Port | Function | Arrived | Sailed |", "|---|---|---|---|"]
    for c in result.port_calls:
        arrived = _t(c.arrived.value) if c.arrived else (f"ETA {_t(c.eta.value)}" if c.eta else "-")
        lines.append(f"| {c.port} | {c.function} | {arrived} | {_t(c.sailed.value) if c.sailed else '-'} |")
    lines.append("")

    if result.checks:
        lines.append("## Please check")
        for i, c in enumerate(result.checks, 1):
            impact = f" (USD {c.impact_usd:,.2f} at stake)" if c.impact_usd else ""
            sources = f" [{', '.join(c.sources)}]" if c.sources else ""
            lines.append(f"{i}. **{c.severity}**: {c.message}{impact}{sources}")
        lines.append("")

    lines.append("Times are local port time unless marked UTC. Every number above has its source email in result.json.")
    return "\n".join(lines) + "\n"


def _t(t: datetime) -> str:
    return t.strftime("%d %b %H:%M")
