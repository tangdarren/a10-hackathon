"""Work-order API for the Dispatch demo."""

from __future__ import annotations

import os
import socket
import threading
import random
from datetime import datetime, timedelta, timezone
import time
from threading import Lock, Thread
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend import browserbase_mail as mail
from backend import replies, voice
from backend.caller import CallerJob
from backend.outreach import OutreachJob, start_outreach
from backend.scout import run_scout

# Demo follow-ups always go here so the property manager can show inbox delivery.
DEMO_FOLLOW_UP_TO = os.getenv("DEMO_FOLLOW_UP_TO") or mail.from_address()

ProblemType = Literal[
    "no_hot_water",
    "leak",
    "no_heat_ac",
    "appliance",
    "lock_access",
    "electrical",
    "other",
]

PROBLEM_LABELS: dict[str, str] = {
    "no_hot_water": "No hot water",
    "leak": "Leak / flooding",
    "no_heat_ac": "No heat or A/C",
    "appliance": "Broken appliance",
    "lock_access": "Lock / access issue",
    "electrical": "Electrical problem",
    "other": "Something else",
}

Urgency = Literal["can_wait", "soon", "emergency"]

URGENCY_LABELS: dict[str, str] = {
    "can_wait": "Can wait",
    "soon": "Need help soon",
    "emergency": "Emergency",
}

# Fixed portfolio the property manager covers (matches resident QR dropdown).
MANAGED_ADDRESSES = frozenset(
    {
        "1420 Elm St, Oakland, CA",
        "900 Harbor Rd, Oakland, CA",
        "455 Telegraph Ave, Oakland, CA",
        "215 Maple Court, Alameda, CA",
    }
)


OrderStatus = Literal[
    "new", "fixers_found", "awaiting_replies", "quotes_in", "no_quotes", "booked"
]

STATUS_LABELS: dict[str, str] = {
    "new": "New",
    "fixers_found": "Fixers found",
    "awaiting_replies": "Awaiting replies",
    "quotes_in": "Quotes in",
    "no_quotes": "No quotes",
    "booked": "Booked",
}


