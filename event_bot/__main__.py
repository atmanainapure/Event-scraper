"""Daily event bot.

1. Find hackathons and networking events on Luma, Meetup and Cerebral Valley (NYC/NJ).
2. Keep networking events that help with a tech internship or a strong network.
3. Keep only events involving the top tech/AI companies (Google, Amazon, Meta, ...).
4. If none of those is a hackathon, Google "<company> hackathon New York City" for each.
5. Email everything at 8 AM New York time.

Usage:
    python -m event_bot              # scrape and send email
    python -m event_bot --dry-run    # scrape, write out/digest.html, don't email or mark seen
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import emailer, hackathons, luma, meetup, platforms, topcos
from .classify import detect_kind, two_word_tag
from .fetch import Browser
from .models import Event

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "data" / "seen.json"
STATE_PATH = ROOT / "data" / "state.json"
NY = ZoneInfo("America/New_York")
log = logging.getLogger("event_bot")


# Luma city pages whose events are in that city even when the address is hidden.
LUMA_CITY_PAGES = {"nyc": "New York"}


def in_area(ev: Event, places: list[str]) -> bool:
    if not ev.location and ev.extra.get("page") in LUMA_CITY_PAGES and ev.in_person is not False:
        ev.location = LUMA_CITY_PAGES[ev.extra["page"]]
    text = " ".join((ev.location, ev.extra.get("geo", ""), ev.title, ev.description)).lower()
    pattern = r"(?<![a-z])(" + "|".join(re.escape(p.lower()) for p in places) + r")(?![a-z])"
    return re.search(pattern, text) is not None


def judge(ev: Event, cfg: dict) -> str | None:
    """Return None if the event should be kept, else the reason it is dropped."""
    now = datetime.now(timezone.utc)
    if ev.start:
        start = ev.start if ev.start.tzinfo else ev.start.replace(tzinfo=timezone.utc)
        if start < now - timedelta(days=1):
            return f"date {start.date()} is in the past"
        if start > now + timedelta(days=cfg.get("lookahead_days", 90)):
            return f"date {start.date()} is beyond lookahead_days"
    ev.kind = ev.extra.get("force_kind") or detect_kind(ev, cfg["keywords"])
    if not ev.kind:
        return "not a hackathon or networking event"
    if ev.kind != "hackathon":
        # Networking must be useful for a tech internship or professional network.
        text = f"{ev.title} {ev.extra.get('organizer', '')} {ev.description}".lower()
        if not re.search(cfg["networking_relevance"], text):
            return "networking event not related to tech / careers"
    places = cfg.get("locations") or []
    if places:
        if ev.in_person is False:
            return "online event"
        if not in_area(ev, places):
            return "no NYC/NJ location found in location, title or description"
    ev.tag = two_word_tag(ev)
    return None


def keep(ev: Event, cfg: dict) -> bool:
    return judge(ev, cfg) is None


def dedupe(events: list[Event]) -> list[Event]:
    by_key: dict[str, Event] = {}
    by_title: set[str] = set()
    for e in events:
        tkey = re.sub(r"\W+", "", e.title.lower()) + (e.start.strftime("%Y%m%d") if e.start else "")
        if e.key in by_key or tkey in by_title:
            continue
        by_key[e.key] = e
        by_title.add(tkey)
    return list(by_key.values())


def load_seen() -> dict:
    try:
        return json.loads(SEEN_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_seen(seen: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_PATH.parent.mkdir(exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=0, sort_keys=True))


def check_url(url: str, cfg: dict, browser: Browser) -> None:
    """Print what the bot sees on one event page and whether it would be emailed."""
    html = platforms._html(url, browser)
    ev = platforms.event_from_page(url, html, "check") if html else None
    print(f"URL: {url}")
    if not ev:
        print("RESULT: could not read an event from this page")
        return
    print(f"title: {ev.title}\nstart: {ev.start}\nlocation: {ev.location!r}\nin_person: {ev.in_person}")
    print(f"text sample: {ev.description[:400]!r}")
    reason = judge(ev, cfg)
    print(f"top companies: {', '.join(topcos.match(ev, cfg['top_companies'])) or 'none'}")
    print(f"kind: {ev.kind or '-'}  tag: {ev.tag or '-'}")
    print("RESULT: KEPT" if reason is None else f"RESULT: DROPPED - {reason}")
    host = url.split("/")[2]
    covered = [p["name"] for p in cfg.get("event_platforms", []) if host in p["url"]] + \
              (["Luma"] if host.endswith(("luma.com", "lu.ma")) else []) + \
              (["Meetup"] if host.endswith("meetup.com") else [])
    print("covered by source: " + (", ".join(covered) if covered else "NONE - this site is not scraped"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't send email or update seen.json")
    ap.add_argument("--no-browser", action="store_true", help="skip headless Chromium fallback")
    ap.add_argument("--all", action="store_true", help="include events already emailed before")
    ap.add_argument("--check-url", help="explain whether one event page would be kept, then exit")
    ap.add_argument("--skip-if-sent", action="store_true",
                    help="exit without doing anything if today's email already went out (backup schedule)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    today_ny = datetime.now(NY).date().isoformat()
    state = load_json(STATE_PATH)
    if args.skip_if_sent and state.get("last_sent") == today_ny:
        log.info("Today's email (%s) was already sent; nothing to do", today_ny)
        return

    browser = Browser()
    if args.no_browser:
        browser.available = False
    if args.check_url:
        try:
            check_url(args.check_url, cfg, browser)
        finally:
            browser.close()
        return

    # 1. Collect from Luma, Meetup and Cerebral Valley.
    try:
        l_events, l_err = luma.scrape(cfg.get("luma", {}), browser)
        m_events, m_err = meetup.scrape(cfg.get("meetup", {}), browser)
        p_events, p_err = platforms.scrape(cfg.get("event_platforms", []), browser)
    finally:
        browser.close()
    errors = l_err + m_err + p_err

    # 2. Hackathons + useful networking events in NYC/NJ.
    events = dedupe([e for e in p_events + l_events + m_events if keep(e, cfg)])
    # 3. Only those involving a top company.
    companies = cfg["top_companies"]
    for e in events:
        e.extra["companies"] = topcos.match(e, companies)
    top = [e for e in events if e.extra["companies"]]
    hacks = [e for e in top if e.kind == "hackathon"]
    nets = [e for e in top if e.kind != "hackathon"]
    log.info("%d relevant NYC/NJ events; %d involve a top company (%d hackathons, %d networking)",
             len(events), len(top), len(hacks), len(nets))

    # 4. No top-company hackathon on the three sites -> Google each company.
    leads: list[Event] = []
    searched = False
    if not hacks or cfg.get("google_fallback", {}).get("always"):
        searched = True
        raw, g_err = hackathons.company_leads(companies, cfg.get("google_fallback", {}))
        errors += g_err
        known = {e.key for e in top}
        for e in raw:
            if e.key in known:
                continue
            reason = judge(e, cfg)
            # Google results often have no address; the query was already NYC-specific,
            # so only drop a lead for location when it has an address outside NYC/NJ.
            no_address = reason and reason.startswith("no NYC/NJ location") and not e.location
            if reason and not no_address:
                continue
            e.extra["companies"] = topcos.match(e, companies)
            e.tag = two_word_tag(e)
            leads.append(e)
        leads = dedupe(leads)
        log.info("Google fallback: %d leads", len(leads))

    # 5. Email.
    seen = load_seen()
    new_keys = {e.key for e in hacks + nets + leads if e.key not in seen}
    subject, text, html_body = emailer.build_daily(hacks, nets, leads, searched, new_keys,
                                                    [c["name"] for c in companies], errors)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "digest.html").write_text(html_body)
    (out / "digest.txt").write_text(f"Subject: {subject}\n\n{text}")
    if args.dry_run:
        log.info("Dry run: wrote %s", out / "digest.html")
        return

    emailer.send(subject, text, html_body)
    log.info("Email sent: %s", subject)
    for e in hacks + nets + leads:
        seen.setdefault(e.key, today_ny)
    save_seen(seen)
    state["last_sent"] = today_ny
    STATE_PATH.write_text(json.dumps(state, indent=1))


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}


if __name__ == "__main__":
    main()
