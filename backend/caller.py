"""Caller agent: phone every Outreach responder to confirm timing and book.

CallerJob runs in the background, one call at a time, so the board can follow
along. With ElevenLabs configured, each call is a live voice conversation in
the browser: the dispatcher plays the contractor and talks to the agent
(backend/voice.py). When it ends, Bedrock reads the transcript to decide
whether they confirmed, and at what price and time. Without ElevenLabs (or on
"simulate"), the call is simulated from their Outreach quote. The best
confirmed quote is booked after the calls.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from backend import replies, voice

from backend.bedrock_client import bedrock_ready, converse_json


def _confirmation_code(order_id: str, fixer_id: str) -> str:
    digest = hashlib.sha256(f"{order_id}|{fixer_id}".encode()).hexdigest()
    return f"DSP-{digest[:4].upper()}"


def _site_label(order: dict) -> str:
    unit = (order.get("unit") or "").strip()
    address = order.get("address") or order.get("building") or "the property"
    if unit:
        return f"Unit {unit} at {address}"
    return address


def _template_transcript(*, order: dict, quote: dict, code: str) -> list[dict]:
    reply = quote["reply"]
    site = _site_label(order)
    return [
        {"speaker": "fixer", "text": f"{quote['name']}, this is dispatch speaking."},
        {
            "speaker": "caller",
            "text": (
                f"Hi, this is Dispatch calling about {site}. You quoted "
                f"${reply['price']} by email for {order['title'].lower()}. "
                f"Can you confirm a tech for {reply['window']}?"
            ),
        },
        {
            "speaker": "fixer",
            "text": f"Yes, {reply['window']} works. Who should the tech ask for on site?",
        },
        {
            "speaker": "caller",
            "text": (
                f"The tenant at {site} will let you in. "
                f"We'll email them your window next. Your booking reference is {code}."
            ),
        },
        {"speaker": "fixer", "text": f"Got it — {code}. We'll see them then."},
    ]


def _bedrock_transcript(*, order: dict, quote: dict, code: str) -> list[dict] | None:
    reply = quote["reply"]
    system = (
        "You are Caller, a property-maintenance dispatch agent placing a short "
        "phone call to a contractor to confirm an arrival window. "
        "Write the call as a realistic, brief transcript. Respond with JSON only."
    )
    prompt = f"""
Work order: {order['title']} at {_site_label(order)} (urgency: {order['urgency']})
Tenant description: {order['description']}
Contractor: {quote['name']} ({quote['trade']})
Their emailed quote: ${reply['price']}, arrival window {reply['window']}
Booking reference to give them: {code}

Return JSON with this exact shape:
{{
  "transcript": [
    {{"speaker": "fixer" | "caller", "text": "..."}}
  ]
}}

