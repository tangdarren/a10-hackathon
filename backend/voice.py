"""Browser voice calls for Caller via an ElevenLabs Conversational AI agent.

The dispatcher plays the contractor in the board's call popup. The browser
opens a WebRTC session with a short-lived conversation token from
conversation_token(), so the API key never leaves the server. When the call
ends the backend fetches the transcript with get_conversation() and Bedrock
analyzes it (see caller.py).

.env:
  ELEVENLABS_API_KEY=...
  ELEVENLABS_AGENT_ID=...   # the "Dispatch Caller" agent

The agent's prompt and first message use {{dynamic variables}} that Caller
fills in per call (contractor, job, quote, booking reference).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv

from backend.places import SSL_CONTEXT

load_dotenv()

API = "https://api.elevenlabs.io/v1/convai"

AGENT_FIRST_MESSAGE = (
    "Hi, is this {{contractor_name}}? Dispatch here about your quote for {{site}}."
)

AGENT_PROMPT = """You are Dispatch calling {{contractor_name}} ({{trade}}) about a quote they emailed.

Job: {{job_title}} at {{site}}. Their quote: ${{quoted_price}}, window {{quoted_window}}.

Keep every spoken line under 18 words. Plain speech only.
Never use brackets, emotion tags, stage directions, emoji, or exclamation marks.
Do not write things like [happy], [sad], or !! — just calm sentences ending in a period.

Call flow (2–4 turns total):
1. Confirm who you reached and restate price + window briefly.
2. Ask them to confirm.
3. If they confirm: give booking reference {{booking_ref}}, say the tenant will let them in, thank them, and say goodbye.
4. If they change price/window: repeat it back, agree, give {{booking_ref}}, goodbye.
5. If they can't: thank them and goodbye.

After goodbye, stop talking."""

DYNAMIC_VARIABLE_DEFAULTS = {
    "contractor_name": "the contractor",
    "trade": "trades",
    "job_title": "maintenance request",
    "job_description": "See work order",
    "site": "the property",
    "urgency": "soon",
    "quoted_price": "0",
    "quoted_window": "today",
    "booking_ref": "DSP-0000",
}

_AUDIO_TAG_RE = re.compile(r"\[[^\[\]]{0,40}\]")
_MULTI_BANG_RE = re.compile(r"!+")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")

_agent_synced = False


def voice_ready() -> bool:
    return bool(os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_AGENT_ID"))


class VoiceError(RuntimeError):
    pass


def clean_speech_text(text: str) -> str:
    """Strip ElevenLabs audio/emotion tags and flashy punctuation from transcripts."""
    cleaned = _AUDIO_TAG_RE.sub("", text or "")
    cleaned = _MULTI_BANG_RE.sub(".", cleaned)
    cleaned = cleaned.replace("！", ".")
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise VoiceError(f"ElevenLabs {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise VoiceError(f"ElevenLabs unreachable: {exc.reason}") from exc


def _get(path: str) -> dict:
    return _request("GET", path)


def ensure_agent_configured(*, force: bool = False) -> None:
    """Register dynamic-variable placeholders + prompt on the ElevenLabs agent once."""
    global _agent_synced
    if (_agent_synced and not force) or not voice_ready():
        return
    agent_id = os.environ["ELEVENLABS_AGENT_ID"]
    _request(
        "PATCH",
        f"/agents/{urllib.parse.quote(agent_id)}",
        {
            "conversation_config": {
                "agent": {
                    "first_message": AGENT_FIRST_MESSAGE,
                    "prompt": {"prompt": AGENT_PROMPT},
                    "dynamic_variables": {
                        "dynamic_variable_placeholders": DYNAMIC_VARIABLE_DEFAULTS,
                    },
                }
            }
        },
    )
    _agent_synced = True


def conversation_token(*, participant_name: str | None = None) -> dict:
    """Short-lived WebRTC token the browser uses to start one voice conversation."""
    ensure_agent_configured()
    query = urllib.parse.urlencode(
        {
            "agent_id": os.environ["ELEVENLABS_AGENT_ID"],
            **({"participant_name": participant_name} if participant_name else {}),
        }
    )
    data = _get(f"/conversation/token?{query}")
    token = data.get("token")
    conversation_id = data.get("conversation_id")
    if not token or not conversation_id:
        raise VoiceError("ElevenLabs returned no conversation token")
    return {"conversation_token": token, "conversation_id": conversation_id}


def signed_url() -> str:
    """Legacy WebSocket signed URL (kept as fallback). Prefer conversation_token()."""
    ensure_agent_configured()
    query = urllib.parse.urlencode({"agent_id": os.environ["ELEVENLABS_AGENT_ID"]})
    data = _get(f"/conversation/get-signed-url?{query}")
    if not data.get("signed_url"):
        raise VoiceError("ElevenLabs returned no signed URL")
    return data["signed_url"]


def get_conversation(conversation_id: str) -> dict:
    """Status and transcript of a finished (or finishing) conversation.

    status: initiated | in-progress | processing | done | failed
    """
    data = _get(f"/conversations/{urllib.parse.quote(conversation_id)}")
    transcript = []
    for row in data.get("transcript") or []:
        text = clean_speech_text(row.get("message") or "")
        if not text:
            continue
        transcript.append(
            {
                "speaker": "caller" if row.get("role") == "agent" else "fixer",
                "text": text,
            }
        )
    meta = data.get("metadata") or {}
    return {
        "status": data.get("status") or "initiated",
        "transcript": transcript,
        "duration_s": meta.get("call_duration_secs"),
    }
