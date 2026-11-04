"""Decide what kind of event something is and give it a two-word tag."""
from __future__ import annotations

import re

from .models import Event

# Topic words, checked in order; first match wins.
TOPICS: list[tuple[str, str]] = [
    (r"\bagent(s|ic)?\b", "Agents"),
    (r"\bgen ?ai\b|generative", "GenAI"),
    (r"\bllms?\b|language model", "LLM"),
    (r"\brag\b|graphrag|retrieval", "RAG"),
    (r"knowledge graph|\bgraph", "Graph"),
    (r"\bvector", "Vector"),
    (r"\bai\b|artificial intelligence|machine learning|\bml\b", "AI"),
    (r"\bdata\b|analytics|database|\bsql\b", "Data"),
    (r"cloud|serverless|kubernetes|devops", "Cloud"),
    (r"security|cyber", "Security"),
    (r"web3|crypto|blockchain", "Web3"),
    (r"robot", "Robotics"),
    (r"health|bio", "Health"),
    (r"climate", "Climate"),
    (r"fintech|finance", "Fintech"),
    (r"founder|startup|vc\b|investor", "Founder"),
    (r"product|design|\bpm\b", "Product"),
    (r"women|female", "Women"),
    (r"developer|\bdev\b|engineer|coding|builder", "Builder"),
]

KIND_LABEL = {
    "hackathon": "Hackathon",
    "networking": "Mixer",
    "conference": "Conference",
    "event": "Event",
}

# More specific networking labels.
NETWORKING_LABELS = [
    (r"happy hour", "Happy-hour"),
    (r"demo (day|night)", "Demo-night"),
    (r"meet-?up", "Meetup"),
    (r"fireside", "Fireside"),
    (r"summit", "Summit"),
]


def _matches(text: str, words: list[str]) -> bool:
    return any(re.search(r"(?<![a-z])" + re.escape(w.lower()) + r"(?![a-z])", text) for w in words)


def detect_kind(ev: Event, keywords: dict[str, list[str]]) -> str:
    text = f"{ev.title} {ev.description}".lower()
    title = ev.title.lower()
    # Title wins over description so "Workshop (followed by networking)" stays sane.
    for kind in ("hackathon", "networking", "conference"):
        if _matches(title, keywords.get(kind, [])):
            return kind
    for kind in ("hackathon", "networking", "conference"):
        if _matches(text, keywords.get(kind, [])):
            return kind
    return ""


def two_word_tag(ev: Event) -> str:
    text = f"{ev.title} {ev.description}".lower()
    topic = next((label for pat, label in TOPICS if re.search(pat, text)), None)
    label = KIND_LABEL.get(ev.kind or "event", "Event")
    if ev.kind == "networking":
        label = next((lbl for pat, lbl in NETWORKING_LABELS if re.search(pat, text)), label)
    if not topic:
        topic = ev.source.split(" ")[0] if not ev.source.startswith("Luma") else "Tech"
    return f"{topic} {label}"