class WorkOrderCreate(BaseModel):
    problem: ProblemType
    problem_other: str | None = Field(default=None, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    address: str = Field(min_length=1, max_length=160)
    unit: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    urgency: Urgency = "soon"


class OrderBooking(BaseModel):
    fixer_name: str
    price: int
    window: str
    expected_at: str
    expected_label: str
    confirmation: str


class WorkOrder(BaseModel):
    id: str
    created_at: str
    problem: ProblemType
    problem_label: str
    problem_other: str | None
    description: str
    address: str
    unit: str | None
    name: str
    email: str
    urgency: Urgency
    urgency_label: str
    title: str
    location: str
    status: OrderStatus = "new"
    status_label: str = STATUS_LABELS["new"]
    booking: OrderBooking | None = None
    follow_up_sent: bool = False


app = FastAPI(title="Dispatch API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    # The UI goes through the Vite proxy (same origin); only allow local/LAN dev origins.
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|10(\.\d+){3}|192\.168(\.\d+){2}|172\.(1[6-9]|2\d|3[01])(\.\d+){2}):\d+$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = Lock()
_orders: list[WorkOrder] = []
_scout_results: dict[str, dict] = {}
_outreach_jobs: dict[str, OutreachJob] = {}
_caller_results: dict[str, dict] = {}  # finished Caller runs (bookings live here)
_caller_jobs: dict[str, CallerJob] = {}
_follow_ups: dict[str, bool] = {}


def _get_order(order_id: str) -> WorkOrder:
    for order in _orders:
        if order.id == order_id:
            return order
    raise HTTPException(status_code=404, detail="Work order not found")


def _status_for(order_id: str) -> OrderStatus:
    """Derive an order's status from how far the agents have gotten. Call under _lock."""
    caller = _caller_results.get(order_id)
    if caller and caller.get("booking"):
        return "booked"
    job = _outreach_jobs.get(order_id)
    outreach = job.snapshot() if job else None
    if outreach and outreach["status"] == "done":
        return "quotes_in" if outreach.get("quoted_count") else "no_quotes"
    if outreach and outreach.get("awaiting_replies"):
        return "awaiting_replies"
    if _scout_results.get(order_id):
        return "fixers_found"
    return "new"


def _booking_for(order_id: str) -> OrderBooking | None:
    caller = _caller_results.get(order_id)
    raw = caller.get("booking") if caller else None
    if not raw:
        return None
    return OrderBooking(
        fixer_name=raw["name"],
        price=int(raw["price"]),
        window=raw["window"],
        expected_at=raw.get("expected_at") or "",
        expected_label=raw.get("expected_label") or raw["window"],
        confirmation=raw["confirmation"],
    )


def _with_status(order: WorkOrder) -> WorkOrder:
    status = _status_for(order.id)
    return order.model_copy(
        update={
            "status": status,
            "status_label": STATUS_LABELS[status],
            "booking": _booking_for(order.id) if status == "booked" else None,
            "follow_up_sent": bool(_follow_ups.get(order.id)),
        }
    )


def _title_for(payload: WorkOrderCreate) -> str:
    if payload.problem == "other":
        other = (payload.problem_other or "").strip()
        return other or "Something else"
    return PROBLEM_LABELS[payload.problem]


def _location_label(*, address: str, unit: str | None) -> str:
    if unit:
        return f"Unit {unit} · {address}"
    return address


def _build_order(
    payload: WorkOrderCreate, created_at: datetime | None = None
) -> WorkOrder:
    description = payload.description.strip()
    address = payload.address.strip()
    unit = (payload.unit or "").strip() or None
    name = payload.name.strip()
    email = payload.email.strip().lower()
    title = _title_for(payload)
    return WorkOrder(
        id=str(uuid4()),
        created_at=(created_at or datetime.now(timezone.utc)).isoformat(),
        problem=payload.problem,
        problem_label=PROBLEM_LABELS[payload.problem],
        problem_other=(payload.problem_other or "").strip() or None,
        description=description,
        address=address,
        unit=unit,
        name=name,
        email=email,
        urgency=payload.urgency,
        urgency_label=URGENCY_LABELS[payload.urgency],
        title=title,
        location=_location_label(address=address, unit=unit),
    )


def _seed_orders() -> None:
    samples = [
        WorkOrderCreate(
            problem="leak",
            description="Water pouring from under the kitchen sink onto the floor.",
            address="1420 Elm St, Oakland, CA",
            unit="2A",
            name="Maya Chen",
            email="maya.chen@example.com",
            urgency="emergency",
        ),
        WorkOrderCreate(
            problem="no_hot_water",
            description="No hot water since this morning. Cold only in shower and sink.",
            address="1420 Elm St, Oakland, CA",
            unit="4B",
            name="Jordan Lee",
            email="jordan.lee@example.com",
            urgency="soon",
        ),
        WorkOrderCreate(
            problem="no_hot_water",
            description="Water heater only lukewarm. Same building stack as 4B.",
            address="1420 Elm St, Oakland, CA",
            unit="4C",
            name="Chris Nguyen",
            email="chris.nguyen@example.com",
            urgency="soon",
        ),
        WorkOrderCreate(
            problem="lock_access",
            description="Front door lock sticks and sometimes won't open with the key.",
            address="900 Harbor Rd, Oakland, CA",
            unit="1B",
            name="Sam Ortiz",
            email="sam.ortiz@example.com",
            urgency="soon",
        ),
        WorkOrderCreate(
            problem="appliance",
            description="Dishwasher won't start. Tried resetting the breaker once.",
            address="215 Maple Court, Alameda, CA",
            unit=None,
            name="Riley Park",
            email="riley.park@example.com",
            urgency="can_wait",
        ),
    ]
    with _lock:
        if _orders:
            return
        _orders.extend(_build_order(sample) for sample in samples)


_seed_orders()


def _lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/demo-link")
def demo_link() -> dict[str, str]:
    """URL phones should open during a live demo (LAN, not localhost)."""
    port = os.getenv("FRONTEND_PORT", "5173")
    ip = _lan_ip()
    origin = f"http://{ip}:{port}"
    return {
        "origin": origin,
        "report_url": f"{origin}/report",
        "board_url": f"{origin}/",
    }


@app.get("/api/work-orders")
def list_work_orders() -> list[WorkOrder]:
    with _lock:
        return [_with_status(order) for order in reversed(_orders)]


@app.get("/api/work-orders/latest")
def latest_work_order() -> WorkOrder | None:
    with _lock:
        return _with_status(_orders[-1]) if _orders else None


@app.get("/api/work-orders/{order_id}")
def get_work_order(order_id: str) -> WorkOrder:
    with _lock:
        return _with_status(_get_order(order_id))


@app.post("/api/work-orders/{order_id}/agents/scout")
def scout_work_order(order_id: str) -> dict:
    """Scout finds available fixers for this work order's problem."""
    with _lock:
        order = _get_order(order_id)

    result = run_scout(
        problem=order.problem,
        title=order.title,
        description=order.description,
        urgency=order.urgency,
        address=order.address,
    )

    with _lock:
        _scout_results[order_id] = result
        old_job = _outreach_jobs.pop(order_id, None)
        _caller_results.pop(order_id, None)
        old_caller = _caller_jobs.pop(order_id, None)
    if old_job:
        old_job.stop()
    if old_caller:
        old_caller.stop()

    return result


@app.get("/api/work-orders/{order_id}/agents/scout")
def get_scout_result(order_id: str) -> dict:
    with _lock:
        _get_order(order_id)
        result = _scout_results.get(order_id)
    if not result:
        raise HTTPException(status_code=404, detail="Scout has not run yet")
    return result


def _stop_all_outreach_locked() -> None:
    jobs = list(_outreach_jobs.values())
    _outreach_jobs.clear()
    for job in jobs:
        threading.Thread(target=job.stop, daemon=True).start()


@app.post("/api/work-orders/{order_id}/agents/outreach", status_code=202)
def outreach_work_order(order_id: str) -> dict:
    """Start Outreach: email Scout's top fixers for quotes via Browserbase Gmail.

    Returns immediately; poll GET for progress, POST .../stop to stop it.
    """
    with _lock:
        order = _get_order(order_id)
        scout = _scout_results.get(order_id)
        old_job = _outreach_jobs.get(order_id)
    if not scout or not scout.get("fixers"):
        raise HTTPException(status_code=409, detail="Run Scout first")
    if old_job and old_job.snapshot()["status"] == "running":
        return old_job.snapshot()

    job = start_outreach(order=order.model_dump(), fixers=scout["fixers"])
    with _lock:
        _outreach_jobs[order_id] = job
        _caller_results.pop(order_id, None)
        stale_caller = _caller_jobs.pop(order_id, None)
    if stale_caller:
        stale_caller.stop()
    return job.snapshot()


@app.get("/api/work-orders/{order_id}/agents/outreach")
def get_outreach_result(order_id: str) -> dict:
    with _lock:
        _get_order(order_id)
        job = _outreach_jobs.get(order_id)
    if not job:
        raise HTTPException(status_code=404, detail="Outreach has not run yet")
    return job.snapshot()


@app.post("/api/work-orders/{order_id}/agents/outreach/stop")
def stop_outreach(order_id: str) -> dict:
    with _lock:
        _get_order(order_id)
        job = _outreach_jobs.get(order_id)
    if not job:
        raise HTTPException(status_code=404, detail="Outreach has not run yet")
    return job.stop()


# -- inbound replies -------------------------------------------------------


def _route_reply(inbound: replies.InboundEmail) -> bool:
    """Hand a reply email to the Outreach job it answers. True if it matched.

    The subject tag ([Order 1a2b3c4d]) picks the work order; the sender picks
    the fixer, else the reply fills the first fixer still waiting.
    """
    prefix = replies.order_prefix_from(inbound.subject)
    with _lock:
        jobs = list(_outreach_jobs.items())
    candidates = [
        job for order_id, job in jobs if prefix is None or order_id.startswith(prefix)
    ]
    for job in candidates:
        fixer_id = job.matches_sender(inbound.sender)
        if prefix is None and fixer_id is None:
            continue  # no tag and an unknown sender: can't tell which order
        if job.add_reply(
            fixer_id=fixer_id,
            text=inbound.body,
            sender=inbound.sender,
            subject=inbound.subject,
        ):
            return True
    return False


_reply_listener: replies.ReplyListener | None = None


@app.on_event("startup")
def _start_reply_listener() -> None:
    global _reply_listener
    if replies.inbox_replies_enabled():
        _reply_listener = replies.ReplyListener(_route_reply).start()


class InboundReply(BaseModel):
    sender: str = Field(default="", max_length=200)
    subject: str = Field(default="", max_length=400)
    body: str = Field(min_length=1, max_length=5000)


@app.post("/api/replies/inbound")
def inbound_reply(payload: InboundReply) -> dict[str, bool]:
    """Inject a reply by hand (testing, or a webhook instead of SQS)."""
    inbound = replies.InboundEmail(
        sender=payload.sender.strip().lower(),
        subject=payload.subject,
        body=replies.strip_quoted(payload.body),
    )
    return {"matched": _route_reply(inbound)}


@app.get("/api/replies/status")
def reply_status() -> dict:
    listener = _reply_listener
    from backend import browserbase_mail as mail

    return {
        "mode": (
            "inbox"
            if replies.inbox_replies_enabled()
            else "gmail"
            if mail.browserbase_ready()
            else "none"
        ),
        "reply_to": replies.reply_to_address(),
        "listening": bool(listener) or mail.browserbase_ready(),
        "received": listener.received if listener else 0,
        "last_error": listener.last_error if listener else None,
    }


@app.post("/api/work-orders/{order_id}/agents/caller", status_code=202)
def caller_work_order(order_id: str) -> dict:
    """Start Caller: phone every Outreach responder in turn, then book the best.

    Returns immediately; poll GET for progress, POST .../stop to stop it.
    """
    with _lock:
        order = _get_order(order_id)
        outreach_job = _outreach_jobs.get(order_id)
        old_job = _caller_jobs.get(order_id)
    outreach = outreach_job.snapshot() if outreach_job else None
    if not outreach or outreach["status"] != "done":
        raise HTTPException(status_code=409, detail="Run Outreach first")
    if old_job and old_job.snapshot()["status"] == "running":
        return old_job.snapshot()

    def store(result: dict, order_id: str = order_id) -> None:
        with _lock:
            if _caller_jobs.get(order_id) is job:
                _caller_results[order_id] = result

    job = CallerJob(order=order.model_dump(), outreach=outreach, on_done=store)
    with _lock:
        _caller_jobs[order_id] = job
        _caller_results.pop(order_id, None)
    job.start()
    return job.snapshot()


@app.get("/api/work-orders/{order_id}/agents/caller")
def get_caller_result(order_id: str) -> dict:
    with _lock:
        _get_order(order_id)
        job = _caller_jobs.get(order_id)
        result = _caller_results.get(order_id)
    if job:
        return job.snapshot()
    if not result:
        raise HTTPException(status_code=404, detail="Caller has not run yet")
    return result


def _current_caller_job(order_id: str) -> CallerJob:
    with _lock:
        _get_order(order_id)
        job = _caller_jobs.get(order_id)
    if not job:
        raise HTTPException(status_code=404, detail="Caller has not run yet")
    return job


@app.post("/api/work-orders/{order_id}/agents/caller/calls/{index}/session")
def caller_call_session(order_id: str, index: int) -> dict:
    """Signed URL + call details for the browser to start this voice call."""
    job = _current_caller_job(order_id)
    try:
        return job.session(index)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except voice.VoiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class CallComplete(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)


@app.post("/api/work-orders/{order_id}/agents/caller/calls/{index}/complete")
def caller_call_complete(order_id: str, index: int, payload: CallComplete) -> dict:
    """The browser call ended; Caller fetches the transcript and analyzes it."""
    job = _current_caller_job(order_id)
    try:
        return job.complete(index, payload.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/work-orders/{order_id}/agents/caller/calls/{index}/simulate")
def caller_call_simulate(order_id: str, index: int) -> dict:
    job = _current_caller_job(order_id)
    try:
        return job.simulate(index)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/work-orders/{order_id}/agents/caller/stop")
def stop_caller(order_id: str) -> dict:
    with _lock:
        _get_order(order_id)
        job = _caller_jobs.get(order_id)
    if not job:
        raise HTTPException(status_code=404, detail="Caller has not run yet")
    return job.stop()


@app.post("/api/work-orders", status_code=201)
def create_work_order(payload: WorkOrderCreate) -> WorkOrder:
    description = payload.description.strip()
    address = payload.address.strip()
    name = payload.name.strip()
    email = payload.email.strip()
    if not description or not address or not name or not email:
        raise HTTPException(status_code=400, detail="Missing required fields")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Enter a valid email")

    if address not in MANAGED_ADDRESSES:
        raise HTTPException(
            status_code=400,
            detail="Pick one of the managed properties",
        )

    if payload.problem == "other" and not (payload.problem_other or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Please say what the problem is",
        )

    order = _build_order(payload)
    with _lock:
        _orders.append(order)

    return _with_status(order)


def _tenant_follow_up_email(*, order: WorkOrder, booking: OrderBooking) -> dict:
    """Demo follow-up always emailed to DEMO_FOLLOW_UP_TO (not the QR fake address)."""
    when = booking.expected_label or booking.window
    subject = f"Your repair is booked — {order.title}"
    body = (
        f"Hi {order.name},\n\n"
        f"Quick update on your {order.title.lower()} request "
        f"at {order.address}"
        f"{f' (Unit {order.unit})' if order.unit else ''}.\n\n"
        f"{booking.fixer_name} is coming in at {when}.\n"
        f"It will cost about ${booking.price}.\n\n"
        f"Booking reference: {booking.confirmation}\n\n"
        f"Reply to this email if you need anything else.\n\n"
        f"Best,\n"
        f"JD Properties"
    )
    return {"subject": subject, "body": body}


@app.post("/api/work-orders/{order_id}/follow-up")
def follow_up_tenant(order_id: str) -> dict:
    """Email booking details to the demo inbox (DEMO_FOLLOW_UP_TO) via Gmail."""
    with _lock:
        order = _with_status(_get_order(order_id))
        if order.status != "booked" or not order.booking:
            raise HTTPException(status_code=409, detail="Book a helper first")
        if _follow_ups.get(order_id):
            raise HTTPException(status_code=409, detail="Follow-up already sent")
        booking = order.booking
        email = _tenant_follow_up_email(order=order, booking=booking)

    to = DEMO_FOLLOW_UP_TO
    if not mail.browserbase_ready():
        raise HTTPException(
            status_code=503,
            detail="Browserbase/Gmail not ready — run `python3 -m backend.gmail_login`",
        )

    browser: mail.GmailBrowser | None = None
    session_id: str | None = None

    try:
        session = mail.create_session()
        session_id = session.id
        browser = mail.GmailBrowser(session)
        browser.ensure_signed_in()
        browser.send(to=to, subject=email["subject"], body=email["body"])
    except mail.NotSignedIn as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Could not send follow-up ({type(exc).__name__}: {exc})",
        ) from exc
    finally:
        if browser:
            browser.close()
        if session_id:
            mail.release_session(session_id)

    with _lock:
        _follow_ups[order_id] = True
        refreshed = _with_status(_get_order(order_id))

    return {
        "sent": True,
        "to": to,
        "name": order.name,
        "tenant_email": order.email,
        "subject": email["subject"],
        "body": email["body"],
        "send_mode": "browserbase",
        "from_email": mail.from_address(),
        "booking": booking.model_dump(),
        "order": refreshed.model_dump(),
    }


SAMPLE_REQUESTS: list[WorkOrderCreate] = [
    WorkOrderCreate(
        problem="no_heat_ac",
        description="Radiator is stone cold and the apartment is 58°F. Thermostat shows on.",
        address="900 Harbor Rd, Oakland, CA",
        unit="3D",
        name="Priya Shah",
        email="priya.shah@example.com",
        urgency="emergency",
    ),
    WorkOrderCreate(
        problem="no_hot_water",
        description="Shower runs cold after a minute. Neighbors on our floor say the same.",
        address="1420 Elm St, Oakland, CA",
        unit="4D",
        name="Marcus Bell",
        email="marcus.bell@example.com",
        urgency="soon",
    ),
    WorkOrderCreate(
        problem="electrical",
        description="Outlets in the bedroom stopped working and the breaker won't stay on.",
        address="455 Telegraph Ave, Oakland, CA",
        unit="2F",
        name="Elena Rossi",
        email="elena.rossi@example.com",
        urgency="soon",
    ),
    WorkOrderCreate(
        problem="leak",
        description="Brown water stain spreading on the bathroom ceiling, dripping slowly.",
        address="900 Harbor Rd, Oakland, CA",
        unit="1A",
        name="Tom Becker",
        email="tom.becker@example.com",
        urgency="emergency",
    ),
    WorkOrderCreate(
        problem="no_heat_ac",
        description="Heat is barely coming on at night. Same riser as 3D I think.",
        address="900 Harbor Rd, Oakland, CA",
        unit="3E",
        name="Aisha Karim",
        email="aisha.karim@example.com",
        urgency="soon",
    ),
    WorkOrderCreate(
        problem="appliance",
        description="Fridge is making a loud buzzing noise and the freezer is frosting over.",
        address="215 Maple Court, Alameda, CA",
        unit=None,
        name="Leo Martins",
        email="leo.martins@example.com",
        urgency="can_wait",
    ),
    WorkOrderCreate(
        problem="other",
        problem_other="Hallway light out",
        description="The light outside my door on the 2nd floor hallway has been out for days.",
        address="1420 Elm St, Oakland, CA",
        unit="2C",
        name="Grace Kim",
        email="grace.kim@example.com",
        urgency="can_wait",
    ),
]

_batch_running = False


SAMPLE_SPREAD_MINUTES = 180


def _scattered_ages(count: int) -> list[timedelta]:
    """Random, distinct ages within the last few hours, oldest first."""
    minutes = random.sample(range(2, SAMPLE_SPREAD_MINUTES + 1), count)
    return [
        timedelta(minutes=m, seconds=random.randint(0, 59))
        for m in sorted(minutes, reverse=True)
    ]


def _send_sample_batch(interval: float) -> None:
    global _batch_running
    try:
        ages = _scattered_ages(len(SAMPLE_REQUESTS))
        for index, (sample, age) in enumerate(zip(SAMPLE_REQUESTS, ages)):
            if index and interval > 0:
                time.sleep(interval)
            order = _build_order(sample, created_at=datetime.now(timezone.utc) - age)
            with _lock:
                _orders.append(order)
    finally:
        with _lock:
            _batch_running = False


@app.post("/api/work-orders/sample-batch", status_code=202)
def send_sample_batch(interval: float = 1.5) -> dict[str, int | float]:
    """Send a pre-made list of resident requests to the property manager.

    Requests trickle in `interval` seconds apart so the board fills live.
    """
    global _batch_running
    interval = max(0.0, min(interval, 10.0))
    with _lock:
        if _batch_running:
            raise HTTPException(status_code=409, detail="Sample batch already sending")
        _batch_running = True
    Thread(target=_send_sample_batch, args=(interval,), daemon=True).start()
    return {"queued": len(SAMPLE_REQUESTS), "interval": interval}


@app.post("/api/work-orders/seed")
def seed_work_orders() -> dict[str, int]:
    with _lock:
        _orders.clear()
        _scout_results.clear()
        _stop_all_outreach_locked()
        _caller_results.clear()
        for caller_job in _caller_jobs.values():
            caller_job.stop()
        _caller_jobs.clear()
        _follow_ups.clear()
    _seed_orders()
    with _lock:
        return {"seeded": len(_orders)}


@app.delete("/api/work-orders")
def clear_work_orders() -> dict[str, int]:
    with _lock:
        count = len(_orders)
        _orders.clear()
        _scout_results.clear()
        _stop_all_outreach_locked()
        _caller_results.clear()
        for caller_job in _caller_jobs.values():
            caller_job.stop()
        _caller_jobs.clear()
        _follow_ups.clear()
    return {"cleared": count}
