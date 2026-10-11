"""Ask the LLM for the facts in each relevant email, each with the exact text it came from."""

import logging
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, Field, WithJsonSchema

from laytime.fleet import Vessel
from laytime.llm import LLMClient, LLMError, load_prompt
from laytime.models import DeductionCategory, DocType, Email, EmailTriage, EventType, Relevance

log = logging.getLogger(__name__)

T = TypeVar("T")

CONTRACT_DOCS = {DocType.RECAP, DocType.ADDENDUM}


def _wall_clock(value):
    """Keep the date and time as written; drop any offset the model added (time zones are code's job)."""
    if isinstance(value, str):
        return datetime.fromisoformat(value).replace(tzinfo=None)
    return value


# A plain string in the schema: JSON's "date-time" format would require an offset.
WallClock = Annotated[
    datetime,
    BeforeValidator(_wall_clock),
    WithJsonSchema({"type": "string", "description": "YYYY-MM-DDTHH:MM as written in the email"}),
]
UtcOffsetHours = Annotated[
    float | None,
    Field(description="0 for UTC/GMT/Z; the offset for a named zone or offset (CET, WAT, UTC+1 -> 1); "
                      "null for local time or no label"),
]


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
    local_time: WallClock
    utc_offset_hours: UtcOffsetHours
    is_estimate: bool
    quote: str


class StoppageAnswer(BaseModel):
    port: str
    category: DeductionCategory
    reason: str
    start: WallClock
    end: WallClock
    utc_offset_hours: UtcOffsetHours
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
    error: str | None = None  # set when no valid answer came back


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
        is_contract = row.doc_type in CONTRACT_DOCS
        kind, schema = ("contract", ContractAnswer) if is_contract else ("events", EventsAnswer)
        try:
            answer = llm.ask(
                model=model, system=load_prompt(kind), user=_message(email, None if is_contract else vessel),
                schema=schema, label=f"{kind} {email.file}",
            )
        except LLMError as error:
            # One unreadable email should not stop the run; it becomes a check for a human.
            log.error("extraction failed", extra={"stage": "extract", "email": email.file, "kind": kind})
            results.append(Extraction(email=email, doc_type=row.doc_type, error=str(error)))
            continue
        results.append(Extraction(email=email, doc_type=row.doc_type, **{kind: answer}))
        log.info("extracted", extra={"stage": "extract", "email": email.file, "kind": kind})
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
