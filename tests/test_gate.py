from datetime import datetime, timezone

from laytime.gate import gate
from laytime.models import Email


def make_email(file, text, *, subject="", message_id=None, in_reply_to=None):
    return Email(
        file=file,
        message_id=message_id or f"<{file}>",
        in_reply_to=in_reply_to,
        sent_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
        sender="someone",
        subject=subject,
        authored_text=text,
    )


def decide(fleet, *emails):
    return {d.email: d for d in gate(list(emails), fleet)}


def test_vessel_on_voyage_passes(fleet):
    d = decide(fleet, make_email("a.eml", "EOSP 15/1520", subject="KESTREL BAY - ARRIVAL COTONOU"))["a.eml"]
    assert d.passed and d.vessels == ["KESTREL BAY"]


def test_other_ship_is_set_aside(fleet):
    d = decide(fleet, make_email("a.eml", "Vessel: MT ALDER STAR\nNOR tendered 14/02 13:15 LT",
                                 subject="NOR TENDERED - COTONOU"))["a.eml"]
    assert not d.passed and d.reason == "no fleet vessel mentioned"


def test_fleet_vessel_not_on_a_voyage_is_set_aside(fleet):
    d = decide(fleet, make_email("a.eml", "Manifold valves renewed", subject="KESTREL POINT - drydock report"))["a.eml"]
    assert not d.passed and d.vessels == ["KESTREL POINT"]
    assert d.reason == "fleet vessel not on a voyage"


def test_reply_without_vessel_name_inherits_it_from_the_thread(fleet):
    original = make_email("a.eml", "NOR TENDERED 15/1630", subject="KESTREL BAY - ARRIVAL", message_id="<arr>")
    reply = make_email("b.eml", "We confirm NOR received.", subject="RE: arrival", in_reply_to="<arr>")
    assert decide(fleet, original, reply)["b.eml"].vessels == ["KESTREL BAY"]


def test_vessel_named_only_in_quoted_text_does_not_count(fleet):
    email = make_email("a.eml", "Please update the ETA.", subject="FW: noon report")
    email.quoted_text = "> KESTREL BAY NOON REPORT"
    assert not decide(fleet, email)["a.eml"].passed
