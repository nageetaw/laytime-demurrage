"""Set aside mail that is not about a vessel on a voyage, before any LLM call."""

import logging

from pydantic import BaseModel

from laytime.fleet import Fleet, Vessel
from laytime.models import Email

log = logging.getLogger(__name__)


class GateDecision(BaseModel):
    email: str
    passed: bool
    vessels: list[str]
    reason: str


def gate(emails: list[Email], fleet: Fleet) -> list[GateDecision]:
    by_id = {e.message_id: e for e in emails}
    decisions = [_decide(email, _vessels_about(email, by_id, fleet)) for email in emails]
    for d in decisions:
        log.info(
            "passed" if d.passed else f"set aside: {d.reason}",
            extra={"stage": "gate", "email": d.email, "vessels": d.vessels},
        )
    log.info(
        "gate done",
        extra={"stage": "gate", "passed": sum(d.passed for d in decisions), "total": len(decisions)},
    )
    return decisions


def _vessels_about(email: Email, by_id: dict[str, Email], fleet: Fleet) -> list[Vessel]:
    """Fleet vessels named in the subject or the sender's own text.

    A reply that names none inherits them from the email it answers.
    """
    seen: set[str] = set()
    current: Email | None = email
    while current is not None and current.message_id not in seen:
        seen.add(current.message_id)
        vessels = fleet.mentioned_in(f"{current.subject}\n{current.authored_text}")
        if vessels:
            return vessels
        current = by_id.get(current.in_reply_to or "")
    return []


def _decide(email: Email, vessels: list[Vessel]) -> GateDecision:
    if not vessels:
        return GateDecision(email=email.file, passed=False, vessels=[], reason="no fleet vessel mentioned")
    on_voyage = [v.name for v in vessels if v.current_voyage is not None]
    if not on_voyage:
        return GateDecision(
            email=email.file,
            passed=False,
            vessels=[v.name for v in vessels],
            reason="fleet vessel not on a voyage",
        )
    return GateDecision(email=email.file, passed=True, vessels=on_voyage, reason="about a vessel on a voyage")
