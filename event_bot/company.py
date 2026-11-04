"""Scrape company events pages (static HTML first, headless browser as fallback)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from .fetch import Browser, extract_json_scripts, get
from .models import Event

log = logging.getLogger(__name__)

EVENT_HOSTS = ("lu.ma", "luma.com", "eventbrite.", "meetup.com", "linkedin.com/events", "devpost.com",
               "hopin.com", "bevy.com", "splashthat.com", "cvent.com", "gdg.community.dev")
EVENT_PATH = re.compile(r"/(events?|hackathons?|meetups?|summit|conference|world-?tour|devday|local|community)(/|-|$)", re.I)
NAV_TEXT = re.compile(r"^(events?|all events|view (all|more)|see (all|more)|learn more|register( now)?|"
                      r"more|details|upcoming events|past events|home|back|next|previous|read more)$", re.I)
SKIP_TEXT = re.compile(r"on[- ]demand|recording|recap|watch now|replay|podcast|careers?|privacy|cookie|"
                       r"terms of|sign in|log in|contact (us|sales)", re.I)

MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_RE = re.compile(
    rf"\b{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s*[-–]\s*\d{{1,2}})?(?:,?\s+\d{{4}})?"
    rf"|\b\d{{1,2}}\s+{MONTH}(?:\s+\d{{4}})?"
    r"|\b\d{4}-\d{2}-\d{2}\b",
    re.I,
)


def parse_date(text: str) -> datetime | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    raw = re.sub(r"\s*[-–]\s*\d{1,2}(?=,|\s|$)", "", m.group(0))  # "Nov 12-13, 2026" -> "Nov 12, 2026"
    raw = re.sub(r"(\d)(st|nd|rd|th)", r"\1", raw)
    now = datetime.now(timezone.utc)
    try:
        dt = dtparser.parse(raw, default=datetime(now.year, 1, 1), fuzzy=True)
    except Exception:  # noqa: BLE001
        return None
    dt = dt.replace(tzinfo=timezone.utc)
    if not re.search(r"\d{4}", raw) and (now - dt).days > 30:
        dt = dt.replace(year=dt.year + 1)  # no year given and already passed -> next year
    return dt


def _jsonld_events(blob, base: str, source: str, out: dict) -> None:
    if isinstance(blob, list):
        for b in blob:
            _jsonld_events(b, base, source, out)
        return
    if not isinstance(blob, dict):
        return
    if "@graph" in blob:
        _jsonld_events(blob["@graph"], base, source, out)
    t = blob.get("@type")
    types = t if isinstance(t, list) else [t]
    if any(isinstance(x, str) and x.endswith("Event") for x in types) and blob.get("name"):
        url = urljoin(base, blob.get("url") or base)
        loc = blob.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        loc_name = ""
        in_person = None
        if isinstance(loc, dict):
            in_person = loc.get("@type") != "VirtualLocation"
            addr = loc.get("address")
            loc_name = loc.get("name") or (addr.get("addressLocality") if isinstance(addr, dict) else addr) or ""
        mode = str(blob.get("eventAttendanceMode", ""))
        if "Online" in mode:
            in_person = False
        start = None
        if blob.get("startDate"):
            try:
                start = dtparser.parse(blob["startDate"])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
            except Exception:  # noqa: BLE001
                pass
        ev = Event(title=str(blob["name"]).strip(), url=url, source=source, start=start,
                   location=str(loc_name), in_person=in_person,
                   description=str(blob.get("description", ""))[:500])
        out.setdefault(ev.key, ev)
    for v in blob.values():
        if isinstance(v, (dict, list)):
            _jsonld_events(v, base, source, out)


def _card_text(a) -> str:
    """Text of the smallest ancestor that looks like an event card."""
    node = a
    for _ in range(4):
        if node.parent is None:
            break
        node = node.parent
        txt = node.get_text(" ", strip=True)
        if len(txt) > 400:
            break
        if DATE_RE.search(txt):
            return txt
    return a.get_text(" ", strip=True)


def _link_events(html: str, base: str, source: str, out: dict) -> None:
    soup = BeautifulSoup(html, "html.parser")
    base_path = urlparse(base).path.rstrip("/")
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"].strip())
        p = urlparse(href)
        if p.scheme not in ("http", "https"):
            continue
        title = " ".join((a.get("aria-label") or a.get_text(" ", strip=True) or "").split())
        heading = a.find(["h2", "h3", "h4"])
        if heading:
            title = heading.get_text(" ", strip=True)
        if not (8 <= len(title) <= 160) or NAV_TEXT.match(title) or SKIP_TEXT.search(title):
            continue
        is_external_event = any(h in href for h in EVENT_HOSTS)
        is_event_path = bool(EVENT_PATH.search(p.path)) and p.path.rstrip("/") != base_path
        if not (is_external_event or is_event_path):
            continue
        card = _card_text(a)
        ev = Event(title=title, url=href.split("#")[0], source=source, start=parse_date(card),
                   description=card[:400])
        low = card.lower()
        if re.search(r"\b(virtual|online|webinar|livestream)\b", low):
            ev.in_person = False
        elif re.search(r"\b(in[- ]person|venue|office|hq)\b", low):
            ev.in_person = True
        out.setdefault(ev.key, ev)


def scrape_company(company: dict, browser: Browser) -> list[Event]:
    url, name = company["url"], company["name"]
    found: dict[str, Event] = {}
    html = None
    try:
        html = get(url)
    except Exception as e:  # noqa: BLE001
        log.info("%s static fetch failed: %s", name, e)
    if html:
        for blob in extract_json_scripts(html):
            _jsonld_events(blob, url, name, found)
        _link_events(html, url, name, found)
    if len(found) < 2:
        rendered = browser.render(url)
        if rendered:
            html2, _ = rendered
            for blob in extract_json_scripts(html2):
                _jsonld_events(blob, url, name, found)
            _link_events(html2, url, name, found)
    if html is None and not found:
        raise RuntimeError("could not load page")
    log.info("%-22s -> %d candidate events", name, len(found))
    return list(found.values())


def scrape(companies: list[dict], browser: Browser) -> tuple[list[Event], list[str]]:
    events, errors = [], []
    for c in companies:
        if not c.get("url"):
            continue
        try:
            events += scrape_company(c, browser)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{c['name']} ({c['url']}): {e}")
    return events, errors
