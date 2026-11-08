"""Free local-business search for Scout via OpenStreetMap.

Uses Nominatim (geocode) + Overpass (nearby craft/shop nodes). No Google
billing. Coverage is thinner than Maps, but results are real mapped
businesses. Optional website scrape fills a published email when present.
"""

from __future__ import annotations

import json
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import urljoin, urlparse

USER_AGENT = "DispatchScout/1.0 (property maintenance demo)"


def _user_agent() -> str:
    contact = os.getenv("SCOUT_CONTACT_EMAIL") or os.getenv("OUTREACH_FROM_EMAIL")
    return f"DispatchScout/1.0 (property maintenance demo; contact={contact})" if contact else USER_AGENT
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

TRADE_QUERIES: dict[str, str] = {
    "plumbing": "plumber",
    "hvac": "HVAC",
    "appliance": "appliance repair",
    "locksmith": "locksmith",
    "electrical": "electrician",
    "general": "handyman",
}

# Overpass tag filters per trade. Widen carefully — false positives hurt ranking.
TRADE_TAGS: dict[str, list[tuple[str, str]]] = {
    "plumbing": [
        ("craft", "plumber"),
        ("shop", "plumbing"),
        ("shop", "bathroom"),
    ],
    "hvac": [
        ("craft", "hvac"),
        ("shop", "hvac"),
        ("craft", "plumber"),
    ],
    "appliance": [
        ("shop", "appliance"),
        ("craft", "electronics_repair"),
        ("shop", "electronics"),
    ],
    "locksmith": [
        ("craft", "locksmith"),
        ("shop", "locksmith"),
    ],
    "electrical": [
        ("craft", "electrician"),
        ("shop", "electrical"),
    ],
    "general": [
        ("craft", "handyman"),
        ("shop", "hardware"),
        ("shop", "doityourself"),
        ("office", "company"),
    ],
}

CONTACT_PATHS = ("/contact", "/contact-us", "/contact/", "/contact-us/", "/about")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
JUNK_EMAIL = re.compile(
    r"(\.(png|jpe?g|gif|svg|webp|css|js)$)|example\.|sentry|wixpress|"
    r"godaddy|domain\.com|email\.com|yourname|@2x|noreply|no-reply",
    re.IGNORECASE,
)
PREFERRED_LOCAL = (
    "service",
    "info",
    "office",
    "contact",
    "dispatch",
    "hello",
    "support",
    "sales",
)

# Nominatim asks for ≤1 req/sec.
_last_nominatim = 0.0


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CONTEXT = _ssl_context()


def places_ready() -> bool:
    """OSM endpoints are public — always available without an API key."""
    return True


class PlacesError(RuntimeError):
    pass


def _http_json(url: str, *, data: bytes | None = None, timeout: float = 45) -> dict | list:
    headers = {"User-Agent": _user_agent()}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        headers["Accept"] = "application/json"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise PlacesError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise PlacesError(str(reason)) from exc


def _overpass(ql: str) -> dict:
    payload = urllib.parse.urlencode({"data": ql}).encode()
    errors: list[str] = []
    for url in OVERPASS_URLS:
        try:
            data = _http_json(url, data=payload, timeout=35)
            if isinstance(data, dict):
                return data
            errors.append(f"{url}: unexpected payload")
        except PlacesError as exc:
            errors.append(f"{url}: {exc}")
    raise PlacesError("; ".join(errors) or "Overpass unavailable")


def _miles(a: dict, b: dict) -> float:
    lat1, lon1, lat2, lon2 = map(
        math.radians, (a["latitude"], a["longitude"], b["latitude"], b["longitude"])
    )
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def geocode(address: str) -> dict | None:
    """Lat/lon for an address via Nominatim (OpenStreetMap)."""
    global _last_nominatim
    wait = 1.05 - (time.monotonic() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    query = urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": 1, "addressdetails": 0}
    )
    _last_nominatim = time.monotonic()
    rows = _http_json(f"{NOMINATIM_URL}?{query}", timeout=20)
    if not isinstance(rows, list) or not rows:
        return None
    return {
        "latitude": float(rows[0]["lat"]),
        "longitude": float(rows[0]["lon"]),
    }


def _overpass_query(tags: list[tuple[str, str]], *, lat: float, lon: float, radius_m: int) -> str:
    # Nodes + ways only (skip relations) to keep public Overpass mirrors fast.
    parts = []
    for key, value in tags:
        parts.append(f'  nwr["{key}"="{value}"](around:{radius_m},{lat},{lon});')
    body = "\n".join(parts)
    return f"[out:json][timeout:20];\n(\n{body}\n);\nout center tags;"


def _element_location(el: dict) -> dict | None:
    if "lat" in el and "lon" in el:
        return {"latitude": float(el["lat"]), "longitude": float(el["lon"])}
    center = el.get("center") or {}
    if "lat" in center and "lon" in center:
        return {"latitude": float(center["lat"]), "longitude": float(center["lon"])}
    return None


