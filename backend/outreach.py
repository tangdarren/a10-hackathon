"""Outreach agent: email Scout's top fixers for quotes and collect replies.

Emails are drafted by Bedrock (template fallback) and really sent from Gmail
in a Browserbase cloud browser, to each contractor's address with "dispatch"
prefixed (see browserbase_mail.demo_recipient).

Replies are real: after sending, the same signed-in Gmail stays open only for
the reply window (REPLY_WAIT_SECONDS) and is searched every REPLY_POLL_SECONDS
for answers to this order's subject tag — bounces from Mail Delivery Subsystem
and other automated senders are ignored. (If SES inbound is configured, replies
arrive through that instead; see replies.py.) Nothing is made up: a fixer who
doesn't answer in time is "no reply".

Runs as a background job so it can be stopped mid-send: stopping releases the
Browserbase session, which kills the browser immediately.
"""

from __future__ import annotations

import os
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from backend import browserbase_mail as mail
from backend import replies
from backend.bedrock_client import bedrock_ready, converse_json

# Base call-out + labor estimate per trade, in dollars.
TRADE_BASE_PRICE: dict[str, int] = {
    "plumbing": 240,
    "hvac": 310,
    "appliance": 180,
    "locksmith": 140,
    "electrical": 260,
    "general": 160,
}

URGENCY_MULTIPLIER: dict[str, float] = {
    "can_wait": 0.9,
    "soon": 1.0,
    "emergency": 1.45,
}

# Outreach only emails the highest-rated subset of Scout's shortlist.
MAX_CONTACTS = 3

# How long Outreach keeps the Gmail open listening for real human replies.
REPLY_WAIT_SECONDS = int(os.getenv("REPLY_WAIT_SECONDS", "300"))
# After the first real quote lands, wait this long for others, then hand off.
# Default 0 = move to Caller as soon as anyone quotes.
REPLY_GRACE_SECONDS = int(os.getenv("REPLY_GRACE_SECONDS", "0"))
# How often the open Gmail is searched for replies while waiting.
REPLY_POLL_SECONDS = int(os.getenv("REPLY_POLL_SECONDS", "12"))


def demo_inboxes() -> list[str]:
    """Real inboxes (teammates) that stand in for the top-rated fixers in a demo."""
    raw = os.getenv("OUTREACH_DEMO_INBOXES", "")
    return [addr.strip().lower() for addr in raw.split(",") if addr.strip()]


def _top_rated_fixers(fixers: list[dict], limit: int = MAX_CONTACTS) -> list[dict]:
    """Highest stars first; ties go to more reviews, then Scout's rank.

    Only fixers with an email can be contacted (real businesses may have none).
    """
    return sorted(
        [fixer for fixer in fixers if fixer.get("email")],
        key=lambda fixer: (
            -fixer.get("rating", 0),
            -fixer.get("reviews", 0),
            fixer.get("rank", 99),
        ),
    )[:limit]


def _round_to(value: float, step: int = 5) -> int:
    return int(step * round(value / step))


def _window_label(start_minutes: int, *, now: datetime) -> str:
    start = now + timedelta(minutes=start_minutes)
    # Round up to the next quarter hour so windows read like real bookings.
    start = start.replace(second=0, microsecond=0)
    start += timedelta(minutes=(-start.minute) % 15)
    end = start + timedelta(minutes=60)

    def fmt(moment: datetime) -> str:
        return moment.strftime("%-I:%M %p").replace(":00 ", " ")

    days = (start.date() - now.date()).days
    day = "" if days <= 0 else "Tomorrow " if days == 1 else start.strftime("%a ")
    return f"{day}{fmt(start)}–{fmt(end)}"


def _template_email(*, order: dict, fixer: dict) -> dict:
    urgency_line = {
        "emergency": "This is an emergency — we need someone on site as soon as possible.",
        "soon": "We'd like someone out today if you have availability.",
        "can_wait": "This isn't urgent; any time in the next few days works.",
    }[order["urgency"]]
    subject = f"Quote request: {order['title']} — {order['location']}"
    body = (
        f"Hi {fixer['name']} team,\n\n"
        f"We manage {order['address']} and have a maintenance request"
        f"{' in Unit ' + order['unit'] if order.get('unit') else ''} "
        f"({order['title'].lower()}):\n\n"
        f"“{order['description']}”\n\n"
        f"{urgency_line} Could you reply with your earliest arrival window "
        f"and a rough quote for the call-out and repair?\n\n"
        f"Thanks,\nDispatch · {order['address']} property management"
    )
    return {"subject": subject, "body": body}


