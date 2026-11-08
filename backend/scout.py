"""Scout agent: return the 5 nearest curated fixers for the work-order address."""

from __future__ import annotations

import time

from backend.bedrock_client import bedrock_ready, converse_json
from backend.contractors import trades_for_problem
from backend.property_fixers import fixers_for_address

TARGET_OPTIONS = 5
MIN_SCOUT_SECONDS = 8


def _arrival_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    if hours < 2:
        return f"~{hours:.1f} hr"
    return f"~{int(round(hours))} hr"


def _heuristic_why(contractor: dict, *, urgency: str) -> str:
    bits = [
        f"{contractor['rating']}★",
        f"{contractor['miles']} mi",
        f"ETA {_arrival_label(contractor['earliest_minutes'])}",
    ]
    if urgency == "emergency" and contractor["emergency"]:
        bits.append("emergency ready")
    elif contractor["available_now"]:
        bits.append("available now")
    return " · ".join(bits)


def _fixer_payload(contractor: dict, *, rank: int, why: str) -> dict:
    return {
        "id": contractor["id"],
        "rank": rank,
        "name": contractor["name"],
        "trade": contractor["trade"],
        "phone": contractor["phone"],
        "email": contractor["email"],
        "rating": contractor["rating"],
        "reviews": contractor["reviews"],
        "miles": contractor["miles"],
        "earliest": _arrival_label(contractor["earliest_minutes"]),
        "earliest_minutes": contractor["earliest_minutes"],
        "available_now": contractor["available_now"],
        "emergency": contractor["emergency"],
        "notes": contractor["notes"],
        "website": contractor.get("website", ""),
        "address": contractor.get("address", ""),
        "source": "property_directory",
        "why": why,
    }


def _order_for_problem(candidates: list[dict], *, trades: list[str]) -> list[dict]:
    """Prefer matching trades while keeping all 5 nearest for the property."""
    preferred = [c for c in candidates if c["trade"] in trades]
    other = [c for c in candidates if c["trade"] not in trades]
    preferred.sort(key=lambda c: (c["miles"], -c["rating"]))
    other.sort(key=lambda c: (c["miles"], -c["rating"]))
    return (preferred + other)[:TARGET_OPTIONS]


def _rank_with_bedrock(
    *,
    title: str,
    description: str,
    urgency: str,
    address: str,
    candidates: list[dict],
) -> list[dict] | None:
    compact = [
        {
            "id": c["id"],
            "name": c["name"],
            "trade": c["trade"],
            "rating": c["rating"],
            "miles": c["miles"],
            "address": c.get("address") or "",
            "phone": c.get("phone") or "",
            "earliest_minutes": c["earliest_minutes"],
            "available_now": c["available_now"],
            "emergency": c["emergency"],
            "notes": c["notes"],
        }
        for c in candidates
    ]
    system = (
        "You are Scout, a property-maintenance dispatch agent. "
        "Rank these verified nearby businesses for one work order. "
        "Do not invent businesses or change names, phones, or addresses. "
        "Prefer trade fit, then distance, then rating. "
        "Respond with JSON only."
    )
    prompt = f"""
Work order:
- title: {title}
- urgency: {urgency}
- address: {address}
- description: {description}

Verified nearest businesses (already confirmed for this property):
{compact}

Return JSON with this exact shape:
{{
  "ranked": [
    {{"id": "contractor_id", "why": "one short sentence"}}
  ]
}}

Rules:
- Include every candidate id exactly once
- Do not invent new ids or names
- Keep each why under 18 words
- No markdown
""".strip()

    payload = converse_json(prompt=prompt, system=system)
    ranked_rows = payload.get("ranked") if isinstance(payload, dict) else None
    if not isinstance(ranked_rows, list) or not ranked_rows:
        return None

    by_id = {c["id"]: c for c in candidates}
    ordered: list[dict] = []
    seen: set[str] = set()
    for row in ranked_rows:
        if not isinstance(row, dict):
            continue
        contractor_id = row.get("id")
        if contractor_id not in by_id or contractor_id in seen:
            continue
        why = str(row.get("why") or "").strip() or _heuristic_why(
            by_id[contractor_id], urgency=urgency
        )
        ordered.append(
            _fixer_payload(by_id[contractor_id], rank=len(ordered) + 1, why=why)
        )
        seen.add(contractor_id)

    for contractor in candidates:
        if contractor["id"] in seen:
            continue
        ordered.append(
            _fixer_payload(
                contractor,
                rank=len(ordered) + 1,
                why=_heuristic_why(contractor, urgency=urgency),
            )
        )
    return ordered or None


