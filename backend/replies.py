"""Inbound quote replies for Outreach — no inbox polling.

Flow (see backend/ses_setup.py for the AWS side):

    contractor hits Reply in their mail app
      → Gmail's Reply-To sends it to REPLY_TO_ADDRESS (a domain on Amazon SES)
      → SES receipt rule publishes the raw email to SNS → SQS
      → ReplyListener long-polls SQS and hands each email to a callback
      → the callback matches it to an order (subject tag) and a fixer (sender),
        and parse_reply() turns the text into a structured quote with Bedrock.

Nothing watches Gmail. The listener only runs when REPLY_QUEUE_URL is set, and
SQS long-polling is a single cheap request every 20 seconds.
"""

from __future__ import annotations

import email
import email.policy
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Callable

from dotenv import load_dotenv

from backend.bedrock_client import bedrock_ready, converse_json

load_dotenv()

TAG_RE = re.compile(r"\[Order ([0-9a-f]{8})\]", re.IGNORECASE)


def reply_queue_url() -> str | None:
    return os.getenv("REPLY_QUEUE_URL") or None


def reply_to_address() -> str | None:
    return os.getenv("REPLY_TO_ADDRESS") or None


def inbox_replies_enabled() -> bool:
    """Real replies are on when SES → SQS is configured."""
    return bool(reply_queue_url())


def order_tag(order_id: str) -> str:
    return f"[Order {order_id[:8]}]"


def tag_subject(subject: str, order_id: str) -> str:
    tag = order_tag(order_id)
    return subject if tag.lower() in subject.lower() else f"{subject} {tag}"


def order_prefix_from(subject: str) -> str | None:
    match = TAG_RE.search(subject or "")
    return match.group(1).lower() if match else None


# -- parsing an email ------------------------------------------------------


@dataclass
class InboundEmail:
    sender: str
    subject: str
    body: str


_QUOTE_HEADER = re.compile(r"^\s*On .+ wrote:\s*$", re.IGNORECASE)


