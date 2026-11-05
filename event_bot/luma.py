"""Scrape Luma city/category/calendar pages and search results."""
from __future__ import annotations

import logging
from urllib.parse import quote

from dateutil import parser as dtparser

from .fetch import Browser, extract_json_scripts, get
from .models import Event

log = logging.getLogger(__name__)

BASE = "https://luma.com"
API_HOSTS = ("api.lu.ma", "api2.luma.com", "api.luma.com")


def _walk(obj, found: dict) -> None:
    """Recursively find Luma event objects in arbitrary JSON."""
    if isinstance(obj, dict):
        if isinstance(obj.get("name"), str) and obj.get("start_at") and isinstance(obj.get("url"), str):
            ev = _to_event(obj)
            if ev:
                found.setdefault(ev.key, ev)
        for v in obj.values():
            _walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, found)


def _to_event(o: dict) -> Event | None:
    slug = o["url"]
    if "/" in slug.strip("/") and not slug.startswith("http"):
        return None
    url = slug if slug.startswith("http") else f"{BASE}/{slug.strip('/')}"
    try:
        start = dtparser.isoparse(o["start_at"])
    except Exception:  # noqa: BLE001
        start = None
    geo = o.get("geo_address_info") or {}
    location = geo.get("city_state") or geo.get("full_address") or geo.get("city") or ""
    geo_text = " ".join(str(geo.get(k) or "") for k in ("full_address", "address", "city", "region", "city_state"))
    loc_type = (o.get("location_type") or "").lower()
    in_person = None
    if loc_type:
        in_person = loc_type not in ("online", "zoom", "virtual")
    return Event(
        title=o["name"].strip(),
        url=url,
        source="Luma",
        start=start,
        location=location or ("Online" if in_person is False else ""),
        in_person=in_person,
        description=(o.get("description") or o.get("description_short") or "")[:500],
        extra={"geo": geo_text.strip()},
    )


def _scrape_page(url: str, label: str, browser: Browser) -> list[Event]:
    found: dict[str, Event] = {}
    try:
        html = get(url)
        for blob in extract_json_scripts(html):
            _walk(blob, found)
    except Exception as e:  # noqa: BLE001
        log.info("Luma static fetch failed for %s: %s", url, e)
    if not found:
        rendered = browser.render(url, json_hosts=API_HOSTS)
        if rendered:
            html, payloads = rendered
            for blob in extract_json_scripts(html) + payloads:
                _walk(blob, found)
    for ev in found.values():
        ev.source = f"Luma ({label})"
        ev.extra["page"] = label
    log.info("Luma %-22s -> %d events", label, len(found))
    return list(found.values())


def scrape(cfg: dict, browser: Browser) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    errors: list[str] = []
    for page in cfg.get("pages", []):
        try:
            events += _scrape_page(f"{BASE}/{page}", page, browser)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Luma page '{page}': {e}")
    for term in cfg.get("search_terms", []):
        found: dict[str, Event] = {}
        # Luma's search needs a browser session; render it and capture the API responses.
        try:
            for ev in _scrape_page(f"{BASE}/search?q={quote(term)}", f"search: {term}", browser):
                found.setdefault(ev.key, ev)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Luma search '{term}': {e}")
        for ev in found.values():
            ev.source = f"Luma (search: {term})"
        events += found.values()
    return events, errors
