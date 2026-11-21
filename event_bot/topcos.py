"""Match events to the top tech/AI companies (Google, Amazon, Meta, ...)."""
from __future__ import annotations

import re

from .models import Event


def _pattern(aliases: list[str]) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(a.lower()) for a in aliases) + r")(?![a-z0-9])")


def match(ev: Event, companies: list[dict]) -> list[str]:
    """Names of top companies mentioned in the event's title, organizer or description."""
    text = " ".join((ev.title, ev.extra.get("organizer", ""), ev.description)).lower()
    return [c["name"] for c in companies if _pattern([c["name"]] + c.get("aliases", [])).search(text)]
