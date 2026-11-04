"""Daily event bot: scrape Luma + company event pages, email what's new.

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

from . import company, emailer, luma
from .classify import detect_kind, two_word_tag
from .fetch import Browser
from .models import Event

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "data" / "seen.json"
log = logging.getLogger("event_bot")


def keep(ev: Event, cfg: dict) -> bool:
    now = datetime.now(timezone.utc)
    if ev.start:
        start = ev.start if ev.start.tzinfo else ev.start.replace(tzinfo=timezone.utc)
        if start < now - timedelta(days=1) or start > now + timedelta(days=cfg.get("lookahead_days", 90)):
            return False
    ev.kind = detect_kind(ev, cfg["keywords"])
    if ev.source.startswith("Luma"):
        if not ev.kind:
            return False  # Luma is huge; only keep matching events
    elif not ev.kind:
        ev.kind = "event"
    # Networking only makes sense in person; hackathons can be anywhere.
    if ev.kind != "hackathon" and ev.in_person is False:
        return False
    ev.tag = two_word_tag(ev)
    return True


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't send email or update seen.json")
    ap.add_argument("--no-browser", action="store_true", help="skip headless Chromium fallback")
    ap.add_argument("--all", action="store_true", help="include events already emailed before")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    browser = Browser()
    if args.no_browser:
        browser.available = False
    try:
        l_events, l_err = luma.scrape(cfg.get("luma", {}), browser)
        c_events, c_err = company.scrape(cfg.get("companies", []), browser)
    finally:
        browser.close()

    events = dedupe([e for e in l_events + c_events if keep(e, cfg)])
    seen = load_seen()
    new = events if args.all else [e for e in events if e.key not in seen]
    log.info("%d relevant events, %d new", len(events), len(new))

    linkedin = [(c["name"], f"https://www.linkedin.com/company/{c['linkedin']}/events/")
                for c in cfg.get("companies", []) if c.get("linkedin")]
    subject, text, html_body = emailer.build(new, linkedin, l_err + c_err)

    if args.dry_run:
        out = ROOT / "out"
        out.mkdir(exist_ok=True)
        (out / "digest.html").write_text(html_body)
        (out / "digest.txt").write_text(f"Subject: {subject}\n\n{text}")
        log.info("Dry run: wrote %s", out / "digest.html")
        return

    if new or cfg.get("email_when_empty", True):
        emailer.send(subject, text, html_body)
        log.info("Email sent: %s", subject)
    today = datetime.now(timezone.utc).date().isoformat()
    for e in new:
        seen.setdefault(e.key, today)
    save_seen(seen)


if __name__ == "__main__":
    main()
