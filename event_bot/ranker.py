"""Rank hackathons by how useful they are for landing an internship.

Uses Claude when ANTHROPIC_API_KEY is set; otherwise (or if the call fails)
a transparent point-based score.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Event

log = logging.getLogger(__name__)


@dataclass
class Ranked:
    event: Event
    score: int
    reason: str


def _num(s: str) -> int:
    m = re.search(r"\$?\s*([\d,]{3,})", s or "")
    return int(m.group(1).replace(",", "")) if m else 0


def heuristic(ev: Event, rcfg: dict) -> Ranked:
    text = f"{ev.title} {ev.description} {ev.extra.get('organizer', '')}".lower()
    score, why = 0, []

    companies = [c for c in rcfg.get("target_companies", []) if re.search(
        r"(?<![a-z])" + re.escape(c.lower()) + r"(?![a-z])", text)]
    if companies:
        score += min(3 * len(companies), 9)
        why.append("sponsors/hosts: " + ", ".join(companies[:3]))
    if re.search(r"recruit|hiring|internship|career|resume|job fair|talent|interview", text):
        score += 4
        why.append("explicit recruiting")
    if re.search(r"student|university|college|campus|columbia|nyu|cornell tech|princeton|rutgers|stevens|undergrad", text) \
            or ev.source == "MLH":
        score += 2
        why.append("student-focused")
    if ev.source == "MLH":
        score += 2
        why.append("MLH (sponsor recruiters on site)")
    regs = int(ev.extra.get("registrations") or 0)
    if regs >= 500:
        score += 3
        why.append(f"{regs} registered")
    elif regs >= 150:
        score += 2
        why.append(f"{regs} registered")
    prize = _num(ev.extra.get("prize", "")) or _num(ev.description)
    if prize >= 10000:
        score += 2
        why.append(f"${prize:,} prizes")
    elif prize >= 2000:
        score += 1
    if re.search(r"\b(24|36|48)[- ]?h|overnight|weekend|two[- ]day", text):
        score += 1
        why.append("multi-day")
    if re.search(r"\bai\b|llm|agent|genai|machine learning", text):
        score += 1
    if ev.start:
        start = ev.start if ev.start.tzinfo else ev.start.replace(tzinfo=timezone.utc)
        days = (start - datetime.now(timezone.utc)).days
        if days <= 30:
            score += 1
    else:
        score -= 1
    return Ranked(ev, score, "; ".join(why) or "in-person NYC/NJ hackathon")


SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranking"],
    "additionalProperties": False,
}


def _claude_rank(cands: list[Ranked], rcfg: dict, top_n: int) -> list[Ranked] | None:
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    try:
        import anthropic
    except ImportError:
        return None

    items = [{
        "id": str(i), "title": r.event.title, "date": r.event.start.date().isoformat() if r.event.start else None,
        "location": r.event.location, "source": r.event.source, "url": r.event.url,
        "details": r.event.description[:400],
        "registrations": r.event.extra.get("registrations"), "prize": r.event.extra.get("prize"),
    } for i, r in enumerate(cands)]
    prompt = (
        f"About me: {rcfg.get('profile', 'A student looking for a tech internship.')}\n\n"
        "Below are upcoming in-person hackathons in NYC / New Jersey. Rank them by networking ROI for "
        "getting an internship: recruiters or engineers from strong employers attending or sponsoring, "
        "explicit recruiting / career components, a student-friendly audience, size and prestige, and "
        "reasonable cost and time commitment. Skip anything that isn't a single real hackathon "
        "(directories, listicles, past events, online-only).\n\n"
        f"Return the best {top_n} at most, best first. score is 1-100. reason is at most 20 words "
        "and names the concrete networking upside.\n\n"
        f"Hackathons (JSON):\n{json.dumps(items, ensure_ascii=False)}"
    )
    client = anthropic.Anthropic()
    kwargs = dict(
        model=rcfg.get("model", "claude-opus-5-5"),
        max_tokens=16000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        try:
            # Server-side fallback if the request is ever declined by a safety classifier.
            resp = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"}, **kwargs)
        except anthropic.BadRequestError:
            resp = client.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            log.warning("Claude declined the ranking request; using heuristic ranking")
            return None
        text = next(b.text for b in resp.content if b.type == "text")
        out = []
        for row in json.loads(text)["ranking"]:
            idx = int(row["id"])
            if 0 <= idx < len(cands):
                out.append(Ranked(cands[idx].event, int(row["score"]), row["reason"].strip()))
        log.info("Ranked %d hackathons with Claude", len(cands))
        return out or None
    except anthropic.APIStatusError as e:
        log.warning("Claude ranking failed (%s %s); using heuristic ranking", e.status_code, e.message)
    except anthropic.APIConnectionError as e:
        log.warning("Claude ranking failed (network: %s); using heuristic ranking", e)
    except (StopIteration, ValueError, KeyError) as e:
        log.warning("Claude ranking returned unexpected output (%s); using heuristic ranking", e)
    return None


def rank(events: list[Event], rcfg: dict, top_n: int) -> tuple[list[Ranked], str]:
    scored = sorted((heuristic(e, rcfg) for e in events), key=lambda r: r.score, reverse=True)
    llm = _claude_rank(scored[:40], rcfg, top_n)
    if llm:
        return llm[:top_n], "Claude"
    return scored[:top_n], "heuristic"
