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

# Common brand prefixes to strip from device names
BRAND_PREFIXES = [
    "anbernic", "retroid", "powkiddy", "miyoo", "ayn", "ayaneo",
    "gpd", "onexplayer", "steam", "valve", "nintendo", "sony",
    "sega", "atari", "trimui", "rgb10", "gameforce", "odroid",
    "clockwork", "hardkernel", "abernic",  # common misspelling
]


def slugify_name(name: str) -> str:
    """
    Convert device name to RetroCatalog-style slug.

    Examples:
        "Anbernic RG35XX Plus" -> "rg-35xx-plus"
        "Retroid Pocket 4 Pro" -> "pocket-4-pro"
        "Miyoo Mini Plus" -> "mini-plus"
    """
    s = name.strip().lower()

    # Remove brand prefix if present
    for brand in BRAND_PREFIXES:
        if s.startswith(brand + " "):
            s = s[len(brand):].strip()
            break
        # Also try without space (e.g., "AnbernicRG35XX")
        if s.startswith(brand) and len(s) > len(brand):
            rest = s[len(brand):]
            if rest[0].isalnum():
                s = rest.strip()
                break

    # Insert hyphen between letters and digits: "rg35xx" -> "rg-35xx"
    # But NOT between digits and letters: "35xx" stays "35xx" (retrocatalog style)
    s = re.sub(r"([a-z])(\d)", r"\1-\2", s)

    # Remove non-word chars except spaces/hyphens, collapse whitespace to hyphens
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    # Collapse multiple hyphens
    s = re.sub(r"-+", "-", s)

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


def _simple_slugify(name: str) -> str:
    """Simple slug without brand stripping (fallback)."""
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")


async def resolve_retrocatalog(name: str, session: aiohttp.ClientSession) -> Optional[RetroHandheld]:
    """
    Best-effort resolver: try multiple slug variants.
    """
    client = RetroCatalogClient(session, min_delay_s=1.0)

    base = slugify_name(name)
    full = _simple_slugify(name)

    candidates = [
        base,                              # "rg-35xx-plus" (brand stripped, hyphenated)
        full,                              # "anbernic-rg35xx-plus" (full name, simple slug)
    ]

    # Also try without trailing modifiers like "plus", "pro", "h", "sp"
    for suffix in ["-plus", "-pro", "-h", "-sp", "-s"]:
        if base.endswith(suffix):
            candidates.append(base[:-len(suffix)])

    tried = set()
    for slug in candidates:
        if not slug or slug in tried:
            continue
        tried.add(slug)

        logger.debug("RetroCatalog: trying slug '%s' for '%s'", slug, name)
        hit = await client.fetch_handheld_page(slug)
        if hit:
            logger.debug("RetroCatalog: found '%s' at slug '%s'", name, slug)
            return hit

    logger.debug("RetroCatalog: no match for '%s' (tried: %s)", name, list(tried))
    return None