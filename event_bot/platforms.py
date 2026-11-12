"""Event platforms that host events on their own site (e.g. Cerebral Valley at
cerebralvalley.ai/e/<slug>). We read the listing page, then each event page."""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import company, luma
from .fetch import Browser, extract_json_scripts, get
from .models import Event

log = logging.getLogger(__name__)


def _html(url: str, browser: Browser, need: str = "") -> str | None:
    """Static fetch, falling back to a headless browser when the page is a JS shell."""
    html = None
    try:
        html = get(url)
    except Exception as e:  # noqa: BLE001
        log.info("static fetch failed for %s: %s", url, e)
    if html and (not need or need in html) and len(BeautifulSoup(html, "html.parser").get_text(strip=True)) > 200:
        return html
    rendered = browser.render(url)
    return rendered[0] if rendered else html


def event_from_page(url: str, html: str, source: str) -> Event | None:
    """Build an Event from a single event page (JSON-LD, embedded JSON, or meta tags)."""
    found: dict = {}
    blobs = extract_json_scripts(html)
    for b in blobs:
        company._jsonld_events(b, url, source, found)
    if not found:
        for b in blobs:
            luma._walk(b, found)
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for n in names:
            el = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if el and el.get("content"):
                return el["content"].strip()
        return ""

    for s in soup(["script", "style", "noscript"]):
        s.decompose()
    body = " ".join(soup.get_text(" ", strip=True).split())[:3000]

    ev = next(iter(found.values()), None)
    if ev is None:
        title = meta("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")
        if not title:
            return None
        ev = Event(title=title, url=url, source=source, start=company.parse_date(body),
                   description=meta("og:description", "description"))
    ev.url, ev.source = url, source
    # Keep the page text so location ("NYC") and type ("hackathon") can be detected.
    ev.description = f"{ev.description} {body}".strip()[:3000]
    if ev.in_person is None:
        low = body.lower()
        if re.search(r"\b(in[- ]person|venue|doors open|address)\b", low):
            ev.in_person = True
        elif re.search(r"\b(virtual only|online only|livestream only)\b", low):
            ev.in_person = False
    return ev


def scrape(platforms: list[dict], browser: Browser) -> tuple[list[Event], list[str]]:
    events, errors = [], []
    for p in platforms:
        name, url, pattern = p["name"], p["url"], p.get("link_pattern", "/e/")
        try:
            html = _html(url, browser, need=pattern)
            if not html:
                raise RuntimeError("could not load listing page")
            host = urlparse(url).netloc
            links = []
            for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
                href = urljoin(url, a["href"]).split("#")[0].split("?")[0]
                if urlparse(href).netloc == host and pattern in urlparse(href).path and href not in links:
                    links.append(href)
            links = links[: int(p.get("max_events", 40))]
            got = 0
            for link in links:
                page = _html(link, browser)
                ev = event_from_page(link, page, name) if page else None
                if ev:
                    events.append(ev)
                    got += 1
            log.info("%-22s -> %d event links, %d events read", name, len(links), got)
            if not links:
                errors.append(f"{name}: no event links matching '{pattern}' on {url}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name} ({url}): {e}")
    return events, errors
