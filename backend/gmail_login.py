"""One-time Gmail sign-in for Outreach.

    .venv/bin/python -m backend.gmail_login

Opens a Browserbase session on the saved context, prints a live-view URL, and
waits (up to 14 min) for you to finish signing in there. On success the session
is released with --persist so the login cookies are saved to the context.
"""

from __future__ import annotations

import sys
import time
from urllib.parse import urlencode

from backend.browserbase_mail import (
    GMAIL_INBOX,
    GmailBrowser,
    _is_signed_out,
    browserbase_ready,
    create_session,
    from_address,
    release_session,
)


def main() -> int:
    if not browserbase_ready():
        print("Set BROWSERBASE_API_KEY and BROWSERBASE_CONTEXT_ID in .env, and install `browse`.")
        return 1

    session = create_session(persist=True, timeout_s=900)
    browser = None
    try:
        browser = GmailBrowser(session)
        login_url = "https://accounts.google.com/ServiceLogin?" + urlencode(
            {"service": "mail", "continue": GMAIL_INBOX, "Email": from_address()}
        )
        browser.page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
        print(f"Sign in as {from_address()} here:\n\n  {session.live_view_url}\n", flush=True)

        deadline = time.time() + 840
        while time.time() < deadline:
            urls = [p.url for p in browser.browser.contexts[0].pages]
            if any(u.startswith("https://mail.google.com/mail/") and not _is_signed_out(u) for u in urls):
                print("Signed in — saving login to the Browserbase context.", flush=True)
                browser.page.wait_for_timeout(5000)
                return 0
            time.sleep(2)
        print("Timed out waiting for sign-in.")
        return 1
    finally:
        if browser:
            browser.close()
        release_session(session.id)


if __name__ == "__main__":
    sys.exit(main())