Rules:
- 4 to 6 turns, contractor answers first
- The contractor confirms the {reply['window']} window and the ${reply['price']} estimate
- Caller states the booking reference {code}
- Do not mention the tenant's name or phone number
- Do not invent anyone's personal name; the caller introduces itself as Dispatch
- Speakers must strictly alternate: fixer, caller, fixer, caller…
- Each line under 35 words, no markdown
""".strip()

    payload = converse_json(prompt=prompt, system=system, max_tokens=700, temperature=0.5)
    rows = payload.get("transcript") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    lines: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        raw = str(row.get("speaker") or "").lower()
        speaker = "caller" if ("call" in raw or "dispatch" in raw) else "fixer"
        lines.append({"speaker": speaker, "text": text})

    # Reject one-sided or out-of-order calls; the template is better than nonsense.
    alternates = all(a["speaker"] != b["speaker"] for a, b in zip(lines, lines[1:]))
    mentions_code = any(code in line["text"] for line in lines)
    if len(lines) < 3 or not alternates or lines[0]["speaker"] != "fixer" or not mentions_code:
        return None
    return lines


def _build_call(*, order: dict, quote: dict) -> dict:
    reply = quote["reply"]
    code = _confirmation_code(order["id"], quote["fixer_id"])
    transcript: list[dict] | None = None
    script_source = "template"
    if bedrock_ready():
        try:
            transcript = _bedrock_transcript(order=order, quote=quote, code=code)
            if transcript:
                script_source = "bedrock"
        except Exception:  # noqa: BLE001 - demo fallback is intentional
            transcript = None
    if not transcript:
        transcript = _template_transcript(order=order, quote=quote, code=code)

    return {
        "fixer_id": quote["fixer_id"],
        "name": quote["name"],
        "trade": quote["trade"],
        "phone": quote["phone"],
        "window": reply["window"],
        "arrival_minutes": reply["arrival_minutes"],
        "price": reply["price"],
        "confirmation": code,
        "transcript": transcript,
        "script_source": script_source,
    }


def run_caller(*, order: dict, outreach: dict) -> dict:
    quoted = [
        quote
        for quote in (outreach.get("quotes") or [])
        if quote.get("reply", {}).get("status") == "quoted"
    ]

    if not quoted:
        return {
            "agent": "caller",
            "status": "done",
            "steps": ["No replies to call — nothing to confirm"],
            "summary": "Nobody replied to Outreach, so no calls were placed.",
            "calls": [],
            "called_count": 0,
            "booking": None,
            "transcript": [],
            "script_source": "none",
        }

    reply_word = "reply" if len(quoted) == 1 else "replies"
    steps = [f"Outreach got {len(quoted)} {reply_word} — calling each one"]
    calls: list[dict] = []
    for quote in quoted:
        steps.append(f"Dialing {quote['name']} at {quote['phone']}…")
        call = _build_call(order=order, quote=quote)
        calls.append(call)
        steps.append(
            f"Confirmed {call['name']} · {call['window']} · ${call['price']}"
        )

    return _booking_result(order=order, outreach=outreach, calls=calls, steps=steps)


def _quoted(outreach: dict) -> list[dict]:
    return [
        quote
        for quote in (outreach.get("quotes") or [])
        if quote.get("reply", {}).get("status") == "quoted"
    ]


def _no_calls_result(summary: str, steps: list[str]) -> dict:
    return {
        "agent": "caller",
        "status": "done",
        "steps": steps,
        "summary": summary,
        "calls": [],
        "called_count": 0,
        "booking": None,
        "transcript": [],
        "script_source": "none",
    }


def _booking_result(*, order: dict, outreach: dict, calls: list[dict], steps: list[str]) -> dict:
    """Book the best call that ended confirmed."""
    confirmed = [call for call in calls if call.get("confirmed", True)]
    if not confirmed:
        steps.append("Nobody confirmed on the phone")
        result = _no_calls_result(
            f"Called {len(calls)} responder{'s' if len(calls) != 1 else ''}; nobody confirmed.",
            steps,
        )
        result.update(calls=calls, called_count=len(calls))
        return result

    best_id = outreach.get("best_fixer_id")
    booked_call = next((call for call in confirmed if call["fixer_id"] == best_id), None)
    if not booked_call:
        booked_call = min(confirmed, key=lambda call: (call["price"], call["arrival_minutes"]))

    steps.append(
        f"Booked {booked_call['name']} · ref {booked_call['confirmation']}"
    )
    steps.append("Ready for dispatcher to email the tenant")

    now = datetime.now(timezone.utc).astimezone()
    expected_at = now + timedelta(minutes=int(booked_call["arrival_minutes"]))
    expected_at = expected_at.replace(second=0, microsecond=0)
    expected_at += timedelta(minutes=(-expected_at.minute) % 15)
    day_label = "Today" if expected_at.date() == now.date() else expected_at.strftime("%a %b %-d")
    expected_label = f"{day_label} · {booked_call['window']}"

    booking = {
        "fixer_id": booked_call["fixer_id"],
        "name": booked_call["name"],
        "trade": booked_call["trade"],
        "phone": booked_call["phone"],
        "window": booked_call["window"],
        "arrival_minutes": booked_call["arrival_minutes"],
        "expected_at": expected_at.isoformat(),
        "expected_label": expected_label,
        "price": booked_call["price"],
        "confirmation": booked_call["confirmation"],
        "tenant_notified": False,
    }

    return {
        "agent": "caller",
        "status": "done",
        "steps": steps,
        "summary": (
            f"Called {len(calls)} responder{'s' if len(calls) != 1 else ''}. "
            f"Booked {booked_call['name']} for {booked_call['window']} "
            f"at ${booked_call['price']}."
        ),
        "calls": calls,
        "called_count": len(calls),
        "booking": booking,
        "transcript": booked_call["transcript"],
        "script_source": booked_call["script_source"],
    }


# -- live, one-call-at-a-time job -------------------------------------------

# How long Caller waits for the dispatcher to join each call before skipping it.
CALL_WAIT_S = int(os.getenv("CALLER_CALL_WAIT_S", "900"))
SIM_LINE_DELAY_S = float(os.getenv("CALLER_SIM_LINE_DELAY_S", "1.1"))
_CONFIRM_RE = re.compile(
    r"\b(yes|yeah|yep|sure|works|confirm(ed)?|sounds good|see you|we'll be there|deal|book it)\b",
    re.IGNORECASE,
)


class Stopped(Exception):
    pass


def _call_variables(*, order: dict, quote: dict, code: str) -> dict:
    reply = quote["reply"]
    return {
        "contractor_name": quote["name"],
        "trade": quote["trade"],
        "job_title": order["title"],
        "job_description": order["description"],
        "site": _site_label(order),
        "urgency": order["urgency"].replace("_", " "),
        "quoted_price": reply["price"],
        "quoted_window": reply["window"],
        "booking_ref": code,
    }


def _rule_analysis(transcript: list[dict], quote: dict) -> dict:
    said = " ".join(line["text"] for line in transcript if line["speaker"] == "fixer")
    facts = replies._rule_parse(said, datetime.now().astimezone()) if said else {}
    declined = bool(facts.get("declined"))
    return {
        "confirmed": bool(said) and not declined and bool(_CONFIRM_RE.search(said)),
        "price": facts.get("price"),
        "arrival_minutes": facts.get("arrival_minutes"),
        "summary": "",
    }


def _bedrock_analysis(transcript: list[dict], quote: dict) -> dict | None:
    reply = quote["reply"]
    now = datetime.now().astimezone()
    lines = "\n".join(
        f"{'Dispatch' if line['speaker'] == 'caller' else 'Contractor'}: {line['text']}"
        for line in transcript
    )
    system = (
        "You review a phone call between a dispatch agent and a contractor and "
        "record the outcome. Respond with JSON only."
    )
    prompt = f"""
