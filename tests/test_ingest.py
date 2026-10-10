from datetime import datetime, timezone

import pytest

from laytime.ingest import read_email, read_inbox, split_quoted


def write_eml(folder, name, *, date, subject="Test", body="Hello", message_id=None, in_reply_to=None):
    headers = [
        "From: Agent <agent@example.test>",
        "To: Ops <ops@example.test>",
        f"Date: {date}",
        f"Subject: {subject}",
        f"Message-ID: {message_id or f'<{name}@example.test>'}",
    ]
    if in_reply_to:
        headers.append(f"In-Reply-To: {in_reply_to}")
    headers += ["MIME-Version: 1.0", 'Content-Type: text/plain; charset="utf-8"']
    path = folder / name
    path.write_text("\n".join(headers) + "\n\n" + body + "\n")
    return path


def test_reads_headers_and_converts_sent_time_to_utc(tmp_path):
    path = write_eml(
        tmp_path, "a.eml", date="Sun, 15 Feb 2026 18:05:29 +0100", subject="RE: ARRIVAL",
        in_reply_to="<arr-0215@example.test>",
    )
    email = read_email(path)
    assert email.file == "a.eml"
    assert email.subject == "RE: ARRIVAL"
    assert email.in_reply_to == "<arr-0215@example.test>"
    assert email.sent_at == datetime(2026, 2, 15, 17, 5, 29, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "separator",
    [
        "-----Original Message-----\nFrom: Master\nSent: 3 February 2026\n\n> ETA COTONOU 16/0500 UTC",
        "---------- Forwarded message ---------\nFrom: Master\n\nETA COTONOU 16/0500 UTC",
        "Begin forwarded message:\n\nETA COTONOU 16/0500 UTC",
        "On Tue, 3 Feb 2026 at 12:05, Master wrote:\n> ETA COTONOU 16/0500 UTC",
        "> ETA COTONOU 16/0500 UTC",
    ],
)
def test_quoted_mail_is_split_from_authored_text(separator):
    authored, quoted = split_quoted(f"Please confirm the latest ETA.\n\nThanks\n\n{separator}")
    assert authored == "Please confirm the latest ETA.\n\nThanks"
    assert "ETA COTONOU 16/0500" in quoted


def test_body_without_quotes_is_all_authored():
    body = "EOSP 15/1520\nNOR TENDERED 15/1630"
    assert split_quoted(body) == (body, "")


def test_inbox_is_ordered_by_sent_time_not_file_name(tmp_path):
    write_eml(tmp_path, "email_01.eml", date="Fri, 20 Feb 2026 10:15:48 +0100")
    write_eml(tmp_path, "email_02.eml", date="Mon, 26 Jan 2026 17:42:10 +0000")
    # 09:00 at UTC+1 is 08:00 UTC: earlier than email_04 although its local clock reads later.
    write_eml(tmp_path, "email_03.eml", date="Tue, 03 Feb 2026 09:00:00 +0100")
    write_eml(tmp_path, "email_04.eml", date="Tue, 03 Feb 2026 08:30:00 +0000")
    assert [e.file for e in read_inbox(tmp_path)] == [
        "email_02.eml", "email_03.eml", "email_04.eml", "email_01.eml",
    ]
