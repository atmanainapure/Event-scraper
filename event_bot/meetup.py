"""Scrape Meetup's public event search (meetup.com/find) for NYC events."""
from __future__ import annotations

import logging
from urllib.parse import quote

from dateutil import parser as dtparser

from .fetch import Browser, extract_json_scripts, get
from .models import Event

log = logging.getLogger(__name__)

JSON_HOSTS = ("meetup.com/gql", "meetup.com/api")


def _resolve(v, state: dict):
    """Follow Apollo cache references ({"__ref": "Venue:123"})."""
    if isinstance(v, dict) and "__ref" in v:
        return state.get(v["__ref"], {})
    return v or {}


def _walk(obj, state: dict, found: dict) -> None:
    if isinstance(obj, dict):
        if isinstance(obj.get("title"), str) and obj.get("dateTime") and isinstance(obj.get("eventUrl"), str):
            ev = _to_event(obj, state)
            if ev:
                found.setdefault(ev.key, ev)
        for v in obj.values():
            _walk(v, state, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, state, found)


def _to_event(o: dict, state: dict) -> Event | None:
    try:
        start = dtparser.isoparse(o["dateTime"])
    except Exception:  # noqa: BLE001
        start = None
    venue = _resolve(o.get("venue"), state)
    group = _resolve(o.get("group"), state)
    location = ", ".join(x for x in (venue.get("name"), venue.get("city"), venue.get("state")) if x)
    etype = str(o.get("eventType") or "").upper()
    in_person = False if (o.get("isOnline") or etype == "ONLINE") else (True if venue or etype == "PHYSICAL" else None)
    org = group.get("name", "") if isinstance(group, dict) else ""
    return Event(
        title=o["title"].strip(),
        url=o["eventUrl"].split("?")[0],
        source="Meetup",
        start=start,
        location=location,
        in_person=in_person,
        description=f"Hosted by {org}. {o.get('description') or ''}".strip()[:1500],
        extra={"organizer": org},
    )


def _apollo_state(blobs: list) -> dict:
    for b in blobs:
        try:
            st = b["props"]["pageProps"].get("__APOLLO_STATE__")
            if st:
                return st
        except Exception:  # noqa: BLE001
            continue
    return {}


def scrape(cfg: dict, browser: Browser) -> tuple[list[Event], list[str]]:
    location = cfg.get("location", "us--ny--New York")
    events: dict[str, Event] = {}
    errors: list[str] = []
    for kw in cfg.get("keywords", []):
        url = (f"https://www.meetup.com/find/?keywords={quote(kw)}&location={quote(location)}"
               f"&source=EVENTS&distance=tenMiles")
        found: dict[str, Event] = {}
        try:
            blobs = []
            try:
                blobs = extract_json_scripts(get(url))
            except Exception as e:  # noqa: BLE001
                log.info("Meetup static fetch failed for %r: %s", kw, e)
            state = _apollo_state(blobs)
            for b in blobs:
                _walk(b, state, found)
            if not found:
                rendered = browser.render(url, json_hosts=JSON_HOSTS)
                if rendered:
                    html, payloads = rendered
                    blobs = extract_json_scripts(html)
                    state = _apollo_state(blobs)
                    for b in blobs + payloads:
                        _walk(b, state, found)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Meetup '{kw}': {e}")
        log.info("Meetup %-24r -> %d events", kw, len(found))
        for k, v in found.items():
            events.setdefault(k, v)
    if cfg.get("keywords") and not events:
        errors.append("Meetup: no events could be read (page layout may have changed)")
    return list(events.values()), errors