def _bedrock_emails(*, order: dict, fixers: list[dict]) -> dict[str, dict] | None:
    system = (
        "You are Outreach, a property-maintenance dispatch agent. "
        "Write short, professional quote-request emails to contractors. "
        "Respond with JSON only."
    )
    compact = [
        {"id": f["id"], "name": f["name"], "trade": f["trade"], "notes": f["notes"]}
        for f in fixers
    ]
    prompt = f"""
Work order:
- title: {order['title']}
- urgency: {order['urgency']}
- unit: {order.get('unit') or 'n/a'}
- address: {order['address']}
- tenant description: {order['description']}

Contractors to email:
{compact}

Return JSON with this exact shape:
{{
  "emails": [
    {{"id": "contractor_id", "subject": "...", "body": "..."}}
  ]
}}

Rules:
- One email per contractor id
- Body under 90 words, plain text, sign off as "Dispatch · {order['address']} property management"
- Ask for earliest arrival window and a rough quote
- Do not include the tenant's name or phone number
- No markdown
""".strip()

    payload = converse_json(prompt=prompt, system=system, max_tokens=1400, temperature=0.4)
    rows = payload.get("emails") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    valid_ids = {f["id"] for f in fixers}
    emails: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in valid_ids:
            continue
        subject = str(row.get("subject") or "").strip()
        body = str(row.get("body") or "").strip()
        if subject and body:
            emails[row["id"]] = {"subject": subject, "body": body}
    return emails or None


def _estimate(*, order: dict, fixer: dict) -> tuple[int, int]:
    """Typical price and arrival for this trade — only used to fill in what a
    real reply left out (marked "estimated" on the quote)."""
    base = TRADE_BASE_PRICE.get(fixer["trade"], 200)
    price = _round_to(base * URGENCY_MULTIPLIER[order["urgency"]])
    return price, int(fixer.get("earliest_minutes") or 60)


def _pick_best(quotes: list[dict], *, urgency: str) -> dict | None:
    quoted = [q for q in quotes if q["reply"]["status"] == "quoted"]
    if not quoted:
        return None

    min_price = min(q["reply"]["price"] for q in quoted)
    min_eta = min(q["reply"]["arrival_minutes"] for q in quoted)
    time_weight = {"emergency": 0.75, "soon": 0.5, "can_wait": 0.25}[urgency]

    def cost(q: dict) -> float:
        price_pen = q["reply"]["price"] / min_price - 1
        eta_pen = (q["reply"]["arrival_minutes"] - min_eta) / 60
        rating_bonus = (q["rating"] - 4.0) * 0.3
        return time_weight * eta_pen + (1 - time_weight) * price_pen * 3 - rating_bonus

    return min(quoted, key=cost)


class Stopped(Exception):
    pass


