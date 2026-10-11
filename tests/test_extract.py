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


def test_times_keep_the_written_clock_time_whatever_offset_the_model_adds():
    from laytime.extract import EventAnswer

    # JSON's "date-time" format requires an offset, so the schema must not advertise it.
    assert "format" not in EventAnswer.model_json_schema()["properties"]["local_time"]
    for written in ("2026-01-30T04:30", "2026-01-30T04:30:00Z", "2026-01-30T04:30:00+01:00"):
        answer = EventAnswer.model_validate({"port": "Antwerp", "type": "eosp", "local_time": written,
                                             "utc_offset_hours": None, "is_estimate": False, "quote": "q"})
        assert answer.local_time == datetime(2026, 1, 30, 4, 30)


def test_an_email_the_llm_cannot_read_becomes_a_check_and_the_run_continues(fleet):
    from laytime.llm import LLMError
    from laytime.normalise import normalise

    class FailingLLM(FakeLLM):
        def ask(self, *, label, **kwargs):
            if label == "events bad.eml":
                raise LLMError("events bad.eml: no valid answer from m after 2 attempts")
            return super().ask(label=label, **kwargs)

    sent = datetime(2026, 2, 1, tzinfo=timezone.utc)
    emails = [Email(file=f, message_id=f, sent_at=sent, sender="x", subject="s", authored_text="t")
              for f in ("bad.eml", "good.eml")]
    rows = [EmailTriage(file=e.file, subject="s", sent_at=sent, vessels=["KESTREL BAY"], doc_type=DocType.SOF_FINAL,
                        relevance=Relevance.LAYTIME, reason="r") for e in emails]
    results = extract(emails, rows, fleet.vessels[0], FailingLLM(), model="m")

    assert [(r.email.file, r.error is None) for r in results] == [("bad.eml", False), ("good.eml", True)]
    [check] = normalise(results, fleet.vessels[0].current_voyage).checks
    assert (check.severity, check.sources) == ("high", ["bad.eml"])
