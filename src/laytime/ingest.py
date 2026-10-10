"""Read `.eml` files into Email records."""

import logging
import re
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

from laytime.models import Email

log = logging.getLogger(__name__)

# The first line where earlier mail begins: a reply/forward separator, "On ... wrote:",
# or a ">" quoted line. These are general email conventions, not tied to any sender.
QUOTE_START = re.compile(
    r"^[ \t]*(?:-{2,}[ \t]*(?:original|forwarded)[ \t]+message[ \t]*-{2,}"
    r"|begin forwarded message:"
    r"|on .+ wrote:[ \t]*$"
    r"|>)",
    re.IGNORECASE | re.MULTILINE,
)


def split_quoted(body: str) -> tuple[str, str]:
    """Split a body into what the sender wrote and the earlier mail quoted below it."""
    match = QUOTE_START.search(body)
    if match is None:
        return body.strip(), ""
    return body[: match.start()].strip(), body[match.start() :].strip()


def read_email(path: Path) -> Email:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    part = msg.get_body(preferencelist=("plain",))
    authored, quoted = split_quoted(part.get_content() if part else "")
    in_reply_to = msg.get("In-Reply-To")
    return Email(
        file=path.name,
        message_id=str(msg.get("Message-ID", path.name)).strip(),
        in_reply_to=str(in_reply_to).strip() if in_reply_to else None,
        sent_at=parsedate_to_datetime(str(msg["Date"])).astimezone(timezone.utc),
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        authored_text=authored,
        quoted_text=quoted,
    )


def read_inbox(folder: Path) -> list[Email]:
    """All `.eml` files in the folder, oldest first by the time they were sent.

    File names are never used for ordering; Message-ID only breaks exact ties.
    """
    emails = sorted(
        (read_email(path) for path in folder.glob("*.eml")),
        key=lambda e: (e.sent_at, e.message_id),
    )
    log.info("emails read", extra={"stage": "ingest", "count": len(emails)})
    return emails
