"""HTTP fetching: plain requests first, headless Chromium when a page needs JS."""
from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

_session = requests.Session()
_session.headers.update(HEADERS)


def get(url: str, *, as_json: bool = False, timeout: int = 25):
    r = _session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json() if as_json else r.text


class Browser:
    """Lazy Playwright wrapper. If Playwright isn't installed, `render` returns None."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self.available = True

    def _ensure(self) -> bool:
        if self._browser or not self.available:
            return self.available
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(args=["--no-sandbox"])
        except Exception as e:  # noqa: BLE001
            log.warning("Headless browser unavailable (%s); JS-heavy pages will be skipped", e)
            self.available = False
        return self.available

    def render(self, url: str, json_hosts: tuple[str, ...] = ()) -> tuple[str, list] | None:
        """Return (final_html, [json bodies from matching XHR responses])."""
        if not self._ensure():
            return None
        payloads: list = []
        page = self._browser.new_page(user_agent=UA)

        def on_response(resp):
            if json_hosts and any(h in resp.url for h in json_hosts):
                try:
                    if "json" in (resp.headers.get("content-type") or ""):
                        payloads.append(resp.json())
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            # Scroll a bit to trigger lazy-loaded lists.
            for _ in range(3):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(800)
            return page.content(), payloads
        finally:
            page.close()

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass


def extract_json_scripts(html: str) -> list:
    """Pull __NEXT_DATA__ and JSON-LD blobs out of a page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for s in soup.find_all("script"):
        t = (s.get("type") or "").lower()
        if s.get("id") == "__NEXT_DATA__" or t in ("application/ld+json", "application/json"):
            try:
                out.append(json.loads(s.string or ""))
            except Exception:  # noqa: BLE001
                continue
    return out
