from datetime import datetime, timezone

from laytime.gate import gate
from laytime.models import DocType, Email, Relevance
from laytime.triage import TriageAnswer, triage


class FakeLLM:
    def __init__(self):
        self.labels = []

    def ask(self, *, label, **kwargs):
        self.labels.append(label)
        return TriageAnswer(doc_type=DocType.MASTER_REPORT, relevance=Relevance.LAYTIME, reason="arrival times")


def make_email(file, subject):
    return Email(file=file, message_id=f"<{file}>", sent_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
                 sender="someone", subject=subject, authored_text="EOSP 15/1520")


def test_only_emails_passed_by_the_gate_reach_the_llm(fleet):
    emails = [
        make_email("a.eml", "KESTREL BAY - ARRIVAL COTONOU"),
        make_email("b.eml", "KESTREL POINT - drydock report"),
        make_email("c.eml", "ALDER STAR - NOR tendered"),
    ]
    llm = FakeLLM()
    rows = triage(emails, gate(emails, fleet), fleet.vessels[0], llm, model="m")

    assert llm.labels == ["triage a.eml"]
    assert [(r.file, r.relevance, r.doc_type) for r in rows] == [
        ("a.eml", Relevance.LAYTIME, DocType.MASTER_REPORT),
        ("b.eml", Relevance.NONE, None),
        ("c.eml", Relevance.NONE, None),
    ]
    assert rows[1].reason == "fleet vessel not on a voyage"
