# Dispatch

Property-maintenance demo: tenants report issues via QR, then three agents book a helper.

**Scout** finds nearby fixers → **Outreach** emails them for quotes (Browserbase Gmail) → **Caller** confirms on a live ElevenLabs voice call (you play the plumber) → **Follow up** emails the booking details.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cd frontend && npm install && cd ..
cp .env.example .env   # fill in keys
```

Required in `.env`: `BROWSERBASE_API_KEY`, `BROWSERBASE_CONTEXT_ID`, `OUTREACH_FROM_EMAIL`, `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, plus AWS workshop creds for Bedrock.

One-time Gmail login for Outreach (Browserbase context):

```bash
.venv/bin/python -m backend.gmail_login
```

## Run

```bash
# terminal 1
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# terminal 2
cd frontend && npm run dev -- --host 0.0.0.0 --port 5173
```

Open [http://localhost:5173](http://localhost:5173). Scan the QR (or open `/report`) to file an order, hit **Dispatch**, join the live call when Caller is ready, then **Follow up** from Booked.
