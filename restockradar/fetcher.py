"""HTTP-Abrufe mit fairem Abstand pro Shop und Erkennung von Bot-Sperren."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
}
MIN_HOST_GAP = 3.0      # Sekunden Mindestabstand zwischen zwei Anfragen an denselben Shop
MAX_CHARS = 4_000_000   # sehr große Seiten werden abgeschnitten


@dataclass
class Page:
    url: str
    status: int
    headers: httpx.Headers
    text: str
    block: str | None = None    # Grund, falls der Shop uns aussperrt
    queue: bool = False         # Shop leitet in eine Warteschlange um (oft: Drop läuft)
    retry_after: int | None = None


class Fetcher:
    def __init__(self, min_gap: float = MIN_HOST_GAP, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            http2=transport is None, follow_redirects=True, timeout=20,
            headers=HEADERS, transport=transport)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self.min_gap = min_gap

    async def get(self, url: str, *, json: bool = False) -> Page:
        host = urlsplit(url).hostname or ""
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last.get(host, 0.0) + self.min_gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                headers = {"Accept": "application/json"} if json else None
                resp = await self._client.get(url, headers=headers)
            finally:
                self._last[host] = time.monotonic()
        page = Page(str(resp.url), resp.status_code, resp.headers, resp.text[:MAX_CHARS])
        page.queue = _is_queue(page)
        page.block = None if page.queue else detect_block(page)
        page.retry_after = _retry_after(resp.headers)
        return page

    async def post_json(self, url: str, payload: dict, **kwargs) -> httpx.Response:
        return await self._client.post(url, json=payload, **kwargs)

    async def raw(self, method: str, url: str, **kwargs) -> httpx.Response:
        return await self._client.request(method, url, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()


def _is_queue(page: Page) -> bool:
    host = urlsplit(page.url).hostname or ""
    return "queue-it" in host or host.startswith("queue.") or "/queue-it" in page.url.lower()


def _retry_after(headers: httpx.Headers) -> int | None:
    value = headers.get("retry-after", "")
    return int(value) if value.isdigit() else None


def detect_block(page: Page) -> str | None:
    """Erkennt Bot-Schutz-Seiten. Wir umgehen nichts, wir sagen nur ehrlich Bescheid."""
    head = page.text[:30000].lower()
    server = page.headers.get("server", "").lower()
    status = page.status
    if status == 429:
        return "Der Shop bremst gerade (zu viele Anfragen)"
    if page.headers.get("cf-mitigated") == "challenge" or (
            "cloudflare" in server and status in (403, 503)) or "cf-chl-" in head:
        return "Cloudflare-Bot-Schutz"
    if "_incapsula_resource" in head or "incapsula incident" in head:
        return "Imperva-Bot-Schutz"
    if "captcha-delivery.com" in head or ("x-datadome" in page.headers and status == 403):
        return "DataDome-Bot-Schutz"
    if "px-captcha" in head or "_pxcaptcha" in head:
        return "PerimeterX-Bot-Schutz"
    if status == 403 and ("akamai" in server or ("access denied" in head and "reference #" in head)):
        return "Akamai-Bot-Schutz"
    if status in (401, 403):
        return "Zugriff verweigert"
    if status == 503 and len(page.text) < 20000 and ("captcha" in head or "challenge" in head):
        return "Bot-Schutz"
    return None
