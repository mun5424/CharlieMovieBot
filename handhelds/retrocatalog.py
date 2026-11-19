from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

RETRO_BASE = "https://retrocatalog.com"
IMAGE_PATH = "/images/retro-handheld_front_"

# Use weserv.nl proxy to get proper Content-Type headers for Discord embeds
IMAGE_PROXY = "https://images.weserv.nl/?url=retrocatalog.com/images/retro-handheld_front_"

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)

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


def _simple_slugify(name: str) -> str:
    """Simple slug without brand stripping (fallback)."""
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")


@dataclass
class RetroHandheld:
    slug: str
    image_url: str


class RetroCatalogClient:
    """Client to check if images exist on retrocatalog.com."""

    def __init__(self, session: aiohttp.ClientSession, *, min_delay_s: float = 0.2):
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

    async def check_image_exists(self, slug: str) -> Optional[str]:
        """
        Check if an image exists for the given slug.
        Returns the proxied image URL if it exists (HTTP 200), None otherwise.
        Uses weserv.nl proxy for proper Content-Type headers (Discord compatibility).
        Checks against the proxy URL directly since retrocatalog returns 200 for errors.
        """
        await self._rate_limit()

        proxy_url = f"{IMAGE_PROXY}{slug}"
        headers = {"User-Agent": "CharlieMovieBot/1.0 (+retrocatalog resolver)"}

        try:
            async with self.session.head(
                proxy_url,
                headers=headers,
                allow_redirects=True,
                timeout=DEFAULT_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    return proxy_url
                return None
        except Exception as e:
            logger.debug("RetroCatalog image check failed for %s: %s", slug, e)
            return None


_SITEMAP_SLUG_RE = re.compile(r"retrocatalog\.com/retro-handhelds/([a-z0-9-]+)\s*<")
_IMAGE_SLUG_RE = re.compile(r"retro-handheld_front_([a-z0-9-]+)")


async def fetch_catalog_slugs(session: aiohttp.ClientSession) -> set[str]:
    """
    Collect known RetroCatalog slugs from the sitemap (device pages) and the
    listing page (image filenames). Newer devices use brand-prefixed slugs
    like "anbernic-rg-dds", which the name-guessing heuristic can't produce.
    """
    headers = {"User-Agent": "CharlieMovieBot/1.0 (+retrocatalog resolver)"}
    slugs: set[str] = set()
    for url, rx in (
        (f"{RETRO_BASE}/sitemap.xml", _SITEMAP_SLUG_RE),
        (f"{RETRO_BASE}/retro-handhelds", _IMAGE_SLUG_RE),
    ):
        try:
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status == 200:
                    slugs.update(rx.findall(await resp.text()))
        except Exception as e:
            logger.warning("RetroCatalog: failed to fetch %s: %s", url, e)
    logger.info("RetroCatalog: loaded %d catalog slugs", len(slugs))
    return slugs


def _squash(s: str, plus: str = "plus") -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("+", plus))


def match_catalog_slugs(name: str, brand: Optional[str], catalog: set[str]) -> list[str]:
    """
    Match a sheet device name (+ brand) to catalog slugs, best first.
    Tries exact (alphanumeric-only) matches with/without a brand prefix, then
    the shortest catalog slug that starts with the key (e.g. "gpd win mini"
    -> "gpd-win-mini-2025").
    """
    if not catalog:
        return []

    index: dict[str, str] = {}
    for s in sorted(catalog, key=len):
        index.setdefault(_squash(s), s)

    # Name variants: as-is, without "(...)", and each "/"-separated alias
    base_names = [name, re.sub(r"\([^)]*\)", " ", name)]
    for part in re.sub(r"\([^)]*\)", " ", name).split("/"):
        base_names.append(part)

    brands = [""]
    for b in (brand or "").split("/"):
        b = b.strip()
        if b:
            brands += [b, b.split()[0]]

    keys: list[str] = []
    for n in base_names:
        if not n.strip():
            continue
        for b in brands:
            for plus in ("plus", ""):
                k = _squash(f"{b} {n}", plus)
                if k and k not in keys:
                    keys.append(k)

    out: list[str] = []
    for k in keys:
        if k in index and index[k] not in out:
            out.append(index[k])
    if out:
        return out

    for k in keys:
        if len(k) < 6:
            continue
        for sq, s in index.items():  # shortest first
            if sq.startswith(k) and s not in out:
                out.append(s)
                break
    return out[:3]


async def resolve_retrocatalog(
    name: str,
    session: aiohttp.ClientSession,
    *,
    brand: Optional[str] = None,
    catalog: Optional[set[str]] = None,
) -> Optional[RetroHandheld]:
    """
    Try to find an image URL for a handheld on retrocatalog.com.
    Uses catalog slug matching first, then falls back to guessing slugs.
    Every candidate is verified with a HEAD request.
    """
    client = RetroCatalogClient(session, min_delay_s=0.2)

    base = slugify_name(name)
    full = _simple_slugify(name)

    candidates = match_catalog_slugs(name, brand, catalog or set()) + [
        base,   # "rg-35xx-plus" (brand stripped, hyphenated)
        full,   # "anbernic-rg35xx-plus" (full name, simple slug)
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
        image_url = await client.check_image_exists(slug)
        if image_url:
            logger.debug("RetroCatalog: found image for '%s' at '%s'", name, image_url)
            return RetroHandheld(slug=slug, image_url=image_url)

    logger.debug("RetroCatalog: no image for '%s' (tried: %s)", name, list(tried))
    return None
