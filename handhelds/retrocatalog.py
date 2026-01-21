from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

RETRO_BASE = "https://retrocatalog.com"
HH_PATH = "/retro-handhelds/"

# Prefer og:image if present; otherwise fall back to any big product image patterns
OG_IMAGE_RE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.IGNORECASE)
CANONICAL_RE = re.compile(r'<link\s+rel="canonical"\s+href="([^"]+)"', re.IGNORECASE)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=8)


def slugify_name(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")


@dataclass
class RetroHandheld:
    url: str
    slug: str
    og_image: Optional[str] = None
    canonical_url: Optional[str] = None
    html_len: int = 0


class RetroCatalogClient:
    def __init__(self, session: aiohttp.ClientSession, *, min_delay_s: float = 1.0):
        self.session = session
        self.min_delay_s = min_delay_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def _rate_limit(self):
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = (self._last + self.min_delay_s) - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = loop.time()

    async def fetch_handheld_page(self, slug: str) -> Optional[RetroHandheld]:
        await self._rate_limit()

        url = f"{RETRO_BASE}{HH_PATH}{slug}"
        headers = {"User-Agent": "CharlieMovieBot/1.0 (+retrocatalog resolver)"}

        try:
            async with self.session.get(url, headers=headers, allow_redirects=True, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

            og = None
            m = OG_IMAGE_RE.search(html)
            if m:
                og = m.group(1).strip()

            canonical = None
            m2 = CANONICAL_RE.search(html)
            if m2:
                canonical = m2.group(1).strip()

            return RetroHandheld(
                url=url,
                slug=slug,
                og_image=og,
                canonical_url=canonical,
                html_len=len(html),
            )
        except Exception as e:
            logger.warning("RetroCatalog fetch failed for %s: %s", url, e)
            return None


async def resolve_retrocatalog(name: str, session: aiohttp.ClientSession) -> Optional[RetroHandheld]:
    """
    Best-effort resolver: try a few slug variants.
    """
    client = RetroCatalogClient(session, min_delay_s=1.0)

    base = slugify_name(name)
    candidates = [
        base,
        base.replace("plus", "plus"),  # placeholder if you add more transforms
    ]

    tried = set()
    for slug in candidates:
        if not slug or slug in tried:
            continue
        tried.add(slug)

        hit = await client.fetch_handheld_page(slug)
        if hit:
            return hit

    return None