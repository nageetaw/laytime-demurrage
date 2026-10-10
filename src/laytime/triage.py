"""Ask the LLM what each email is and whether it matters for the voyage."""

import logging

from pydantic import BaseModel

from laytime.fleet import Vessel
from laytime.gate import GateDecision
from laytime.llm import LLMClient, load_prompt
from laytime.models import DocType, Email, EmailTriage, Relevance

log = logging.getLogger(__name__)


class TriageAnswer(BaseModel):
    doc_type: DocType
    relevance: Relevance
    reason: str


def triage(
    emails: list[Email], decisions: list[GateDecision], vessel: Vessel, llm: LLMClient, model: str
) -> list[EmailTriage]:
    """One row per email. Only emails the gate passed for this vessel go to the LLM."""
    by_file = {d.email: d for d in decisions}
    system = load_prompt("triage")
    rows = []
    for email in emails:
        decision = by_file[email.file]
        row = EmailTriage(
            file=email.file,
            subject=email.subject,
            sent_at=email.sent_at,
            vessels=decision.vessels,
            doc_type=None,
            relevance=Relevance.NONE,
            reason=decision.reason if not decision.passed else "about another voyage",
        )
        if vessel.name in decision.vessels:
            answer = llm.ask(
                model=model, system=system, user=_user_message(email, vessel), schema=TriageAnswer,
                label=f"triage {email.file}",
            )
            row = row.model_copy(update=answer.model_dump())
            log.info(
                "triaged",
                extra={"stage": "triage", "email": email.file, "doc_type": row.doc_type, "relevance": row.relevance},
            )
        rows.append(row)
    return rows


def _user_message(email: Email, vessel: Vessel) -> str:
    voyage = vessel.current_voyage
    itinerary = ", ".join(f"{p.port} ({p.function})" for p in voyage.itinerary) if voyage else "none"
    return (
        f"Vessel: {vessel.name} (IMO {vessel.imo})\n"
        f"Voyage: {voyage.voyage_no if voyage else 'none'}; itinerary: {itinerary}\n\n"
        f"Email sent: {email.sent_at.isoformat()}\n"
        f"From: {email.sender}\n"
        f"Subject: {email.subject}\n"
        f"Quoted earlier mail below the sender's text: {'yes (removed)' if email.quoted_text else 'no'}\n\n"
        f"Text written by the sender:\n<<<\n{email.authored_text}\n>>>"
    )
