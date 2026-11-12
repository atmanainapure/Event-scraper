"""Daily event bot: scrape Luma, company event pages, Google, Devpost and MLH;
email the top NYC/NJ hackathons ranked by internship-networking ROI.

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

import yaml

from . import company, emailer, hackathons, luma, platforms, ranker
from .classify import detect_kind, two_word_tag
from .fetch import Browser
from .models import Event

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "data" / "seen.json"
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
    if ev.source.startswith("Luma"):
        if not ev.kind:
            return "Luma event with no hackathon/networking/conference keyword"
    elif not ev.kind:
        ev.kind = "event"
    places = cfg.get("locations") or []
    if places:
        if ev.in_person is False:
            return "online event"
        if not in_area(ev, places):
            return "no NYC/NJ location found in location, title or description"
    elif ev.kind != "hackathon" and ev.in_person is False:
        return "online networking event"
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
    print(f"kind: {ev.kind or '-'}  tag: {ev.tag or '-'}")
    print("RESULT: KEPT" if reason is None else f"RESULT: DROPPED - {reason}")
    host = url.split("/")[2]
    covered = [p["name"] for p in cfg.get("event_platforms", []) if host in p["url"]] + \
              [c["name"] for c in cfg.get("companies", []) if c.get("url") and host in c["url"]]
    print("covered by source: " + (", ".join(covered) if covered else "NONE - this site is not scraped"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't send email or update seen.json")
    ap.add_argument("--no-browser", action="store_true", help="skip headless Chromium fallback")
    ap.add_argument("--all", action="store_true", help="include events already emailed before")
    ap.add_argument("--check-url", help="explain whether one event page would be kept, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    browser = Browser()
    if args.no_browser:
        browser.available = False
    if args.check_url:
        try:
            check_url(args.check_url, cfg, browser)
        finally:
            browser.close()
        return
    try:
        l_events, l_err = luma.scrape(cfg.get("luma", {}), browser)
        c_events, c_err = company.scrape(cfg.get("companies", []), browser)
        p_events, p_err = platforms.scrape(cfg.get("event_platforms", []), browser)
    finally:
        browser.close()
    h_events, h_err = hackathons.scrape(cfg.get("hackathon_sources", {}))
    errors = l_err + c_err + p_err + h_err

    events = dedupe([e for e in h_events + p_events + l_events + c_events if keep(e, cfg)])
    seen = load_seen()
    ecfg = cfg.get("email", {})
    linkedin = [(c["name"], f"https://www.linkedin.com/company/{c['linkedin']}/events/")
                for c in cfg.get("companies", []) if c.get("linkedin")]

    if ecfg.get("mode", "top_hackathons") == "top_hackathons":
        hacks = [e for e in events if e.kind == "hackathon"]
        top, method = ranker.rank(hacks, cfg.get("ranking", {}), int(ecfg.get("top_n", 5)))
        new_keys = {r.event.key for r in top if r.event.key not in seen}
        log.info("%d NYC/NJ hackathons found; top %d ranked by %s (%d new)",
                 len(hacks), len(top), method, len(new_keys))
        subject, text, html_body = emailer.build_ranked(top, new_keys, len(hacks), method, errors)
        to_mark = [r.event for r in top]
        should_send = bool(top) or cfg.get("email_when_empty", True)
    else:
        new = events if args.all else [e for e in events if e.key not in seen]
        log.info("%d relevant events, %d new", len(events), len(new))
        subject, text, html_body = emailer.build(new, linkedin, errors)
        to_mark = new
        should_send = bool(new) or cfg.get("email_when_empty", True)

    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "digest.html").write_text(html_body)
    (out / "digest.txt").write_text(f"Subject: {subject}\n\n{text}")
    if args.dry_run:
        log.info("Dry run: wrote %s", out / "digest.html")
        return

    if should_send:
        emailer.send(subject, text, html_body)
        log.info("Email sent: %s", subject)
    today = datetime.now(timezone.utc).date().isoformat()
    for e in to_mark:
        seen.setdefault(e.key, today)
    save_seen(seen)


if __name__ == "__main__":
    main()
