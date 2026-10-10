"""Data models shared by the pipeline stages.

The enums are the fixed vocabularies the LLM maps free text onto, so code never has to
match the wording of an email.
"""

from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, Field

T = TypeVar("T")


class DocType(StrEnum):
    RECAP = "recap"
    ADDENDUM = "addendum"
    VOYAGE_INSTRUCTIONS = "voyage_instructions"
    ARRIVAL_NOTICE = "arrival_notice"
    SOF_INTERIM = "sof_interim"
    SOF_FINAL = "sof_final"
    MASTER_REPORT = "master_report"
    PORT_CALL_REPORT = "port_call_report"
    LINEUP = "lineup"
    DISBURSEMENT_ACCOUNT = "disbursement_account"
    OTHER = "other"


class EventType(StrEnum):
    ETA = "eta"
    EOSP = "eosp"
    ANCHORED = "anchored"
    NOR_TENDERED = "nor_tendered"
    ANCHOR_AWEIGH = "anchor_aweigh"
    PILOT_ON_BOARD = "pilot_on_board"
    ALL_FAST = "all_fast"
    HOSES_CONNECTED = "hoses_connected"
    COMMENCED_CARGO = "commenced_cargo"
    COMPLETED_CARGO = "completed_cargo"
    HOSES_DISCONNECTED = "hoses_disconnected"
    SAILED = "sailed"


class DeductionCategory(StrEnum):
    SHIFTING = "shifting_anchorage_to_berth"
    VESSEL_EQUIPMENT = "vessel_equipment"
    WEATHER = "weather"
    SHORE_TERMINAL = "shore_terminal"
    WAITING_FOR_BERTH = "waiting_for_berth"
    OTHER = "other"


class Relevance(StrEnum):
    LAYTIME = "laytime"
    SCHEDULE = "schedule"
    NONE = "no"


class Email(BaseModel):
    """One parsed `.eml` file. Private fields are excluded from repr so they never end up in logs."""

    file: str
    message_id: str
    in_reply_to: str | None = None
    sent_at: AwareDatetime  # from the Date: header, in UTC; never an event time
    sender: str = Field(repr=False)
    subject: str
    authored_text: str = Field(repr=False)  # what the sender wrote; facts come only from here
    quoted_text: str = Field(default="", repr=False)  # earlier mail quoted or forwarded below it


class Superseded(BaseModel):
    """A value that a higher-priority or later source replaced."""

    value: str
    source: str
    quote: str


class Fact(BaseModel, Generic[T]):
    """A value with its citation: the email it came from and the exact text that says it."""

    value: T
    source: str
    quote: str
    also_reported_in: list[str] = []
    replaces: list[Superseded] = []


class Contract(BaseModel):
    """Laytime and demurrage terms after applying any addenda to the recap."""

    laytime_allowed_hours: Fact[float]
    reversible: Fact[bool]
    basis: Fact[str]  # e.g. "SHINC"
    turn_time_hours: Fact[float]
    demurrage_rate_per_day: Fact[float]
    currency: str
    pro_rata: Fact[bool]
    laytime_ends_on: Fact[EventType] | None = None
    exceptions_apply_on_demurrage: Fact[bool] | None = None
    # Share of a stoppage that still counts as laytime: 0 = not at all, 0.5 = half, 1 = in full.
    counts_as_laytime: dict[DeductionCategory, Fact[float]] = {}


class PortEvent(BaseModel):
    """One actual event at one port, after reconciling every email that reports it."""

    port: str
    type: EventType
    time: Fact[AwareDatetime]


class Stoppage(BaseModel):
    """A period inside a port call when operations stopped, and why."""

    port: str
    category: DeductionCategory
    reason: str
    start: Fact[AwareDatetime]
    end: Fact[AwareDatetime]


class Check(BaseModel):
    """Something a human should look at."""

    severity: Literal["high", "medium", "info"]
    message: str
    sources: list[str] = []
    impact_usd: float | None = None


class Deduction(BaseModel):
    """A stoppage or shift inside the laytime window and how much of it was taken off."""

    reason: str
    category: DeductionCategory
    start: Fact[AwareDatetime]
    end: Fact[AwareDatetime]
    hours: float  # the part inside the laytime window
    counts_as_laytime: float
    deducted_hours: float


class PortLaytime(BaseModel):
    port: str
    function: str
    nor_tendered: Fact[AwareDatetime]
    turn_time_hours: float
    start: AwareDatetime
    end: Fact[AwareDatetime] | None  # None while cargo operations are still running
    counted_until: AwareDatetime
    gross_hours: float
    deductions: list[Deduction]
    used_hours: float


class Demurrage(BaseModel):
    status: Literal["final", "provisional"]
    allowed_hours: float
    used_hours: float
    hours_on_demurrage: float
    on_demurrage_from: AwareDatetime | None
    rate_per_day: float
    currency: str
    amount: float
    formula: str


class LaytimeResult(BaseModel):
    ports: list[PortLaytime]
    demurrage: Demurrage
    checks: list[Check]