def run_scout(
    *, problem: str, title: str, description: str, urgency: str, address: str = ""
) -> dict:
    """Return the 5 nearest curated fixers for this managed property.

    Always takes at least 8 seconds so the demo can show Scout working.
    """
    started = time.monotonic()
    steps = [f"Reading work order: {title} at {address or 'unknown address'}"]
    trades = trades_for_problem(problem)
    candidates = fixers_for_address(address) if address else []

    if candidates:
        steps.append(f"Looking up nearest verified helpers for {address}…")
        steps.append(f"Loaded {len(candidates)} nearby businesses with public listings")
        candidates = _order_for_problem(candidates, trades=trades)
        steps.append(
            "Closest matches: "
            + ", ".join(f"{c['name']} ({c['miles']} mi)" for c in candidates)
        )
    elif not address.strip():
        steps.append("No address on the work order — cannot look up nearby helpers")
    else:
        steps.append(f"No curated helpers for “{address}” — pick a managed property")

    ranking_source = "distance"
    fixers: list[dict] = []

    if candidates and bedrock_ready():
        steps.append("Asking Bedrock to rank these verified helpers for the job…")
        try:
            bedrock_fixers = _rank_with_bedrock(
                title=title,
                description=description,
                urgency=urgency,
                address=address,
                candidates=candidates,
            )
            if bedrock_fixers:
                fixers = bedrock_fixers
                ranking_source = "bedrock"
                steps.append("Bedrock ranked the shortlist")
            else:
                steps.append("Bedrock returned no usable ranking — keeping distance order")
        except Exception as exc:  # noqa: BLE001
            steps.append(f"Bedrock unavailable ({type(exc).__name__}) — keeping distance order")
    elif candidates:
        steps.append("AWS creds not set — keeping closest-first order")

    if not fixers and candidates:
        fixers = [
            _fixer_payload(
                contractor,
                rank=index,
                why=_heuristic_why(contractor, urgency=urgency),
            )
            for index, contractor in enumerate(candidates, start=1)
        ]

    fixers = fixers[:TARGET_OPTIONS]
    for index, fixer in enumerate(fixers, start=1):
        fixer["rank"] = index

    # Demo pacing: Scout must feel like a real search.
    elapsed = time.monotonic() - started
    remaining = MIN_SCOUT_SECONDS - elapsed
    if remaining > 0:
        steps.append(f"Confirming listings… ({MIN_SCOUT_SECONDS}s search window)")
        time.sleep(remaining)

    if fixers:
        top = fixers[0]
        steps.append(f"Ready with {len(fixers)} nearest helpers")
        steps.append(
            f"Top match: {top['name']} · {top['miles']} mi · {top.get('address') or ''}"
        )
        summary = (
            f"Found {len(fixers)} nearest helpers for {address}. "
            f"Best option is {top['name']} ({top['miles']} mi)."
        )
    else:
        summary = f"No helpers on file for “{address}”."

    return {
        "agent": "scout",
        "status": "done",
        "trades": trades,
        "steps": steps,
        "summary": summary,
        "fixers": fixers,
        "ranking_source": ranking_source,
        "source": "property_directory",
        "query": {
            "problem": problem,
            "title": title,
            "description": description,
            "urgency": urgency,
            "address": address,
        },
    }
