"""One-time Gmail sign-in for Outreach.

    .venv/bin/python -m backend.gmail_login

Opens a Browserbase session on the saved context, prints a live-view URL, and
waits until Gmail is clearly signed in (or you press Enter). On success the
session is released with --persist so the login cookies are saved to the
context — Outreach only works after you see "Login saved".
"""

from __future__ import annotations

import select
import sys
import time
from urllib.parse import urlencode

from backend.browserbase_mail import (
    GMAIL_INBOX,
    GmailBrowser,
    browserbase_ready,
    create_session,
    from_address,
    release_session,
)


def _looks_signed_in(browser: GmailBrowser) -> bool:
    """True when any tab is on Gmail and the compose UI is present."""
    try:
        pages = list(browser.browser.contexts[0].pages)
    except Exception:  # noqa: BLE001
        return False
    for page in pages:
        url = page.url or ""
        if "mail.google.com/mail" not in url:
            continue
        if any(
            host in url
            for host in ("accounts.google.com", "workspace.google.com", "ServiceLogin")
        ):
            continue
        try:
            # Compose button (or search box) means the inbox actually loaded.
            if page.locator('div[role="button"][gh="cm"]').count() > 0:
                return True
            if page.locator('input[aria-label="Search mail"]').count() > 0:
                return True
            if page.locator('div[aria-label="Main menu"]').count() > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _enter_pressed() -> bool:
    if not sys.stdin.isatty():
        return False
    ready, _, _ = select.select([sys.stdin], [], [], 0)
    if not ready:
        return False
    line = sys.stdin.readline()
    return True if line is not None else False


def main() -> int:
    if not browserbase_ready():
        print(
            "Set BROWSERBASE_API_KEY and BROWSERBASE_CONTEXT_ID in .env, "
            "and install `browse`."
        )
        return 1

    print(f"Using context {__import__('os').environ.get('BROWSERBASE_CONTEXT_ID')}")
    session = create_session(persist=True, timeout_s=900)
    browser = None
    saved = False
    try:
        browser = GmailBrowser(session)
        login_url = "https://accounts.google.com/ServiceLogin?" + urlencode(
            {"service": "mail", "continue": GMAIL_INBOX, "Email": from_address()}
        )
        browser.page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
        print(
            f"Sign in as {from_address()} in the live view:\n\n"
            f"  {session.live_view_url}\n\n"
            "Finish Google sign-in there (including 2FA). When the Gmail inbox "
            "is visible, press Enter here — or wait and this script will detect it.\n",
            flush=True,
        )

        deadline = time.time() + 840
        while time.time() < deadline:
            if _looks_signed_in(browser) or _enter_pressed():
                # Land on inbox so cookies cover mail.google.com fully.
                try:
                    browser.page.goto(
                        GMAIL_INBOX, wait_until="domcontentloaded", timeout=45_000
                    )
                    browser.page.wait_for_timeout(3000)
                except Exception:  # noqa: BLE001
                    pass
                if not _looks_signed_in(browser) and not _enter_pressed():
                    # Enter was pressed early — give the inbox a moment.
                    print("Waiting for Gmail inbox to finish loading…", flush=True)
                    browser.page.wait_for_timeout(5000)
                print(
                    "Signed in — saving cookies to the Browserbase context "
                    "(keep this terminal open ~10s)…",
                    flush=True,
                )
                browser.page.wait_for_timeout(8000)
                saved = True
                return 0
            time.sleep(2)
        print("Timed out waiting for sign-in.")
        return 1
    finally:
        if browser:
            browser.close()
        release_session(session.id)
        if saved:
            print(
                "Login saved. Re-run Outreach — do not start Dispatch until "
                "you see this line.",
                flush=True,
            )


if __name__ == "__main__":
    sys.exit(main())
