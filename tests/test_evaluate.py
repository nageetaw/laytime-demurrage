import copy
from datetime import datetime, timezone

import pytest

from laytime.evaluate import audit_citations, fingerprint, score
from laytime.models import Email

GOLD = {
    "voyage": "V-1",
    "emails": {"a.eml": {"vessels": ["KESTREL BAY"], "relevance": "laytime", "doc_type": "sof_final"},
               "b.eml": {"vessels": [], "relevance": "no", "doc_type": None}},
    "contract": {"turn_time_hours": 6, "reversible": True, "counts_as_laytime": {"weather": 0.5}},
    "port_calls": {"Cotonou": {"arrived": "2026-02-15T16:20+01:00", "sailed": "2026-02-20T07:00+01:00"}},
    "laytime": {"Cotonou": {"nor_tendered": "2026-02-15T17:30+01:00", "start": "2026-02-15T23:30+01:00",
                            "end": "2026-02-20T03:30+01:00", "deducted_hours": {"weather": 2.0}, "used_hours": 95.5}},
    "demurrage": {"status": "final", "amount": 65343.75},
    "required_checks": [{"about": "rain end corrected", "sources": ["a.eml", "c.eml"]}],
}


def fact(value, source="a.eml", quote="NOR tendered 17:30"):
    return {"value": value, "source": source, "quote": quote, "also_reported_in": [], "replaces": []}


@pytest.fixture
def result():
    return {
        "summary": {"status": "final"},
        "emails": [{"file": "a.eml", "vessels": ["KESTREL BAY"], "relevance": "laytime", "doc_type": "sof_final"},
                   {"file": "b.eml", "vessels": [], "relevance": "no", "doc_type": None}],
        "contract": {"turn_time_hours": fact(6.0), "reversible": fact(True),
                     "counts_as_laytime": {"weather": fact(0.5)}},
        # Same moments in UTC: times are compared as instants, not as text.
        "port_calls": [{"port": "Cotonou", "arrived": fact("2026-02-15T15:20:00Z"),
                        "sailed": fact("2026-02-20T06:00:00Z")}],
        "laytime": [{"port": "Cotonou", "nor_tendered": fact("2026-02-15T17:30:00+01:00"),
                     "start": "2026-02-15T23:30:00+01:00", "end": fact("2026-02-20T03:30:00+01:00"),
                     "used_hours": 95.5,
                     "deductions": [{"category": "weather", "deducted_hours": 2.0},
                                    {"category": "shore_terminal", "deducted_hours": 0.0}]}],
        "demurrage": {"status": "final", "amount": 65343.75},
        "checks": [{"sources": ["a.eml", "c.eml"], "message": "rain end changed"}],
    }


def failures(result):
    return [line.item for line in score(result, GOLD) if not line.ok]


def test_a_correct_result_passes(result):
    assert failures(result) == []


def test_wrong_numbers_and_labels_are_caught(result):
    result["demurrage"]["amount"] = 65609.38
    result["laytime"][0]["deductions"][0]["deducted_hours"] = 1.75
    result["emails"][0]["relevance"] = "schedule"
    result["contract"]["reversible"]["value"] = False
    assert failures(result) == ["a.eml relevance", "reversible", "Cotonou deducted hours", "amount"]


def test_a_missing_required_check_is_caught(result):
    result["checks"] = []
    assert failures(result) == ["rain end corrected"]


def test_a_missing_contract_counts_as_failures_not_a_crash(result):
    result["contract"] = None
    result["demurrage"] = None
    assert len(failures(result)) == 5


def test_citation_audit_catches_an_invented_quote(result):
    email = Email(file="a.eml", message_id="a", sent_at=datetime(2026, 2, 20, tzinfo=timezone.utc), sender="x",
                  subject="SOF", authored_text="15/02/2026 17:30   NOR tendered\nNOR tendered 17:30")
    result["laytime"][0]["end"]["quote"] = "Hoses disconnected 03:30"
    lines = audit_citations(result, {"a.eml": email})
    assert [line.ok for line in lines].count(False) == 1
    assert "Hoses disconnected" in next(line.item for line in lines if not line.ok)


def test_fingerprint_ignores_file_names_but_not_numbers(result):
    renamed = copy.deepcopy(result)
    renamed["laytime"][0]["nor_tendered"]["source"] = "msg_0007.eml"
    renamed["checks"][0]["sources"] = ["msg_0001.eml"]
    assert fingerprint(renamed) == fingerprint(result)
    renamed["laytime"][0]["used_hours"] = 95.0
    assert fingerprint(renamed) != fingerprint(result)
