"""Dedicated hackathon sources: Google results, Devpost and MLH."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from . import company
from .fetch import extract_json_scripts, get
from .models import Event

log = logging.getLogger(__name__)

# Google results that are listicles / directories rather than one event.
LISTING = re.compile(r"\b(top \d+|best|list of|upcoming hackathons|hackathons (in|near)|"
                     r"find hackathons|all hackathons|hackathon calendar)\b", re.I)


def _hackathon(ev: Event) -> Event:
    ev.extra["force_kind"] = "hackathon"
    return ev


# ---------------------------------------------------------------- Google ---

def _serpapi(query: str, engine: str, key: str) -> dict:
    return get(f"https://serpapi.com/search.json?engine={engine}&q={quote(query)}"
               f"&hl=en&gl=us&api_key={key}", as_json=True, timeout=40)


def google(cfg: dict) -> tuple[list[Event], list[str]]:
    """Google results via SerpAPI (SERPAPI_KEY) or Google Programmable Search
    (GOOGLE_API_KEY + GOOGLE_CSE_ID). Skipped when neither is configured."""
    serp = os.environ.get("SERPAPI_KEY", "").strip()
    cse_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    cse_id = os.environ.get("GOOGLE_CSE_ID", "").strip()
    if not (serp or (cse_key and cse_id)):
        log.info("Google search skipped: no SERPAPI_KEY or GOOGLE_API_KEY/GOOGLE_CSE_ID")
        return [], ["Google search is off: add a SERPAPI_KEY secret to turn it on (see README)"]

    events: list[Event] = []
    errors: list[str] = []
    for q in cfg.get("google_events_queries", []) if serp else []:
        try:
            data = _serpapi(q, "google_events", serp)
            for r in data.get("events_results", []):
                date = r.get("date") or {}
                start = company.parse_date(" ".join(filter(None, [date.get("start_date"), date.get("when")])))
                addr = ", ".join(r.get("address") or [])
                events.append(_hackathon(Event(
                    title=r.get("title", "").strip(), url=r.get("link", ""), source="Google",
                    start=start, location=addr, in_person=None if addr else None,
                    description=(r.get("description") or "")[:500],
                    extra={"venue": (r.get("venue") or {}).get("name", "")},
                )))
        except Exception as e:  # noqa: BLE001
            errors.append(f"Google events '{q}': {e}")
    for q in cfg.get("google_queries", []):
        try:
            if serp:
                items = [{"title": r.get("title", ""), "link": r.get("link", ""),
                          "snippet": r.get("snippet", "")}
                         for r in _serpapi(q, "google", serp).get("organic_results", [])]
            else:
                items = get(f"https://www.googleapis.com/customsearch/v1?key={cse_key}&cx={cse_id}"
                            f"&q={quote(q)}&num=10", as_json=True).get("items", [])
            for r in items:
                title, snippet = r.get("title", ""), r.get("snippet", "")
                if LISTING.search(title) or "hack" not in f"{title} {snippet}".lower():
                    continue
                events.append(_hackathon(Event(
                    title=re.sub(r"\s*[|·–-]\s*(Devpost|Eventbrite|Luma|Meetup|LinkedIn).*$", "", title).strip(),
                    url=r.get("link", ""), source="Google", start=company.parse_date(snippet),
                    description=snippet[:500],
                )))
        except Exception as e:  # noqa: BLE001
            errors.append(f"Google search '{q}': {e}")
    events = [e for e in events if e.title and e.url]
    log.info("Google                 -> %d hackathon results", len(events))
    return events, errors


# --------------------------------------------------------------- Devpost ---

def devpost(cfg: dict) -> tuple[list[Event], list[str]]:
    events: dict[str, Event] = {}
    errors: list[str] = []
    for q in cfg.get("devpost_queries", []):
        url = ("https://devpost.com/api/hackathons?challenge_type[]=in-person"
               f"&status[]=upcoming&status[]=open&search={quote(q)}")
        try:
            for h in get(url, as_json=True).get("hackathons", []):
                loc = (h.get("displayed_location") or {}).get("location", "")
                prize = re.sub(r"<[^>]+>", "", str(h.get("prize_amount") or ""))
                themes = ", ".join(t.get("name", "") for t in h.get("themes") or [])
                ev = _hackathon(Event(
                    title=h.get("title", "").strip(), url=h.get("url", ""), source="Devpost",
                    start=company.parse_date(h.get("submission_period_dates", "")),
                    location=loc, in_person=True,
                    description=f"Organized by {h.get('organization_name') or 'n/a'}. Themes: {themes}. Prizes: {prize}.",
                    extra={"registrations": h.get("registrations_count") or 0, "prize": prize,
                           "organizer": h.get("organization_name") or ""},
                ))
                events.setdefault(ev.key, ev)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Devpost '{q}': {e}")
    log.info("Devpost                -> %d hackathons", len(events))
    return list(events.values()), errors


# ------------------------------------------------------------------- MLH ---

def _mlh_microdata(html: str, base: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for node in soup.select('[itemtype*="schema.org/Event"]'):
        def prop(name):
            el = node.find(attrs={"itemprop": name})
            if not el:
                return ""
            return el.get("content") or el.get_text(" ", strip=True)

        name = prop("name")
        link = node.find("a", href=True)
        if not name or not link:
            continue
        try:
            start = dtparser.parse(prop("startDate")) if prop("startDate") else None
            if start and start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            start = None
        text = node.get_text(" ", strip=True)
        loc = ", ".join(filter(None, [prop("addressLocality"), prop("addressRegion")]))
        out.append(_hackathon(Event(
            title=name, url=company.urljoin(base, link["href"]), source="MLH", start=start,
            location=loc, in_person=not re.search(r"digital only|online", text, re.I),
            description=text[:300],
        )))
    return out


def mlh(cfg: dict) -> tuple[list[Event], list[str]]:
    if not cfg.get("mlh", True):
        return [], []
    year = datetime.now().year
    found: dict[str, Event] = {}
    errors = []
    for season in (year + 1, year):
        url = f"https://mlh.io/seasons/{season}/events"
        try:
            html = get(url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"MLH {season}: {e}")
            continue
        evs = _mlh_microdata(html, url)
        if not evs:  # site redesign fallback
            tmp: dict = {}
            for blob in extract_json_scripts(html):
                company._jsonld_events(blob, url, "MLH", tmp)
            company._link_events(html, url, "MLH", tmp)
            evs = [_hackathon(e) for e in tmp.values()]
        for e in evs:
            found.setdefault(e.key, e)
    log.info("MLH                    -> %d hackathons", len(found))
    return list(found.values()), (errors if not found else [])


def scrape(cfg: dict) -> tuple[list[Event], list[str]]:
    events, errors = [], []
    for fn in (google, devpost, mlh):
        e, err = fn(cfg)
        events += e
        errors += err
    return events, errors
