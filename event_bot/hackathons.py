"""Google search fallback: "<company> hackathon New York City"."""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote


from . import company
from .fetch import get
from .models import Event

log = logging.getLogger(__name__)

# Google results that are listicles / directories rather than one event.
LISTING = re.compile(r"\b(top \d+|best|list of|upcoming hackathons|hackathons (in|near)|"
                     r"find hackathons|all hackathons|hackathon calendar)\b", re.I)



def _serpapi(query: str, engine: str, key: str) -> dict:
    return get(f"https://serpapi.com/search.json?engine={engine}&q={quote(query)}"
               f"&hl=en&gl=us&api_key={key}", as_json=True, timeout=40)


# ------------------------------------------------- per-company fallback ---

def company_leads(companies: list[dict], cfg: dict) -> tuple[list[Event], list[str]]:
    """Google "<Company> hackathon New York City" for each top company.

    Uses Google Events results first (structured date + address) and falls back
    to regular web results for a company when Events returns nothing."""
    from .topcos import match

    serp = os.environ.get("SERPAPI_KEY", "").strip()
    cse_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    cse_id = os.environ.get("GOOGLE_CSE_ID", "").strip()
    if not (serp or (cse_key and cse_id)):
        return [], ["Google fallback search is off: add a SERPAPI_KEY secret (see README)"]
    template = cfg.get("query", "{company} hackathon New York City")
    leads: dict[str, Event] = {}
    errors: list[str] = []
    for c in companies:
        q = template.format(company=c["name"])
        got: list[Event] = []
        try:
            if serp:
                for r in _serpapi(q, "google_events", serp).get("events_results", []):
                    date = r.get("date") or {}
                    got.append(Event(
                        title=r.get("title", "").strip(), url=r.get("link", ""), source="Google",
                        start=company.parse_date(" ".join(filter(None, [date.get("start_date"), date.get("when")]))),
                        location=", ".join(r.get("address") or []),
                        description=(r.get("description") or "")[:800],
                    ))
            if not got:
                if serp:
                    items = [{"title": r.get("title", ""), "link": r.get("link", ""), "snippet": r.get("snippet", "")}
                             for r in _serpapi(q, "google", serp).get("organic_results", [])]
                else:
                    items = get(f"https://www.googleapis.com/customsearch/v1?key={cse_key}&cx={cse_id}"
                                f"&q={quote(q)}&num=10", as_json=True).get("items", [])
                for r in items:
                    title, snippet = r.get("title", ""), r.get("snippet", "")
                    if LISTING.search(title):
                        continue
                    got.append(Event(title=title.strip(), url=r.get("link", ""), source="Google",
                                     start=company.parse_date(snippet), description=snippet[:800]))
        except Exception as e:  # noqa: BLE001
            errors.append(f"Google '{q}': {e}")
        for ev in got:
            text = f"{ev.title} {ev.description}".lower()
            # A lead must be a hackathon and actually involve this company.
            if not ev.title or not ev.url or "hack" not in text or c["name"] not in match(ev, [c]):
                continue
            ev.extra.update({"force_kind": "hackathon", "lead_for": c["name"], "query": q})
            leads.setdefault(ev.key, ev)
        log.info("Google %-40r -> %d leads", q, sum(1 for e in leads.values() if e.extra.get("query") == q))
    return list(leads.values()), errors
