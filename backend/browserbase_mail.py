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
        self.page.wait_for_timeout(1500)
        if _is_signed_out(self.page.url):
            raise NotSignedIn(
                "Gmail isn't signed in on Browserbase — run `python -m backend.gmail_login`"
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

    def find_replies(self, *, subject_tag: str, skip_threads: set[str]) -> list[dict]:
        """Human replies to emails whose subject contains `subject_tag`.

        One Gmail search, then each matching thread not in `skip_threads` is
        opened. Bounces (Mail Delivery Subsystem / mailer-daemon), other automated
        senders and our own messages are ignored. Returns
        [{thread_id, sender, subject, body, our_recipients}].
        """
        from urllib.parse import quote

        from backend import replies

        me = from_address().lower()
        # Exclude bounce traffic up front so DSN threads aren't even opened.
        query = (
            f'subject:"{subject_tag}" newer_than:2d '
            f"-from:me -from:mailer-daemon -from:mail-daemon -from:postmaster "
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
            ours = {
                addr.lower()
                for m in messages
                if m["from"].lower() == me
                for addr in (m.get("to") or [])
            }
            for message in messages:
                sender = (message.get("from") or "").lower()
                body = (message.get("body") or "").strip()
                subject = message.get("subject") or ""
                if not sender or sender == me or not body:
                    continue
                if replies.is_delivery_failure(sender=sender, subject=subject, body=body):
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
