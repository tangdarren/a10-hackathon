"""Send Gmail through a Browserbase cloud browser.

Session lifecycle (create / live view / release) goes through the `browse`
CLI (`npm install -g browse`). The page itself is driven with Playwright over
CDP: `browse open --cdp` can't attach to a pre-created Browserbase session
(its extension provisioning fails), and only a pre-created session can load
the saved Gmail login context.

Releasing the session kills the remote browser immediately, which is how
Outreach is stopped mid-send.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv()

GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"
SEND_BUTTON = 'div[role="button"][aria-label^="Send"]'
SENT_TOAST = 'text="Message sent"'
NOT_SIGNED_IN_HOSTS = ("accounts.google.com", "workspace.google.com", "www.google.com/gmail")
RECIPIENT_PREFIX = os.getenv("OUTREACH_RECIPIENT_PREFIX", "dispatch")


class NotSignedIn(RuntimeError):
    pass


class BrowserbaseError(RuntimeError):
    pass


def browserbase_ready() -> bool:
    return bool(
        os.getenv("BROWSERBASE_API_KEY")
        and os.getenv("BROWSERBASE_CONTEXT_ID")
        and shutil.which("browse")
    )


def from_address() -> str:
    return os.getenv("OUTREACH_FROM_EMAIL", "")


def demo_recipient(real_email: str) -> str:
    """Prefix the contractor's own address with "dispatch", keeping their domain.

    desk@fixitappliance.example -> dispatchdesk@fixitappliance.example
    """
    local, _, domain = real_email.partition("@")
    return f"{RECIPIENT_PREFIX}{local}@{domain}" if domain else f"{RECIPIENT_PREFIX}{local}"


def _browse(*args: str, timeout: float = 45) -> dict:
    proc = subprocess.run(
        ["browse", "cloud", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ},
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise BrowserbaseError(detail[-1] if detail else "browse CLI failed")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BrowserbaseError("Unexpected browse CLI output") from exc


@dataclass
class Session:
    id: str
    connect_url: str
    live_view_url: str | None


def create_session(*, persist: bool = False, timeout_s: int = 600) -> Session:
    args = [
        "sessions",
        "create",
        "--context-id",
        os.environ["BROWSERBASE_CONTEXT_ID"],
        "--timeout",
        str(timeout_s),
        "--keep-alive",
    ]
    if persist:
        args.append("--persist")
    data = _browse(*args)
    live = None
    try:
        live = _browse("sessions", "debug", data["id"]).get("debuggerFullscreenUrl")
    except BrowserbaseError:
        pass
    return Session(id=data["id"], connect_url=data["connectUrl"], live_view_url=live)


def release_session(session_id: str) -> None:
    """Ends the remote browser. Safe to call twice or from another thread."""
    try:
        _browse("sessions", "update", session_id, timeout=20)
    except (BrowserbaseError, subprocess.TimeoutExpired):
        pass


def _is_signed_out(url: str) -> bool:
    return any(host in url for host in NOT_SIGNED_IN_HOSTS)



# -- reading replies ----------------------------------------------------------

# Every expanded message in the open thread: sender, recipients, visible body.
_THREAD_JS = """
() => [...document.querySelectorAll('div.adn')].map(msg => ({
  from: msg.querySelector('span.gD[email]')?.getAttribute('email') || '',
  to: [...msg.querySelectorAll('span.g2[email]')].map(el => el.getAttribute('email')),
  subject: document.querySelector('h2.hP')?.innerText || '',
  body: (msg.querySelector('div.a3s') || {}).innerText || '',
}))
"""

# Thread ids of the search result rows.
_ROWS_JS = """
() => [...document.querySelectorAll('tr.zA')].map(row => {
  const el = row.querySelector('[data-thread-id], [data-legacy-thread-id]');
  return el ? (el.getAttribute('data-thread-id') || el.getAttribute('data-legacy-thread-id')) : '';
})
"""


class GmailBrowser:
    """Playwright handle on a Browserbase session's first tab."""

    def __init__(self, session: Session):
        from playwright.sync_api import sync_playwright

        self.session = session
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(session.connect_url)
        context = self.browser.contexts[0]
        self.page = context.pages[0] if context.pages else context.new_page()

    def close(self) -> None:
        for fn in (self.browser.close, self._pw.stop):
            try:
                fn()
            except Exception:  # noqa: BLE001 - session may already be gone
                pass

    def ensure_signed_in(self) -> None:
        self.page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=45_000)
        # Google often redirects through accounts.*; give the inbox time to settle.
        for _ in range(8):
            self.page.wait_for_timeout(1000)
            url = self.page.url or ""
            if "mail.google.com/mail" in url and not _is_signed_out(url):
                try:
                    if (
                        self.page.locator('div[role="button"][gh="cm"]').count() > 0
                        or self.page.locator('input[aria-label="Search mail"]').count() > 0
                    ):
                        return
                except Exception:  # noqa: BLE001
                    return
            if _is_signed_out(url):
                break
        raise NotSignedIn(
            "Gmail isn't signed in on Browserbase — run "
            "`.venv/bin/python -m backend.gmail_login`, finish sign-in in the "
            "live view, wait until it prints 'Login saved', then retry Outreach."
        )

    def send(self, *, to: str, subject: str, body: str) -> None:
        # Put only to/subject in the URL — long bodies blow the compose URL and fail.
        query = urlencode({"view": "cm", "fs": "1", "tf": "1", "to": to, "su": subject})
        self.page.goto(
            f"https://mail.google.com/mail/?{query}",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        if _is_signed_out(self.page.url):
            raise NotSignedIn(
                "Gmail session expired — run `python3 -m backend.gmail_login`"
            )
        send_button = self.page.locator(SEND_BUTTON).first
        send_button.wait_for(state="visible", timeout=30_000)
        self.page.wait_for_timeout(800)
        body_box = self.page.locator('div[aria-label="Message Body"]').first
        body_box.wait_for(state="visible", timeout=15_000)
        body_box.click()
        body_box.fill(body)
        self.page.wait_for_timeout(400)
        send_button.click()
        self.page.locator(SENT_TOAST).first.wait_for(state="visible", timeout=20_000)

    def find_replies(
        self,
        *,
        subject_tag: str,
        skip_threads: set[str],
        outbound_bodies: set[str] | None = None,
    ) -> list[dict]:
        """Human replies to emails whose subject contains `subject_tag`.

        Opens matching threads and returns contractor-style replies. Bounces are
        ignored. Demo replies from the same Gmail account (from:me) are accepted
        as long as they aren't the original Outreach send. Returns
        [{thread_id, sender, subject, body, our_recipients}].
        """
        from urllib.parse import quote

        from backend import replies

        me = from_address().lower()
        outbound = {_normalize_mail_body(b) for b in (outbound_bodies or set()) if b}
        # Do NOT use -from:me — demo replies are often sent from this same inbox.
        # Still drop known bounce/DSN traffic from the search list.
        query = (
            f'subject:"{subject_tag}" newer_than:2d '
            f"-from:mailer-daemon -from:mail-daemon -from:postmaster "
            f'-subject:undeliverable -subject:"delivery status" '
            f'-subject:"mail delivery subsystem" -subject:"failure notice"'
        )
        self.page.goto(
            f"https://mail.google.com/mail/u/0/#search/{quote(query)}",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        if _is_signed_out(self.page.url):
            raise NotSignedIn("Gmail session expired — run `python3 -m backend.gmail_login`")
        self.page.wait_for_timeout(2500)  # let the result list render
        thread_ids = [t for t in self.page.evaluate(_ROWS_JS) if t and t not in skip_threads]

        found: list[dict] = []
        for thread_id in thread_ids[:10]:
            row = self.page.locator(
                f'tr.zA:has([data-thread-id="{thread_id}"]), '
                f'tr.zA:has([data-legacy-thread-id="{thread_id}"])'
            ).first
            try:
                row.click(timeout=10_000)
                self.page.locator("div.adn").first.wait_for(state="visible", timeout=15_000)
                self.page.wait_for_timeout(800)
                messages = self.page.evaluate(_THREAD_JS)
            except Exception:  # noqa: BLE001 - skip a thread that won't open
                messages = []
            # Recipients of our original outbound = which fixer this thread is for.
            ours: set[str] = set()
            for message in messages:
                sender = (message.get("from") or "").lower()
                body = (message.get("body") or "").strip()
                if sender == me and _is_outreach_outbound(body, outbound):
                    for addr in message.get("to") or []:
                        if addr:
                            ours.add(addr.lower())
            if not ours:
                for message in messages:
                    if (message.get("from") or "").lower() == me:
                        for addr in message.get("to") or []:
                            if addr:
                                ours.add(addr.lower())

            for message in messages:
                sender = (message.get("from") or "").lower()
                raw_body = (message.get("body") or "").strip()
                subject = message.get("subject") or ""
                if not sender or not raw_body:
                    continue
                if replies.is_delivery_failure(
                    sender=sender, subject=subject, body=raw_body
                ):
                    continue
                # Gmail reply bodies include the quoted original — strip that
                # first or demo self-replies look like our outbound email.
                body = replies.strip_quoted(raw_body) or raw_body
                if _is_outreach_outbound(body, outbound):
                    continue
                if _is_outreach_outbound(raw_body, outbound) and body == raw_body:
                    continue
                found.append(
                    {
                        "thread_id": thread_id,
                        "sender": sender,
                        "subject": subject,
                        "body": body,
                        "our_recipients": sorted(ours),
                    }
                )
            self.page.go_back(wait_until="domcontentloaded", timeout=20_000)
            self.page.wait_for_timeout(1200)
        return found


def _normalize_mail_body(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _is_outreach_outbound(body: str, outbound_bodies: set[str]) -> bool:
    """True for the original Dispatch quote-request email (not a demo reply)."""
    normalized = _normalize_mail_body(body)
    if not normalized:
        return False
    for outbound in outbound_bodies:
        if outbound and (outbound[:160] in normalized or normalized[:160] in outbound):
            return True
    return (
        "could you reply with your earliest arrival" in normalized
        or (
            "we manage" in normalized
            and "maintenance request" in normalized
            and "property management" in normalized
        )
    )