def _format_address(tags: dict) -> str:
    bits = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
    ]
    street = " ".join(b for b in bits if b).strip()
    city = tags.get("addr:city") or tags.get("addr:suburb") or ""
    state = tags.get("addr:state", "")
    postcode = tags.get("addr:postcode", "")
    parts = [p for p in (street, city, state, postcode) if p]
    return ", ".join(parts)


def _slug_email(name: str, place_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", name.lower())[:24] or "fixer"
    token = re.sub(r"[^a-z0-9]+", "", place_id.lower())[-10:] or "osm"
    return f"dispatch@{slug}-{token}.example"


def search_trade(trade: str, *, address: str, origin: dict | None) -> list[dict]:
    """Mapped businesses for one trade near origin, closest first."""
    if not origin:
        return []
    tags = TRADE_TAGS.get(trade) or TRADE_TAGS["general"]
    lat, lon = origin["latitude"], origin["longitude"]
    found: list[dict] = []
    seen: set[str] = set()

    for radius_m in (8_000, 16_000, 28_000):
        ql = _overpass_query(tags, lat=lat, lon=lon, radius_m=radius_m)
        data = _overpass(ql)
        elements = data.get("elements") if isinstance(data, dict) else []
        for el in elements or []:
            tags_map = el.get("tags") or {}
            name = (tags_map.get("name") or "").strip()
            if not name:
                continue
            place_id = f"{el.get('type', 'node')}/{el.get('id')}"
            if place_id in seen:
                continue
            location = _element_location(el)
            if not location:
                continue
            seen.add(place_id)
            miles = round(_miles(origin, location), 1)
            phone = tags_map.get("phone") or tags_map.get("contact:phone") or ""
            website = (
                tags_map.get("website")
                or tags_map.get("contact:website")
                or tags_map.get("url")
                or ""
            )
            email = tags_map.get("email") or tags_map.get("contact:email") or ""
            addr = _format_address(tags_map) or address
            opening = (tags_map.get("opening_hours") or "").lower()
            emergency = "24" in opening or "emergency" in name.lower()
            found.append(
                {
                    "id": place_id,
                    "name": name,
                    "trade": trade,
                    # OSM has no star ratings — leave neutral so Bedrock ranks on distance/fit.
                    "rating": 4.2,
                    "reviews": 0,
                    "miles": miles,
                    "earliest_minutes": int(30 + miles * 6),
                    "available_now": True,
                    "emergency": emergency,
                    "licensed": None,
                    "phone": phone,
                    "website": website,
                    "email": email,
                    "address": addr,
                    "notes": addr,
                    "source": "openstreetmap",
                }
            )
        if len(found) >= 8:
            break

    found.sort(key=lambda c: c["miles"])
    return found


# -- email lookup ---------------------------------------------------------


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=6, context=SSL_CONTEXT) as resp:
            if "html" not in (resp.headers.get("Content-Type") or "html"):
                return ""
            return resp.read(600_000).decode(errors="replace")
    except Exception:  # noqa: BLE001 - unreachable sites just have no email
        return ""


def emails_in(html: str) -> list[str]:
    text = unescape(html).replace("%40", "@")
    found = {m.group(0).strip(".").lower() for m in EMAIL_RE.finditer(text)}
    for m in re.finditer(r"mailto:([^\"'?>\s]+)", text, re.IGNORECASE):
        found.add(m.group(1).strip(".").lower())
    return sorted(e for e in found if not JUNK_EMAIL.search(e))


def pick_email(emails: list[str], website: str) -> str:
    if not emails:
        return ""
    site = (urlparse(website).hostname or "").lower().removeprefix("www.")

    def rank(email: str) -> tuple:
        local, _, domain = email.partition("@")
        same_site = bool(site) and (domain == site or domain.endswith("." + site))
        preferred = next(
            (i for i, p in enumerate(PREFERRED_LOCAL) if local.startswith(p)), 99
        )
        return (not same_site, preferred, len(email))

    return min(emails, key=rank)


def find_email(website: str) -> str:
    """The business's published email from its own site, or ''."""
    if not website:
        return ""
    home = _fetch(website)
    emails = emails_in(home)
    if not emails:
        for path in CONTACT_PATHS:
            emails = emails_in(_fetch(urljoin(website, path)))
            if emails:
                break
    return pick_email(emails, website)


def fill_emails(businesses: list[dict]) -> None:
    """Fill emails from OSM tags, websites, or a demo-safe placeholder."""
    need_web = [b for b in businesses if not b.get("email") and b.get("website")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for business, email in zip(
            need_web, pool.map(lambda b: find_email(b.get("website", "")), need_web)
        ):
            if email:
                business["email"] = email
    for business in businesses:
        if not business.get("email"):
            business["email"] = _slug_email(business["name"], business["id"])