def strip_quoted(text: str) -> str:
    """Keep only what the contractor typed, dropping the quoted original."""
    kept: list[str] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if _QUOTE_HEADER.match(line) or line.strip().startswith("-----Original Message"):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def parse_mime(raw: str) -> InboundEmail:
    msg = email.message_from_string(raw, policy=email.policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    subject = str(msg.get("Subject", ""))
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    return InboundEmail(sender=sender, subject=subject, body=strip_quoted(body))


def parse_sqs_body(body: str) -> InboundEmail | None:
    """SQS message from an SES → SNS → SQS chain (raw delivery on or off)."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("Type") == "Notification":
        try:
            payload = json.loads(payload.get("Message") or "{}")
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict) or payload.get("notificationType") != "Received":
        return None
    content = payload.get("content")
    if content:
        return parse_mime(content)
    headers = (payload.get("mail") or {}).get("commonHeaders") or {}
    sender = parseaddr((headers.get("from") or [""])[0])[1].lower()
    return InboundEmail(sender=sender, subject=headers.get("subject") or "", body="")


# -- delivery failures (bounces) vs real contractor replies ----------------

AUTOMATED_LOCAL_PARTS = (
    "mailer-daemon",
    "mail-daemon",
    "postmaster",
    "no-reply",
    "noreply",
    "donotreply",
    "do-not-reply",
    "mail-delivery-subsystem",
    "mailerdaemon",
)

_BOUNCE_SUBJECT_RE = re.compile(
    r"(undeliverable|delivery\s*status|returned\s*mail|mail\s*delivery\s*subsystem|"
    r"delivery\s*failure|failure\s*notice|couldn.?t\s*be\s*delivered|"
    r"wasn.?t\s*delivered|not\s*delivered)",
    re.IGNORECASE,
)

_BOUNCE_BODY_RE = re.compile(
    r"(delivery\s*status\s*notification|mail\s*delivery\s*subsystem|"
    r"address\s*not\s*found|user\s*unknown|mailbox\s*unavailable|"
    r"permanent\s*(fatal\s*)?error|wasn.?t\s*delivered|was\s*not\s*delivered|"
    r"couldn.?t\s*be\s*delivered|message\s*blocked|returned\s*to\s*sender|"
    r"undeliverable|this\s*is\s*an?(\s*automatically\s*)?generated|"
    r"delivery\s*has\s*failed|recipient\s*address\s*rejected)",
    re.IGNORECASE,
)


def is_automated_sender(address: str) -> bool:
    raw = (address or "").lower()
    email_addr = parseaddr(raw)[1] or raw
    local = email_addr.partition("@")[0]
    display = parseaddr(raw)[0]
    haystack = f"{local} {display} {raw}"
    return any(prefix in haystack for prefix in AUTOMATED_LOCAL_PARTS) or (
        "mail delivery subsystem" in haystack
    )


def is_delivery_failure(
    *,
    sender: str = "",
    subject: str = "",
    body: str = "",
) -> bool:
    """True for bounce / Mail Delivery Subsystem mail — never treat as a quote."""
    if is_automated_sender(sender):
        return True
    if subject and _BOUNCE_SUBJECT_RE.search(subject):
        return True
    sample = (body or "")[:2500]
    if sample and _BOUNCE_BODY_RE.search(sample):
        return True
    return False


# -- turning reply text into a quote --------------------------------------

_DECLINE_RE = re.compile(
    r"\b(can't|cannot|can not|unable|not available|no availability|fully booked|decline|pass on)\b",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(r"(?:\$\s?(\d{2,5}(?:,\d{3})?)|(\d{2,5}(?:,\d{3})?)\s*\$)")
_MINUTES_RE = re.compile(r"\b(\d{1,3})\s*(?:min|mins|minutes)\b", re.IGNORECASE)
_HOURS_RE = re.compile(r"\bin\s+(\d{1,2})\s*(?:h|hr|hrs|hour|hours)\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)


def _minutes_until_clock(hour: int, minute: int, meridiem: str, now: datetime) -> int:
    hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds() // 60)


def _rule_parse(text: str, now: datetime) -> dict:
    price = _PRICE_RE.search(text)
    minutes: int | None = None
    if m := _MINUTES_RE.search(text):
        minutes = int(m.group(1))
    elif m := _HOURS_RE.search(text):
        minutes = int(m.group(1)) * 60
    elif m := _CLOCK_RE.search(text):
        minutes = _minutes_until_clock(int(m.group(1)), int(m.group(2) or 0), m.group(3), now)
    declined = bool(_DECLINE_RE.search(text)) and not price
    price_raw = (price.group(1) or price.group(2)) if price else None
    return {
        "declined": declined,
        "price": int(price_raw.replace(",", "")) if price_raw else None,
        "arrival_minutes": minutes,
    }


def _bedrock_parse(text: str, now: datetime) -> dict | None:
    system = (
        "You read a contractor's email reply to a maintenance quote request and "
        "extract the facts. Respond with JSON only."
    )
    prompt = f"""
Current local time: {now.strftime('%A %-I:%M %p')}

Contractor reply:
\"\"\"{text[:2000]}\"\"\"

Return JSON with this exact shape:
{{"declined": true | false, "price": integer dollars or null, "arrival_minutes": integer minutes from now or null}}

Rules:
- declined is true only if they say they can't take the job
- price is their quoted total or estimate in whole dollars; null if they gave none
- arrival_minutes is how long from the current time until they can arrive; null if not stated
""".strip()
    payload = converse_json(prompt=prompt, system=system, max_tokens=200, temperature=0)
    if not isinstance(payload, dict):
        return None

    def as_int(value) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "declined": bool(payload.get("declined")),
        "price": as_int(payload.get("price")),
        "arrival_minutes": as_int(payload.get("arrival_minutes")),
    }


def parse_reply(
    text: str,
    *,
    now: datetime,
    fallback_price: int,
    fallback_minutes: int,
    window_label: Callable[[int], str],
) -> dict:
    """Turn a contractor's reply into the Quote.reply shape Outreach uses.

    Missing facts fall back to the fixer's listed estimate so Caller can still
    confirm on the phone; `estimated` records which ones were filled in.
    Delivery-failure / bounce text is never turned into a quote.
    """
    message = text.strip() or "(empty reply)"
    if is_delivery_failure(body=message):
        return {
            "status": "declined",
            "message": message,
            "parsed_by": "bounce",
            "bounce": True,
        }

    facts: dict | None = None
    source = "rules"
    if bedrock_ready():
        try:
            facts = _bedrock_parse(message, now)
            source = "bedrock" if facts else "rules"
        except Exception:  # noqa: BLE001 - fall back to regex
            facts = None
    if not facts:
        facts = _rule_parse(message, now)

    if facts["declined"]:
        return {"status": "declined", "message": message, "parsed_by": source}

    estimated = []
    price = facts["price"]
    if not price or price <= 0:
        price, estimated = fallback_price, [*estimated, "price"]
    minutes = facts["arrival_minutes"]
    if minutes is None or minutes < 0:
        minutes, estimated = fallback_minutes, [*estimated, "arrival"]
    return {
        "status": "quoted",
        "price": price,
        "arrival_minutes": minutes,
        "window": window_label(minutes),
        "message": message,
        "parsed_by": source,
        "estimated": estimated,
    }


# -- SQS listener ----------------------------------------------------------


class ReplyListener:
    """Long-polls the SES reply queue on a daemon thread."""

    def __init__(self, on_email: Callable[[InboundEmail], bool]):
        self.on_email = on_email
        self.queue_url = reply_queue_url()
        self.last_error: str | None = None
        self.received = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "ReplyListener":
        if self.queue_url:
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _client(self):
        import boto3

        region = self.queue_url.split(".")[1] if self.queue_url and "sqs." in self.queue_url else None
        return boto3.client(
            "sqs",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
            region_name=region or os.getenv("AWS_REGION", "us-east-1"),
        )

    def _run(self) -> None:
        client = None
        while not self._stop.is_set():
            try:
                client = client or self._client()
                resp = client.receive_message(
                    QueueUrl=self.queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=20
                )
                for message in resp.get("Messages", []):
                    inbound = parse_sqs_body(message.get("Body") or "")
                    if inbound:
                        self.received += 1
                        self.on_email(inbound)
                    # Unmatched or unreadable mail is dropped too; it would never match later.
                    client.delete_message(
                        QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"]
                    )
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - keep listening; creds may refresh
                self.last_error = f"{type(exc).__name__}: {exc}"
                client = None
                self._stop.wait(10)
