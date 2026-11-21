"""Match events to the top tech/AI companies (Google, Amazon, Meta, ...).

A company counts when it is in the event's title or host/organizer, or when the
description says it is actually involved ("hosted by Google", "sponsored by AWS",
"at Google's NYC office", "speakers from OpenAI", "Anthropic x ..."). A passing
mention such as "attendees from Google, Meta and more" does not count.
"""
from __future__ import annotations

import re

from .models import Event

# Phrases that, shortly before the company name (same sentence), mean real involvement.
BEFORE = (r"(hosted|co-hosted|sponsored|presented|powered|organi[sz]ed|supported|judged|run|"
          r"brought to you|backed) by|in partnership with|partnered with|in collaboration with|"
          r"co-hosted with|hosted at|held at|(speakers?|engineers?|panelists?|recruiters?|researchers?|"
          r"judges?|mentors?|leaders?|pms?|product managers?|team members?) (from|at)|"
          r"thanks to( our)?( sponsors?)?|our (sponsors?|partners?|hosts?)( include)?|sponsors?:|partners?:")
# Words right after the company name that mean real involvement.
AFTER = (r"('s|’s)?\s+(nyc |new york )?(office|offices|hq|headquarters|campus|engineers?|team|teams|"
         r"speakers?|recruiters?|researchers?|devrel|developer relations|is hosting|hosts|will host|"
         r"sponsors|is sponsoring|presents|for startups)\b")


def _alias_re(aliases: list[str]) -> str:
    return "(" + "|".join(re.escape(a.lower()) for a in sorted(aliases, key=len, reverse=True)) + ")"


def involvement(company: dict, ev: Event) -> str | None:
    """How the company is involved (for display), or None."""
    names = [company["name"]] + company.get("aliases", [])
    alias = _alias_re(names)
    word = rf"(?<![a-z0-9]){alias}(?![a-z0-9])"
    head = f"{ev.title} {ev.extra.get('organizer', '')}".lower()
    if re.search(word, head):
        return "in title / host"
    desc = ev.description.lower()
    if re.search(rf"\b({BEFORE})\b[^.!?\n]{{0,80}}?{word}", desc):
        return "host / sponsor / speakers"
    if re.search(word + AFTER, desc):
        return "office / team / sponsor"
    if re.search(rf"(?<![a-z0-9])x\s+{alias}(?![a-z0-9])|{word}\s+(x|×)\s", desc):
        return "partner event"
    return None


def match(ev: Event, companies: list[dict]) -> list[str]:
    """Names of top companies actually involved in the event."""
    return [c["name"] for c in companies if involvement(c, ev)]