Current local time: {now.strftime('%A %-I:%M %p')}
Contractor: {quote['name']}
Their emailed quote before the call: ${reply['price']}, arrival window {reply['window']}

Call transcript:
\"\"\"{lines[:6000]}\"\"\"

Return JSON with this exact shape:
{{"confirmed": true | false, "price": integer dollars or null, "arrival_minutes": integer minutes from now or null, "summary": "one sentence"}}

Rules:
- confirmed is true only if the contractor clearly agreed to do the job
- price: the final price they agreed to on the call; null if they didn't change or state one
- arrival_minutes: minutes from the current time until the arrival they agreed to; null if unchanged or unstated
- summary under 25 words, no markdown
""".strip()
    payload = converse_json(prompt=prompt, system=system, max_tokens=250, temperature=0)
    if not isinstance(payload, dict) or "confirmed" not in payload:
        return None

    def as_int(value) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "confirmed": bool(payload.get("confirmed")),
        "price": as_int(payload.get("price")),
        "arrival_minutes": as_int(payload.get("arrival_minutes")),
        "summary": str(payload.get("summary") or "").strip(),
    }


def analyze_call(transcript: list[dict], quote: dict) -> dict:
    """Bedrock reads the transcript; regex rules if Bedrock is unavailable."""
    result, source = None, "rules"
    if transcript and bedrock_ready():
        try:
            result = _bedrock_analysis(transcript, quote)
            source = "bedrock" if result else "rules"
        except Exception:  # noqa: BLE001 - demo fallback is intentional
            result = None
    if result is None:
        result = _rule_analysis(transcript, quote)
    return {**result, "analyzed_by": source}


class CallerJob:
    """Calls each Outreach responder in turn. Thread-safe snapshot + stop.

    With ElevenLabs configured, each call waits for the dispatcher to join it
    in the browser (session → complete); simulate() or no ElevenLabs falls
    back to a paced simulated call. `on_done(result)` gets the final result.
    """

    def __init__(self, *, order: dict, outreach: dict, on_done=None):
        self.order = order
        self.outreach = outreach
        self.on_done = on_done
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._action = threading.Event()
        self._pending: tuple[str, str | None] | None = None
        quoted = _quoted(outreach)
        live = voice.voice_ready()
        self.state: dict = {
            "agent": "caller",
            "status": "running",
            "steps": [],
            "summary": "",
            "voice_mode": "elevenlabs" if live else "simulated",
            "calls": [
                {
                    "fixer_id": q["fixer_id"],
                    "name": q["name"],
                    "trade": q["trade"],
                    "phone": q["phone"],
                    "mode": "voice" if live else "simulated",
                    "status": "queued",
                    "transcript": [],
                }
                for q in quoted
            ],
            "current": None,
            "called_count": 0,
            "booking": None,
            "transcript": [],
            "script_source": "none",
        }
        self._quotes = {q["fixer_id"]: q for q in quoted}
        self._thread = threading.Thread(target=self._run, daemon=True)

    # -- public ------------------------------------------------------------
    def start(self) -> "CallerJob":
        self._thread.start()
        return self

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self.state)

    def stop(self) -> dict:
        self._stop.set()
        self._action.set()
        with self._lock:
            if self.state["status"] == "running":
                self.state["steps"].append("Stopped by dispatcher")
                self.state["status"] = "stopped"
                self.state["current"] = None
        return self.snapshot()

    def session(self, index: int) -> dict:
        """Everything the browser needs to start call `index` with the agent."""
        with self._lock:
            self._require_current_locked(index, ("ready", "in_call"))
            row = self.state["calls"][index]
            quote = self._quotes[row["fixer_id"]]
        code = _confirmation_code(self.order["id"], quote["fixer_id"])
        variables = {
            k: str(v)
            for k, v in _call_variables(order=self.order, quote=quote, code=code).items()
        }
        # Prefer WebRTC conversation tokens — better for live voice demos.
        try:
            token = voice.conversation_token(participant_name=row["name"])
            connection = {
                "connection_type": "webrtc",
                "conversation_token": token["conversation_token"],
                "conversation_id": token["conversation_id"],
            }
        except voice.VoiceError:
            connection = {
                "connection_type": "websocket",
                "signed_url": voice.signed_url(),
                "conversation_id": None,
            }
        with self._lock:
            self._require_current_locked(index, ("ready", "in_call"))
            self.state["calls"][index]["status"] = "in_call"
            if connection.get("conversation_id"):
                self.state["calls"][index]["conversation_id"] = connection["conversation_id"]
            self.state["steps"].append(f"On a call with {row['name']}…")
        return {
            "index": index,
            "name": row["name"],
            "dynamic_variables": variables,
            **connection,
        }

    def complete(self, index: int, conversation_id: str) -> dict:
        """The browser finished call `index`; analyze conversation_id."""
        with self._lock:
            self._require_current_locked(index, ("ready", "in_call"))
            self._pending = ("complete", conversation_id)
        self._action.set()
        return self.snapshot()

    def simulate(self, index: int) -> dict:
        with self._lock:
            self._require_current_locked(index, ("ready", "in_call"))
            self._pending = ("simulate", None)
        self._action.set()
        return self.snapshot()

    # -- internals ---------------------------------------------------------
    def _require_current_locked(self, index: int, statuses: tuple[str, ...]) -> None:
        if self.state["status"] != "running" or self.state["current"] != index:
            raise ValueError("That call isn't active")
        if self.state["calls"][index]["status"] not in statuses:
            raise ValueError("That call isn't waiting to be joined")

    def _check(self) -> None:
        if self._stop.is_set():
            raise Stopped

    def _step(self, text: str) -> None:
        with self._lock:
            if self.state["status"] == "running":
                self.state["steps"].append(text)

    def _patch(self, index: int, **patch) -> None:
        with self._lock:
            self.state["calls"][index].update(patch)

    def _sleep(self, seconds: float) -> None:
        if self._stop.wait(seconds):
            raise Stopped

    def _run(self) -> None:
        try:
            calls = self.state["calls"]
            if not calls:
                self._finish(_no_calls_result(
                    "Nobody replied to Outreach, so no calls were placed.",
                    ["No replies to call — nothing to confirm"],
                ))
                return
            word = "reply" if len(calls) == 1 else "replies"
            self._step(f"Outreach got {len(calls)} {word} — calling each one")

            finished: list[dict] = []
            for index in range(len(calls)):
                self._check()
                with self._lock:
                    self.state["current"] = index
                    row = deepcopy(self.state["calls"][index])
                quote = self._quotes[row["fixer_id"]]
                if row["mode"] == "voice":
                    call = self._voice_call(index, row, quote)
                else:
                    call = self._simulated_call(index, row, quote)
                finished.append(call)
                with self._lock:
                    self.state["called_count"] = len(finished)

            self._check()
            steps = self.snapshot()["steps"]
            self._finish(
                _booking_result(order=self.order, outreach=self.outreach, calls=finished, steps=steps)
            )
        except Stopped:
            pass
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if self.state["status"] == "running":
                    self.state["status"] = "error"
                    self.state["summary"] = f"Caller failed: {exc}"
                    self.state["steps"].append(f"Caller failed ({type(exc).__name__}: {exc})")

    def _finish(self, result: dict) -> None:
        with self._lock:
            if self.state["status"] != "running":
                return
            self.state.update(result, current=None, voice_mode=self.state["voice_mode"])
            self.state["status"] = "done"
            final = deepcopy(self.state)
        if self.on_done:
            self.on_done(final)

    def _simulated_call(self, index: int, row: dict, quote: dict) -> dict:
        call = _build_call(order=self.order, quote=quote)
        self._step(f"Calling {row['name']} (simulated)…")
        self._patch(index, status="in_call", mode="simulated")
        shown: list[dict] = []
        for line in call["transcript"]:
            self._sleep(SIM_LINE_DELAY_S)
            shown.append(line)
            self._patch(index, transcript=list(shown))
        call.update(mode="simulated", status="done", confirmed=True)
        self._patch(index, **call)
        self._step(f"Confirmed {call['name']} · {call['window']} · ${call['price']}")
        return call

    def _voice_call(self, index: int, row: dict, quote: dict) -> dict:
        reply = quote["reply"]
        code = _confirmation_code(self.order["id"], quote["fixer_id"])
        self._patch(index, status="ready")
        self._step(f"Ready to call {row['name']} — join the call")

        deadline = time.time() + CALL_WAIT_S
        action: tuple[str, str | None] | None = None
        while action is None:
            self._check()
            if time.time() >= deadline:
                break
            self._action.wait(1)
            with self._lock:
                action, self._pending = self._pending, None
                self._action.clear()

        base = {
            "fixer_id": quote["fixer_id"],
            "name": quote["name"],
            "trade": quote["trade"],
            "phone": quote["phone"],
            "mode": "voice",
            "window": reply["window"],
            "arrival_minutes": reply["arrival_minutes"],
            "price": reply["price"],
            "confirmation": code,
            "script_source": "elevenlabs",
        }
        if action is None:
            self._step(f"Nobody joined the call to {row['name']} — skipped")
            call = {**base, "status": "skipped", "confirmed": False, "transcript": []}
            self._patch(index, **call)
            return call
        if action[0] == "simulate":
            return self._simulated_call(index, row, quote)

        conversation_id = action[1] or ""
        self._patch(index, status="analyzing", conversation_id=conversation_id)
        self._step(f"Call with {row['name']} ended — saving the Outreach quote…")
        convo = {"status": "processing", "transcript": [], "duration_s": None}
        for _ in range(30):  # ElevenLabs finalizes the transcript a few seconds after hang-up
            try:
                convo = voice.get_conversation(conversation_id)
            except voice.VoiceError as exc:
                convo = {**convo, "error": str(exc)}
            if convo["status"] in ("done", "failed") or (
                convo["transcript"] and convo["status"] != "in-progress"
            ):
                break
            self._sleep(2)

        # Ending the live call books the Outreach quote (price/window already known).
        # Only mark unconfirmed if the contractor clearly declined on the call.
        outcome = analyze_call(convo["transcript"], quote) if convo["transcript"] else {
            "confirmed": True,
            "price": None,
            "arrival_minutes": None,
            "summary": "Booked from Outreach quote after live call.",
            "analyzed_by": "outreach",
        }
        fixer_said = " ".join(
            line["text"] for line in convo["transcript"] if line["speaker"] == "fixer"
        )
        clearly_declined = bool(fixer_said) and not outcome["confirmed"] and bool(
            re.search(
                r"\b(can't|cannot|can not|unable|no thanks|not available|pass)\b",
                fixer_said,
                re.IGNORECASE,
            )
        )
        confirmed = not clearly_declined
        price = reply["price"]
        minutes = reply["arrival_minutes"]
        window = reply["window"]
        if (
            confirmed
            and outcome.get("price")
            and outcome["price"] > 0
            and outcome["price"] != reply["price"]
        ):
            price = outcome["price"]
        if (
            confirmed
            and outcome.get("arrival_minutes") is not None
            and outcome["arrival_minutes"] >= 0
            and outcome["arrival_minutes"] != reply["arrival_minutes"]
        ):
            from backend.outreach import _window_label

            minutes = outcome["arrival_minutes"]
            window = _window_label(minutes, now=datetime.now(timezone.utc).astimezone())

        call = {
            **base,
            "status": "done",
            "confirmed": confirmed,
            "price": price,
            "arrival_minutes": minutes,
            "window": window,
            "transcript": convo["transcript"],
            "summary": outcome.get("summary")
            or (f"Confirmed via Outreach quote · ${price} · {window}"),
            "analyzed_by": "outreach" if confirmed else outcome.get("analyzed_by", "rules"),
            "duration_s": convo.get("duration_s"),
            "conversation_id": conversation_id,
        }
        self._patch(index, **call)
        if confirmed:
            self._step(f"Confirmed {call['name']} · {window} · ${price}")
        else:
            self._step(f"{call['name']} declined on the call")
        return call


def start_caller(*, order: dict, outreach: dict, on_done=None) -> CallerJob:
    return CallerJob(order=order, outreach=outreach, on_done=on_done).start()
