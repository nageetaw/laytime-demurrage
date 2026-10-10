"""Ask the LLM for the facts in each relevant email, each with the exact text it came from."""

import logging
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, NaiveDatetime

from laytime.fleet import Vessel
from laytime.llm import LLMClient, load_prompt
from laytime.models import DeductionCategory, DocType, Email, EmailTriage, EventType, Relevance

log = logging.getLogger(__name__)

T = TypeVar("T")

CONTRACT_DOCS = {DocType.RECAP, DocType.ADDENDUM}
TIME_ZONE = Field(pattern=r"^(UTC|local|[+-]\d{2}:\d{2})$")


class Quoted(BaseModel, Generic[T]):
    value: T
    quote: str


class Duration(BaseModel):
    value: float
    unit: Literal["hours", "days"]
    quote: str


class DeductionRuleAnswer(BaseModel):
    category: DeductionCategory
    counts_as_laytime: float
    quote: str


class ContractAnswer(BaseModel):
    laytime_allowed: Duration | None
    reversible: Quoted[bool] | None
    basis: Quoted[str] | None
    turn_time: Duration | None
    demurrage_rate_per_day: Quoted[float] | None
    currency: str | None
    pro_rata: Quoted[bool] | None
    laytime_ends_on: Quoted[EventType] | None
    exceptions_apply_on_demurrage: Quoted[bool] | None
    deduction_rules: list[DeductionRuleAnswer]


class EventAnswer(BaseModel):
    port: str
    type: EventType
    local_time: NaiveDatetime
    time_zone: str = TIME_ZONE
    is_estimate: bool
    quote: str


class StoppageAnswer(BaseModel):
    port: str
    category: DeductionCategory
    reason: str
    start: NaiveDatetime
    end: NaiveDatetime
    time_zone: str = TIME_ZONE
    start_quote: str
    end_quote: str


class EventsAnswer(BaseModel):
    events: list[EventAnswer]
    stoppages: list[StoppageAnswer]


class Extraction(BaseModel):
    email: Email
    doc_type: DocType
    contract: ContractAnswer | None = None
    events: EventsAnswer | None = None


def extract(
    emails: list[Email], rows: list[EmailTriage], vessel: Vessel, llm: LLMClient, model: str
) -> list[Extraction]:
    """Contract terms from recaps and addenda; events and stoppages from every other relevant email."""
    by_file = {r.file: r for r in rows}
    results = []
    for email in emails:
        row = by_file[email.file]
        if row.relevance == Relevance.NONE or row.doc_type is None:
            continue
        if row.doc_type in CONTRACT_DOCS:
            answer = llm.ask(
                model=model, system=load_prompt("contract"), user=_message(email), schema=ContractAnswer,
                label=f"contract {email.file}",
            )
            results.append(Extraction(email=email, doc_type=row.doc_type, contract=answer))
            log.info("contract extracted", extra={"stage": "extract", "email": email.file,
                                                  "rules": len(answer.deduction_rules)})
        else:
            answer = llm.ask(
                model=model, system=load_prompt("events"), user=_message(email, vessel), schema=EventsAnswer,
                label=f"events {email.file}",
            )
            results.append(Extraction(email=email, doc_type=row.doc_type, events=answer))
            log.info("events extracted", extra={"stage": "extract", "email": email.file,
                                                "events": len(answer.events), "stoppages": len(answer.stoppages)})
    return results


def _message(email: Email, vessel: Vessel | None = None) -> str:
    header = ""
    if vessel is not None and vessel.current_voyage is not None:
        ports = ", ".join(f"{p.port} ({p.function})" for p in vessel.current_voyage.itinerary)
        header = f"Vessel: {vessel.name}\nItinerary: {ports}\n\n"
    return (
        f"{header}"
        f"Email sent: {email.sent_at.isoformat()}\n"
        f"Subject: {email.subject}\n\n"
        f"Text written by the sender:\n<<<\n{email.authored_text}\n>>>"
    )
