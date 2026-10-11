"""The whole pipeline, once per vessel on a voyage: read, filter, triage, extract, reconcile, calculate."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from laytime.calculator import calculate
from laytime.extract import extract
from laytime.fleet import Vessel, load_fleet
from laytime.gate import gate
from laytime.ingest import read_inbox
from laytime.llm import LLMClient
from laytime.models import Check, EmailTriage, LaytimeResult
from laytime.normalise import normalise
from laytime.reconcile import Timeline, reconcile
from laytime.settings import Settings
from laytime.triage import triage

log = logging.getLogger(__name__)


@dataclass
class VoyageResult:
    vessel: Vessel
    emails: list[EmailTriage]
    timeline: Timeline
    laytime: LaytimeResult | None  # None when the contract terms could not be found
    checks: list[Check]
    # as_of is the time the newest email in the inbox was sent
    as_of: datetime


def run(emails_dir: Path, fleet_path: Path, settings: Settings, llm: LLMClient) -> list[VoyageResult]:
    fleet = load_fleet(fleet_path)
    emails = read_inbox(emails_dir)
    decisions = gate(emails, fleet)
    as_of = max((e.sent_at for e in emails), default=datetime.now(timezone.utc))

    results = []
    for vessel in fleet.on_voyage():
        voyage = vessel.current_voyage
        log.info("voyage started", extra={"stage": "pipeline", "voyage": voyage.voyage_no})
        rows = triage(emails, decisions, vessel, llm, settings.triage_model)
        observations = normalise(extract(emails, rows, vessel, llm, settings.extract_model), voyage)
        timeline = reconcile(observations, voyage)
        laytime = None
        if timeline.contract is not None:
            laytime = calculate(timeline.contract, voyage.itinerary, timeline.events, timeline.stoppages, as_of)
        checks = observations.checks + timeline.checks + (laytime.checks if laytime else [])
        results.append(VoyageResult(vessel=vessel, emails=rows, timeline=timeline, laytime=laytime,
                                    checks=checks, as_of=as_of))
        log.info("voyage done", extra={"stage": "pipeline", "voyage": voyage.voyage_no,
                                       "status": laytime.demurrage.status if laytime else "incomplete"})
    return results