class OutreachJob:
    """One Outreach run for one work order. Thread-safe snapshot + stop."""

    def __init__(self, *, order: dict, fixers: list[dict]):
        self.order = order
        self.fixers = _top_rated_fixers(fixers, MAX_CONTACTS)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._session_id: str | None = None
        self._replied = threading.Event()
        self._sent_at: float | None = None
        # Where replies come from: SES inbound if configured, else the Gmail
        # Outreach sends from (read in Browserbase), else nowhere.
        if replies.inbox_replies_enabled():
            reply_mode = "inbox"
        elif mail.browserbase_ready():
            reply_mode = "gmail"
        else:
            reply_mode = "none"
        inboxes = demo_inboxes() if reply_mode != "none" else []
        self._seen_replies: set[tuple[str, str, int]] = set()
        self._answered_threads: set[str] = set()
        self.state: dict = {
            "agent": "outreach",
            "status": "running",
            "steps": [],
            "summary": "",
            "draft_source": "template",
            "send_mode": "browserbase" if mail.browserbase_ready() else "simulated",
            "from_email": mail.from_address(),
            "live_view_url": None,
            # "gmail": read from the sending inbox; "inbox": SES; "none": can't send.
            "reply_mode": reply_mode,
            "reply_to": replies.reply_to_address(),
            "reply_tag": replies.order_tag(order["id"]),
            "awaiting_replies": False,
            "reply_deadline": None,
            "emails": [
                {
                    "fixer_id": f["id"],
                    "name": f["name"],
                    "to": inboxes[i] if i < len(inboxes) else mail.demo_recipient(f["email"]),
                    "real_email": f["email"],
                    "status": "queued",
                }
                for i, f in enumerate(self.fixers)
            ],
            "quotes": [],
            "quoted_count": 0,
            "best_fixer_id": None,
        }
        self._thread = threading.Thread(target=self._run, daemon=True)

    # -- public ------------------------------------------------------------
    def start(self) -> "OutreachJob":
        self._thread.start()
        return self

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self.state)

    def stop(self) -> dict:
        self._stop.set()
        with self._lock:
            session_id = self._session_id
        if session_id:
            mail.release_session(session_id)  # kills the remote browser mid-action
        with self._lock:
            if self.state["status"] == "running":
                self._finish_locked("stopped", "Stopped by dispatcher")
        return self.snapshot()

    def matches_sender(self, sender: str) -> str | None:
        """fixer_id whose emailed address is `sender`, if any."""
        sender = sender.lower()
        with self._lock:
            for row in self.state["emails"]:
                if row["to"].lower() == sender or row["real_email"].lower() == sender:
                    return row["fixer_id"]
        return None

    def add_reply(
        self,
        *,
        fixer_id: str | None,
        text: str,
        sender: str = "",
        subject: str = "",
    ) -> dict | None:
        """Record a real human reply. Unknown senders fill the first fixer still waiting.

        Delivery failures / Mail Delivery Subsystem mail are ignored so Outreach
        keeps waiting for an actual contractor response. Returns the updated
        quote, or None if Outreach isn't listening / the mail was a bounce.
        """
        if replies.is_delivery_failure(sender=sender, subject=subject, body=text):
            self._step("Ignored a bounce / delivery-failure notice — still waiting")
            return None

        with self._lock:
            if self.state["status"] != "running" or not self.state["awaiting_replies"]:
                return None
            waiting = [q for q in self.state["quotes"] if q["reply"]["status"] == "waiting"]
            quote = next((q for q in waiting if q["fixer_id"] == fixer_id), None)
            if quote is None and fixer_id is None and waiting:
                quote = waiting[0]
            if quote is None:
                return None
            fixer = next(f for f in self.fixers if f["id"] == quote["fixer_id"])

        now = datetime.now(timezone.utc).astimezone()
        est_price, est_minutes = _estimate(order=self.order, fixer=fixer)
        reply = replies.parse_reply(
            text,
            now=now,
            fallback_price=est_price,
            fallback_minutes=est_minutes,
            window_label=lambda minutes: _window_label(minutes, now=now),
        )
        reply["reply_after_s"] = int(time.time() - (self._sent_at or time.time()))
        reply["real"] = True

        with self._lock:
            if quote["reply"]["status"] != "waiting":
                return None  # a second email from the same fixer; keep the first
            quote["reply"] = reply
            if reply["status"] == "quoted":
                self.state["steps"].append(
                    f"{quote['name']} replied: ${reply['price']} · {reply['window']}"
                )
            else:
                self.state["steps"].append(f"{quote['name']} replied: can't take it")
            self._refresh_counts_locked()
            result = deepcopy(quote)
        self._replied.set()
        return result

    # -- internals ---------------------------------------------------------
    def _refresh_counts_locked(self) -> None:
        self.state["quoted_count"] = sum(
            1 for q in self.state["quotes"] if q["reply"]["status"] == "quoted"
        )

    def _release_browser(self, browser: "mail.GmailBrowser | None") -> None:
        if browser:
            browser.close()
        with self._lock:
            session_id, self._session_id = self._session_id, None
            self.state["live_view_url"] = None
        if session_id:
            mail.release_session(session_id)

    def _step(self, text: str) -> None:
        with self._lock:
            if self.state["status"] == "running":
                self.state["steps"].append(text)

    def _set_email(self, index: int, **patch) -> None:
        with self._lock:
            self.state["emails"][index].update(patch)

    def _check(self) -> None:
        if self._stop.is_set():
            raise Stopped

    def _finish_locked(self, status: str, step: str | None = None) -> None:
        if step:
            self.state["steps"].append(step)
        self.state["status"] = status
        self.state["live_view_url"] = None
        for email in self.state["emails"]:
            if email["status"] in ("queued", "sending"):
                email["status"] = "cancelled" if status == "stopped" else "failed"

    def _draft(self) -> dict[str, dict]:
        drafts: dict[str, dict] = {}
        if bedrock_ready():
            self._step("Asking Bedrock to draft each email…")
            try:
                drafts = _bedrock_emails(order=self.order, fixers=self.fixers) or {}
                if drafts:
                    with self._lock:
                        self.state["draft_source"] = "bedrock"
                    self._step("Bedrock drafted the emails")
                else:
                    self._step("Bedrock returned no usable drafts — using template")
            except Exception as exc:  # noqa: BLE001 - demo fallback is intentional
                self._step(f"Bedrock unavailable ({type(exc).__name__}) — using template")
        else:
            self._step("AWS creds not set — drafting from template")
        return drafts

    def _run(self) -> None:
        browser: mail.GmailBrowser | None = None
        try:
            top_rated = ", ".join(
                f"{fixer['name']} ({fixer.get('rating', '?')}★)"
                for fixer in self.fixers
            )
            self._step(
                f"Contacting the {len(self.fixers)} top-rated from Scout: {top_rated}"
            )
            drafts = self._draft()
            self._check()

            emails = [
                drafts.get(f["id"]) or _template_email(order=self.order, fixer=f)
                for f in self.fixers
            ]
            # The tag is how a reply finds its way back to this work order.
            emails = [
                {**e, "subject": replies.tag_subject(e["subject"], self.order["id"])}
                for e in emails
            ]
            for index, email in enumerate(emails):
                self._set_email(index, subject=email["subject"], body=email["body"])

            if self.state["send_mode"] != "browserbase":
                self._step("Browserbase isn't configured — can't send email")
                with self._lock:
                    self.state["summary"] = "Browserbase isn't configured, so no emails were sent."
                    self._finish_locked("done")
                return

            if self.state["send_mode"] == "browserbase":
                self._step("Opening Gmail in a Browserbase browser…")
                # Long enough to send and then watch for replies.
                session = mail.create_session(timeout_s=REPLY_WAIT_SECONDS + 600)
                with self._lock:
                    self._session_id = session.id
                    self.state["live_view_url"] = session.live_view_url
                self._check()
                browser = mail.GmailBrowser(session)
                browser.ensure_signed_in()
                self._step(f"Signed in as {mail.from_address()}")

            sent_fixers: list[tuple[dict, dict]] = []
            for index, (fixer, email) in enumerate(zip(self.fixers, emails)):
                self._check()
                to = self.state["emails"][index]["to"]
                self._set_email(index, status="sending")
                self._step(f"Sending to {fixer['name']} ({to})…")
                try:
                    if browser:
                        browser.send(to=to, subject=email["subject"], body=email["body"])
                except mail.NotSignedIn:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self._check()  # a stop mid-send surfaces as a Playwright error
                    self._set_email(index, status="failed", error=type(exc).__name__)
                    self._step(f"Couldn't send to {fixer['name']} ({type(exc).__name__})")
                    continue
                self._set_email(index, status="sent")
                self._step(f"Sent to {fixer['name']}")
                sent_fixers.append((fixer, email))

            self._check()
            self._sent_at = time.time()
            if self.state["reply_mode"] != "gmail":
                # Replies come through SES; nothing needs Gmail open.
                self._release_browser(browser)
                browser = None
            if sent_fixers:
                self._await_replies(sent_fixers, browser)
            else:
                self._finalize([], announce=False)
        except Stopped:
            pass  # stop() already recorded the state
        except mail.NotSignedIn as exc:
            with self._lock:
                self._finish_locked("error", str(exc))
                self.state["summary"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            if self._stop.is_set():
                return
            with self._lock:
                self._finish_locked("error", f"Outreach failed ({type(exc).__name__}: {exc})")
                self.state["summary"] = f"Outreach failed: {exc}"
        finally:
            self._release_browser(browser)

    def _quote_row(self, fixer: dict, email: dict, reply: dict) -> dict:
        to = next(e["to"] for e in self.state["emails"] if e["fixer_id"] == fixer["id"])
        return {
            "fixer_id": fixer["id"],
            "name": fixer["name"],
            "trade": fixer["trade"],
            "email": fixer["email"],
            "phone": fixer["phone"],
            "rating": fixer["rating"],
            "scout_rank": fixer["rank"],
            "sent": {"to": to, **email},
            "reply": reply,
        }

    def _poll_gmail(self, browser: "mail.GmailBrowser") -> None:
        """Search the open Gmail for new human replies and record them."""
        with self._lock:
            outbound_bodies = {
                row.get("body") or ""
                for row in self.state["emails"]
                if row.get("body")
            }
            by_address = {row["to"].lower(): row["fixer_id"] for row in self.state["emails"]}
        try:
            found = browser.find_replies(
                subject_tag=self.state["reply_tag"].strip("[]"),
                skip_threads=self._answered_threads,
                outbound_bodies=outbound_bodies,
            )
        except mail.NotSignedIn:
            raise
        except Exception as exc:  # noqa: BLE001 - keep waiting; try again next poll
            self._check()
            self._step(f"Couldn't check Gmail ({type(exc).__name__}) — retrying")
            return
        for reply in found:
            key = (reply["thread_id"], reply["sender"], hash(reply["body"]))
            if key in self._seen_replies:
                continue
            self._seen_replies.add(key)
            if replies.is_delivery_failure(
                sender=reply.get("sender", ""),
                subject=reply.get("subject", ""),
                body=reply.get("body", ""),
            ):
                # Don't treat DSN as answering the thread — keep watching it.
                self._step("Skipped Mail Delivery / bounce notice")
                continue
            # The thread's own To: line says which fixer this answers.
            # Demo self-replies (from:me) still map via that outbound To:.
            fixer_id = next(
                (by_address[a] for a in reply["our_recipients"] if a in by_address), None
            ) or self.matches_sender(reply["sender"])
            if self.add_reply(
                fixer_id=fixer_id,
                text=replies.strip_quoted(reply["body"]),
                sender=reply.get("sender", ""),
                subject=reply.get("subject", ""),
            ):
                self._answered_threads.add(reply["thread_id"])

    def _await_replies(
        self, sent: list[tuple[dict, dict]], browser: "mail.GmailBrowser | None" = None
    ) -> None:
        """Wait for real human replies: poll Gmail (bounces ignored), or SES."""
        deadline = time.time() + REPLY_WAIT_SECONDS
        with self._lock:
            self.state["quotes"] = [
                self._quote_row(fixer, email, {"status": "waiting"}) for fixer, email in sent
            ]
            self.state["awaiting_replies"] = True
            self.state["reply_deadline"] = (
                datetime.now(timezone.utc) + timedelta(seconds=REPLY_WAIT_SECONDS)
            ).isoformat()
        if browser:
            self._step(
                f"Watching {mail.from_address()} for real replies "
                f"(ignoring bounces) · up to {REPLY_WAIT_SECONDS // 60} min…"
            )
        else:
            self._step(f"Waiting for replies at {self.state['reply_to'] or 'the reply inbox'}…")

        first_quote_at: float | None = None
        last_poll = 0.0
        while True:
            self._check()
            if browser and time.time() - last_poll >= REPLY_POLL_SECONDS:
                self._poll_gmail(browser)
                last_poll = time.time()
            with self._lock:
                replies_in = [q["reply"]["status"] for q in self.state["quotes"]]
            if "waiting" not in replies_in:
                break
            # First real quote → hand off to Caller (optionally brief grace).
            if first_quote_at is None and "quoted" in replies_in:
                first_quote_at = time.time()
                if REPLY_GRACE_SECONDS <= 0:
                    self._step("Got a real quote — moving on to Caller")
                    break
                self._step(
                    f"Got a real quote — waiting {REPLY_GRACE_SECONDS}s for others"
                )
            if first_quote_at and time.time() - first_quote_at >= REPLY_GRACE_SECONDS:
                break
            if time.time() >= deadline:
                break
            self._replied.wait(1)
            self._replied.clear()

        with self._lock:
            self.state["awaiting_replies"] = False
            quotes = self.state["quotes"]
            for q in quotes:
                if q["reply"]["status"] == "waiting":
                    q["reply"] = {"status": "no_reply"}
            quotes = deepcopy(quotes)
        self._finalize(quotes, announce=False)

    def _finalize(self, quotes: list[dict], *, announce: bool) -> None:
        quotes.sort(key=lambda q: (q["reply"]["status"] != "quoted", q["reply"].get("reply_after_s", 99)))
        for q in quotes:
            status = q["reply"]["status"]
            if not announce and status != "no_reply":
                continue  # real replies were already announced as they arrived
            if status == "quoted":
                self._step(f"{q['name']} quoted ${q['reply']['price']} · {q['reply']['window']}")
            elif status == "declined":
                self._step(f"{q['name']} declined")
            else:
                self._step(f"No reply from {q['name']}")

        best = _pick_best(quotes, urgency=self.order["urgency"])
        quoted_count = sum(1 for q in quotes if q["reply"]["status"] == "quoted")
        if best:
            self._step(f"Best quote: {best['name']} (${best['reply']['price']}, {best['reply']['window']})")
            summary = (
                f"Emailed {len(quotes)} fixers, {quoted_count} quoted. "
                f"Recommending {best['name']} at ${best['reply']['price']}."
            )
        elif quotes:
            summary = f"No quotes came back from {len(quotes)} fixers."
        else:
            summary = "No emails were sent."

        with self._lock:
            if self.state["status"] != "running":
                return
            self.state.update(
                quotes=quotes,
                quoted_count=quoted_count,
                best_fixer_id=best["fixer_id"] if best else None,
                summary=summary,
            )
            self._finish_locked("done")


def start_outreach(*, order: dict, fixers: list[dict]) -> OutreachJob:
    return OutreachJob(order=order, fixers=fixers).start()
