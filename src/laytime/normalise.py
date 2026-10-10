"""Turn the LLM's answers into checked observations.

Code, not the LLM, resolves time zones, converts units, and verifies every quote against the
email it claims to come from.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from laytime.extract import ContractAnswer, Duration, EventsAnswer, Extraction
from laytime.fleet import PortCall, Voyage, parse_utc_offset
from laytime.models import (
    Check,
    ContractObservation,
    Email,
    EventObservation,
    Fact,
    StoppageObservation,
)

log = logging.getLogger(__name__)

HOURS_PER = {"hours": 1, "days": 24}


@dataclass
class Observations:
    contracts: list[ContractObservation] = field(default_factory=list)
    events: list[EventObservation] = field(default_factory=list)
    stoppages: list[StoppageObservation] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)


def normalise(extractions: list[Extraction], voyage: Voyage) -> Observations:
    out = Observations()
    for x in extractions:
        if x.contract is not None:
            out.contracts.append(_contract(x, x.contract, out.checks))
        if x.events is not None:
            _events(x, x.events, voyage, out)
    log.info(
        "normalised",
        extra={"stage": "normalise", "events": len(out.events), "stoppages": len(out.stoppages),
               "checks": len(out.checks)},
    )
    return out


def resolve_time(local: datetime, time_zone: str, port: PortCall) -> tuple[datetime, str | None]:
    """Attach a time zone: UTC, the port's own offset, or the offset the email states.

    Returns a warning when a stated offset differs from the port's offset in fleet.json.
    """
    if time_zone == "UTC":
        return local.replace(tzinfo=timezone.utc), None
    if time_zone == "local":
        return local.replace(tzinfo=port.tz), None
    stated = parse_utc_offset(time_zone)
    warning = None
    if stated != port.tz:
        warning = f"{port.port}: email states UTC{time_zone} but the port is UTC{port.utc_offset}"
    return local.replace(tzinfo=stated), warning


def quote_found(quote: str, email: Email) -> bool:
    """True if the quote appears in the sender's text or subject, ignoring case and spacing."""

    def flat(text: str) -> str:
        return " ".join(text.split()).casefold()

    return bool(quote.strip()) and flat(quote) in flat(f"{email.subject}\n{email.authored_text}")


def _contract(x: Extraction, answer: ContractAnswer, checks: list[Check]) -> ContractObservation:
    obs = ContractObservation(
        source=x.email.file, doc_type=x.doc_type, sent_at=x.email.sent_at, currency=answer.currency
    )

    def cite(name: str, value, quote: str) -> None:
        _check_quote(quote, x.email, name, checks)
        obs.terms[name] = Fact(value=value, source=x.email.file, quote=quote)

    def hours(d: Duration) -> float:
        return d.value * HOURS_PER[d.unit]

    if answer.laytime_allowed:
        cite("laytime_allowed_hours", hours(answer.laytime_allowed), answer.laytime_allowed.quote)
    if answer.turn_time:
        cite("turn_time_hours", hours(answer.turn_time), answer.turn_time.quote)
    for name in ("reversible", "basis", "demurrage_rate_per_day", "pro_rata", "laytime_ends_on",
                 "exceptions_apply_on_demurrage"):
        quoted = getattr(answer, name)
        if quoted is not None:
            cite(name, quoted.value, quoted.quote)
    for rule in answer.deduction_rules:
        _check_quote(rule.quote, x.email, f"rule {rule.category}", checks)
        obs.counts_as_laytime[rule.category] = Fact(value=rule.counts_as_laytime, source=x.email.file,
                                                    quote=rule.quote)
    return obs


def _events(x: Extraction, answer: EventsAnswer, voyage: Voyage, out: Observations) -> None:
    email = x.email
    for e in answer.events:
        port = _port(e.port, voyage)
        if port is None:
            log.info("event outside the itinerary ignored",
                     extra={"stage": "normalise", "email": email.file, "event": e.type})
            continue
        time, warning = resolve_time(e.local_time, e.time_zone, port)
        if warning:
            out.checks.append(Check(severity="medium", message=warning, sources=[email.file]))
        if not e.is_estimate and time > email.sent_at:
            out.checks.append(Check(
                severity="medium",
                message=f"{port.port}: {e.type} at {time.isoformat()} is after the email was sent; ignored",
                sources=[email.file],
            ))
            continue
        _check_quote(e.quote, email, f"{port.port} {e.type}", out.checks)
        out.events.append(EventObservation(
            port=port.port, type=e.type, time=time, is_estimate=e.is_estimate, source=email.file,
            quote=e.quote, doc_type=x.doc_type, sent_at=email.sent_at,
        ))

    for s in answer.stoppages:
        port = _port(s.port, voyage)
        if port is None:
            continue
        start, warning = resolve_time(s.start, s.time_zone, port)
        end, _ = resolve_time(s.end, s.time_zone, port)
        if warning:
            out.checks.append(Check(severity="medium", message=warning, sources=[email.file]))
        if not start < end <= email.sent_at:
            out.checks.append(Check(
                severity="medium",
                message=f"{port.port}: stoppage '{s.reason}' has an impossible period "
                f"{start.isoformat()} to {end.isoformat()}; ignored",
                sources=[email.file],
            ))
            continue
        for quote in (s.start_quote, s.end_quote):
            _check_quote(quote, email, f"{port.port} stoppage '{s.reason}'", out.checks)
        out.stoppages.append(StoppageObservation(
            port=port.port, category=s.category, reason=s.reason, start=start, end=end,
            start_quote=s.start_quote, end_quote=s.end_quote, source=email.file, doc_type=x.doc_type,
            sent_at=email.sent_at,
        ))


def _port(name: str, voyage: Voyage) -> PortCall | None:
    return next((p for p in voyage.itinerary if p.port.casefold() == name.strip().casefold()), None)


def _check_quote(quote: str, email: Email, what: str, checks: list[Check]) -> None:
    if not quote_found(quote, email):
        checks.append(Check(
            severity="medium",
            message=f"{what}: the quoted text was not found in the email; verify by hand",
            sources=[email.file],
        ))
