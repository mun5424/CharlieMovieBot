"""
Jikan API client for anime search
https://jikan.moe/ - Unofficial MyAnimeList API
"""

import aiohttp
import asyncio
import logging
import time
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

# Jikan API configuration
JIKAN_BASE_URL = "https://api.jikan.moe/v4"
JIKAN_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3)
JIKAN_AUTOCOMPLETE_TIMEOUT = aiohttp.ClientTimeout(total=2.8, connect=1.5)  # Discord has 3s limit

# Rate limiting: 3 requests per second, 60 per minute
_last_request_time = 0
_request_lock = asyncio.Lock()
MIN_REQUEST_INTERVAL = 0.35  # ~3 requests per second

# Shared session
_session: Optional[aiohttp.ClientSession] = None

# Search cache with TTL (query -> (results, timestamp))
_search_cache: Dict[str, tuple] = {}
CACHE_TTL = 60  # Cache results for 60 seconds
MAX_CACHE_SIZE = 100  # Maximum cached queries


async def get_session() -> aiohttp.ClientSession:
    """Get or create the shared aiohttp session."""
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=5, keepalive_timeout=30)
        _session = aiohttp.ClientSession(
            timeout=JIKAN_TIMEOUT,
            connector=connector,
            headers={"User-Agent": "CharlieMovieBot/1.0"}
        )
    return _session


async def close_session():
    """Close the shared session."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
        logger.info("Closed Jikan API session")


async def _rate_limited_request(url: str, params: dict = None) -> Optional[dict]:
    """Make a rate-limited request to Jikan API."""
    global _last_request_time

    async with _request_lock:
        # Enforce rate limiting
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)

        _last_request_time = time.time()

    try:
        session = await get_session()
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 429:
                logger.warning("Jikan API rate limited, waiting...")
                await asyncio.sleep(1)
                return None
            else:
                logger.error(f"Jikan API error: {resp.status}")
                return None
    except asyncio.TimeoutError:
        logger.error("Jikan API timeout")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"Jikan API client error: {e}")
        return None


def _clean_cache():
    """Remove expired entries from cache."""
    global _search_cache
    now = time.time()
    expired = [k for k, (_, ts) in _search_cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        del _search_cache[k]

    # If still too large, remove oldest entries
    if len(_search_cache) > MAX_CACHE_SIZE:
        sorted_keys = sorted(_search_cache.keys(), key=lambda k: _search_cache[k][1])
        for k in sorted_keys[:len(_search_cache) - MAX_CACHE_SIZE]:
            del _search_cache[k]


async def search_anime(query: str, limit: int = 10) -> List[Dict]:
    """
    Search for anime by title with caching.

    Returns list of anime with:
    - mal_id: MyAnimeList ID
    - title: Japanese/Romaji title
    - title_english: English title (may be None)
    - episodes: Number of episodes
    - status: Airing status
    - score: MAL score
    - image_url: Poster image URL
    - synopsis: Description
    - year: Release year
    """
    # Normalize query for cache key
    cache_key = f"{query.lower().strip()}:{limit}"

    # Check cache first
    if cache_key in _search_cache:
        results, timestamp = _search_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return results

    # Clean cache periodically
    _clean_cache()

    url = f"{JIKAN_BASE_URL}/anime"
    params = {"q": query, "limit": limit, "sfw": "true"}

    data = await _rate_limited_request(url, params)
    if not data or "data" not in data:
        return []

    results = []
    for item in data["data"]:
        # Get the best available title
        title = item.get("title_english") or item.get("title", "Unknown")

        # Get image URL
        images = item.get("images", {})
        jpg_images = images.get("jpg", {})
        image_url = jpg_images.get("large_image_url") or jpg_images.get("image_url", "")

        results.append({
            "mal_id": item.get("mal_id"),
            "title": title,
            "title_japanese": item.get("title", ""),
            "episodes": item.get("episodes"),
            "status": item.get("status", "Unknown"),
            "score": item.get("score"),
            "image_url": image_url,
            "synopsis": item.get("synopsis", ""),
            "year": item.get("year"),
            "season": item.get("season"),
            "type": item.get("type", "TV"),  # TV, Movie, OVA, etc.
        })

    # Cache the results
    _search_cache[cache_key] = (results, time.time())

    return results


async def search_anime_async(query: str) -> Optional[Dict]:
    """
    Search for a single anime (first result).
    Used for commands like /anime_add.
    """
    results = await search_anime(query, limit=1)
    return results[0] if results else None


async def search_anime_autocomplete(query: str, limit: int = 10) -> List[Dict]:
    """
    Fast anime search for autocomplete (shorter timeout, cache-first).
    Returns quickly to meet Discord's 3s autocomplete deadline.
    """
    if len(query) < 2:
        return []

    # Normalize query for cache key
    cache_key = f"{query.lower().strip()}:{limit}"

    # Check cache first - return immediately if cached
    if cache_key in _search_cache:
        results, timestamp = _search_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return results

    # Try to fetch with short timeout
    url = f"{JIKAN_BASE_URL}/anime"
    params = {"q": query, "limit": limit, "sfw": "true"}

    try:
        session = await get_session()
        async with session.get(url, params=params, timeout=JIKAN_AUTOCOMPLETE_TIMEOUT) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()

        if not data or "data" not in data:
            return []

        results = []
        for item in data["data"]:
            title = item.get("title_english") or item.get("title", "Unknown")
            if not title:
                continue

            results.append({
                "mal_id": item.get("mal_id"),
                "title": title,
                "title_japanese": item.get("title", ""),
                "episodes": item.get("episodes"),
                "status": item.get("status", "Unknown"),
                "score": item.get("score"),
                "year": item.get("year"),
                "type": item.get("type", "TV"),
            })

        # Cache the results
        _search_cache[cache_key] = (results, time.time())
        return results

    except asyncio.TimeoutError:
        logger.debug("Jikan autocomplete timed out (expected under load)")
        return []
    except Exception as e:
        logger.debug(f"Jikan autocomplete error: {e}")
        return []


async def get_anime_by_id(mal_id: int) -> Optional[Dict]:
    """Get anime details by MAL ID."""
    url = f"{JIKAN_BASE_URL}/anime/{mal_id}"

    data = await _rate_limited_request(url)
    if not data or "data" not in data:
        return None

    item = data["data"]
    images = item.get("images", {})
    jpg_images = images.get("jpg", {})
    image_url = jpg_images.get("large_image_url") or jpg_images.get("image_url", "")

    return {
        "mal_id": item.get("mal_id"),
        "title": item.get("title_english") or item.get("title", "Unknown"),
        "title_japanese": item.get("title", ""),
        "episodes": item.get("episodes"),
        "status": item.get("status", "Unknown"),
        "score": item.get("score"),
        "image_url": image_url,
        "synopsis": item.get("synopsis", ""),
        "year": item.get("year"),
        "season": item.get("season"),
        "type": item.get("type", "TV"),
    }


async def warmup_session():
    """Pre-warm the session to reduce first-request latency."""
    try:
        session = await get_session()
        # Make a lightweight request to establish connection
        async with session.get(f"{JIKAN_BASE_URL}/anime/1", timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                logger.info("Jikan API session pre-warmed")
            else:
                logger.warning(f"Jikan API warmup got status {resp.status}")
    except Exception as e:
        logger.warning(f"Failed to pre-warm Jikan session: {e}")
