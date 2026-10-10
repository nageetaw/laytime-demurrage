from datetime import datetime, timezone

from laytime.extract import ContractAnswer, EventsAnswer, extract
from laytime.models import DocType, Email, EmailTriage, Relevance


class FakeLLM:
    def __init__(self):
        self.labels = []

    def ask(self, *, schema, label, **kwargs):
        self.labels.append(label)
        if schema is ContractAnswer:
            return ContractAnswer(laytime_allowed=None, reversible=None, basis=None, turn_time=None,
                                  demurrage_rate_per_day=None, currency=None, pro_rata=None,
                                  laytime_ends_on=None, exceptions_apply_on_demurrage=None, deduction_rules=[])
        return EventsAnswer(events=[], stoppages=[])


def test_each_email_goes_to_the_right_prompt_and_irrelevant_mail_is_skipped(fleet):
    sent = datetime(2026, 2, 1, tzinfo=timezone.utc)
    cases = [
        ("recap.eml", DocType.RECAP, Relevance.LAYTIME),
        ("addendum.eml", DocType.ADDENDUM, Relevance.LAYTIME),
        ("sof.eml", DocType.SOF_FINAL, Relevance.LAYTIME),
        ("noon.eml", DocType.MASTER_REPORT, Relevance.SCHEDULE),
        ("invoice.eml", DocType.DISBURSEMENT_ACCOUNT, Relevance.NONE),
    ]
    emails = [Email(file=f, message_id=f, sent_at=sent, sender="x", subject="s", authored_text="t")
              for f, _, _ in cases]
    rows = [EmailTriage(file=f, subject="s", sent_at=sent, vessels=["KESTREL BAY"], doc_type=d,
                        relevance=r, reason="r") for f, d, r in cases]
    llm = FakeLLM()
    results = extract(emails, rows, fleet.vessels[0], llm, model="m")

    assert llm.labels == ["contract recap.eml", "contract addendum.eml", "events sof.eml", "events noon.eml"]
    assert [r.email.file for r in results] == ["recap.eml", "addendum.eml", "sof.eml", "noon.eml"]
