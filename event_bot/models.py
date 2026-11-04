from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    title: str
    url: str
    source: str  # e.g. "Luma (sf)" or "MongoDB"
    start: datetime | None = None
    location: str = ""
    in_person: bool | None = None  # None = unknown
    description: str = ""
    kind: str = ""  # hackathon | networking | conference | event
    tag: str = ""  # two-word summary
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable id used to avoid emailing the same event twice."""
        norm = self.url.split("?")[0].rstrip("/").lower()
        return hashlib.sha1(norm.encode()).hexdigest()[:16]
