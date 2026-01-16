"""
Jikan API client for anime search
https://jikan.moe/ - Unofficial MyAnimeList API
"""

import aiohttp
import asyncio
import logging
import time
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Jikan API configuration
JIKAN_BASE_URL = "https://api.jikan.moe/v4"
JIKAN_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3)
JIKAN_AUTOCOMPLETE_TIMEOUT = aiohttp.ClientTimeout(total=2.8, connect=1.5)  # Discord has ~3s limit

# Rate limiting: ~3 requests per second
_last_request_time = 0.0
_request_lock = asyncio.Lock()
MIN_REQUEST_INTERVAL = 0.35  # ~3 req/sec

# Shared session
_session: Optional[aiohttp.ClientSession] = None

# Search cache with TTL (key -> (payload, timestamp))
_search_cache: Dict[str, Tuple[Any, float]] = {}
CACHE_TTL = 120  # seconds
MAX_CACHE_SIZE = 200


# ---------------- Normalization ----------------

def _safe_int_year(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        s = str(value)
        if len(s) >= 4 and s[:4].isdigit():
            return int(s[:4])
    except Exception:
        pass
    return None


def normalize_anime(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a raw Jikan v4 anime item into the schema expected by commands/db.

    Returns dict with keys:
      mal_id, title, title_japanese, episodes, status, score, image_url, synopsis, year, season, type
    """
    if not item or not isinstance(item, dict):
        return {}

    mal_id = item.get("mal_id")
    # Prefer English for display; fallback to default title.
    title = item.get("title_english") or item.get("title") or "Unknown"
    title_jp = item.get("title") or ""

    episodes = item.get("episodes")
    if episodes in (0, "0", ""):
        episodes = None

    status = item.get("status") or "Unknown"
    score = item.get("score")

    images = item.get("images") or {}
    jpg = images.get("jpg") or {}
    webp = images.get("webp") or {}
    image_url = (
        jpg.get("large_image_url")
        or jpg.get("image_url")
        or webp.get("large_image_url")
        or webp.get("image_url")
        or item.get("image_url")  # allow already-flattened inputs
        or ""
    )

    synopsis = item.get("synopsis") or ""

    # Year: Jikan sometimes supplies `year`; otherwise derive from `aired.from`.
    year = _safe_int_year(item.get("year"))
    if not year:
        aired = item.get("aired") or {}
        year = _safe_int_year(aired.get("from"))

    season = item.get("season")
    anime_type = item.get("type") or "TV"

    return {
        "mal_id": mal_id,
        "title": title,
        "title_japanese": title_jp,
        "episodes": episodes,
        "status": status,
        "score": score,
        "image_url": image_url,
        "synopsis": synopsis,
        "year": year,
        "season": season,
        "type": anime_type,
    }


# ---------------- Session management ----------------

async def get_session() -> aiohttp.ClientSession:
    """Get or create the shared aiohttp session."""
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=5, keepalive_timeout=30)
        _session = aiohttp.ClientSession(
            timeout=JIKAN_TIMEOUT,
            connector=connector,
            headers={"User-Agent": "CharlieMovieBot/1.0"},
        )
    return _session


async def close_session():
    """Close the shared session."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
        logger.info("Closed Jikan API session")


# ---------------- Cache helpers ----------------

def _clean_cache():
    """Remove expired entries from cache and cap size."""
    global _search_cache
    now = time.time()

    expired = [k for k, (_, ts) in _search_cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        del _search_cache[k]

    if len(_search_cache) > MAX_CACHE_SIZE:
        # remove oldest
        sorted_keys = sorted(_search_cache.keys(), key=lambda k: _search_cache[k][1])
        for k in sorted_keys[: len(_search_cache) - MAX_CACHE_SIZE]:
            del _search_cache[k]


def _cache_get(key: str) -> Optional[Any]:
    val = _search_cache.get(key)
    if not val:
        return None
    payload, ts = val
    if time.time() - ts < CACHE_TTL:
        return payload
    # expired
    _search_cache.pop(key, None)
    return None


def _cache_set(key: str, payload: Any):
    _search_cache[key] = (payload, time.time())
    _clean_cache()


# ---------------- HTTP helpers ----------------

async def _enforce_rate_limit():
    global _last_request_time
    async with _request_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()


async def _get_json(url: str, params: dict = None, timeout: aiohttp.ClientTimeout = None) -> Optional[dict]:
    """
    Make a request (rate-limited) and return JSON.
    Handles basic 429 backoff with Retry-After.
    """
    await _enforce_rate_limit()

    try:
        session = await get_session()
        async with session.get(url, params=params, timeout=timeout) as resp:
            if resp.status == 200:
                return await resp.json()

            if resp.status == 429:
                retry_after = resp.headers.get("Retry-After")
                delay = 1.0
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except Exception:
                        pass
                logger.warning(f"Jikan API 429 rate limited. retry_after={delay}s")
                await asyncio.sleep(min(delay, 3.0))
                return None

            # Sometimes Jikan returns JSON error bodies; we don't need to parse them here.
            logger.warning(f"Jikan API error status={resp.status} url={url}")
            return None

    except asyncio.TimeoutError:
        logger.debug(f"Jikan API timeout url={url}")
        return None
    except aiohttp.ClientError as e:
        logger.debug(f"Jikan API client error: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Jikan API unexpected error: {type(e).__name__}: {e}")
        return None


# ---------------- Public API ----------------

async def search_anime(query: str, limit: int = 10) -> List[Dict]:
    """
    Search for anime by title with caching.
    Returns normalized anime dicts.
    """
    q = (query or "").strip()
    if not q:
        return []

    cache_key = f"search:{q.lower()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = f"{JIKAN_BASE_URL}/anime"
    params = {"q": q, "limit": limit, "sfw": "true"}

    data = await _get_json(url, params=params)
    items = (data or {}).get("data") or []
    results: List[Dict] = []

    for item in items:
        n = normalize_anime(item)
        if n.get("mal_id") and n.get("title"):
            results.append(n)

    _cache_set(cache_key, results)
    return results


async def search_anime_async(query: str) -> Optional[Dict]:
    """Search for a single anime (first result)."""
    results = await search_anime(query, limit=1)
    return results[0] if results else None


async def search_anime_autocomplete(query: str, limit: int = 10) -> List[Dict]:
    """
    Fast anime search for autocomplete (short timeout, cache-first).
    Still rate-limited to avoid slamming Jikan during typing.
    Returns normalized anime dicts (minimal fields OK).
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []

    cache_key = f"ac:{q.lower()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = f"{JIKAN_BASE_URL}/anime"
    params = {"q": q, "limit": limit, "sfw": "true"}

    data = await _get_json(url, params=params, timeout=JIKAN_AUTOCOMPLETE_TIMEOUT)
    items = (data or {}).get("data") or []
    results: List[Dict] = []

    for item in items:
        n = normalize_anime(item)
        # autocomplete doesn’t need synopsis, but we keep schema consistent
        if n.get("mal_id") and n.get("title"):
            results.append(n)

    _cache_set(cache_key, results)
    return results


async def get_anime_by_id(mal_id: int) -> Optional[Dict]:
    """Get anime details by MAL ID (normalized)."""
    cache_key = f"id:{mal_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = f"{JIKAN_BASE_URL}/anime/{mal_id}"
    data = await _get_json(url)
    item = (data or {}).get("data")
    n = normalize_anime(item)

    if not n.get("mal_id"):
        return None

    _cache_set(cache_key, n)
    return n


# ---------------- MAL Direct List Fetch (unchanged except tiny polish) ----------------

async def get_user_animelist_direct(username: str, status: str = None, limit: int = 500) -> List[Dict]:
    """
    Fetch a user's anime list directly from MyAnimeList.

    Returns:
        List of entries with mal_id, title, episodes, image_url, score, status, episodes_watched
    """
    status_map = {
        "watching": 1,
        "completed": 2,
        "on_hold": 3,
        "dropped": 4,
        "plan_to_watch": 6,
        None: 7,  # all
    }
    status_code = status_map.get(status, 7)

    all_entries: List[Dict] = []
    offset = 0
    page_size = 300

    try:
        session = await get_session()

        while len(all_entries) < limit:
            url = f"https://myanimelist.net/animelist/{username}/load.json"
            params = {"status": status_code, "offset": offset}

            async with session.get(url, params=params, timeout=JIKAN_TIMEOUT) as resp:
                if resp.status == 400:
                    logger.warning(f"MAL list not accessible for {username} (not found or private)")
                    return []
                if resp.status != 200:
                    logger.warning(f"MAL list fetch error: status={resp.status}")
                    return all_entries

                data = await resp.json()
                if not data:
                    break

                status_names = {1: "watching", 2: "completed", 3: "on_hold", 4: "dropped", 6: "plan_to_watch"}

                for item in data:
                    title = item.get("anime_title_eng") or item.get("anime_title", "Unknown")
                    if isinstance(title, int):
                        title = str(title)

                    mal_status = item.get("status")

                    all_entries.append({
                        "mal_id": item.get("anime_id"),
                        "title": title,
                        "episodes": item.get("anime_num_episodes"),
                        "image_url": item.get("anime_image_path"),
                        "score": item.get("score") if item.get("score") else None,
                        "status": status_names.get(mal_status, "unknown"),
                        "episodes_watched": item.get("num_watched_episodes", 0),
                    })

                offset += len(data)

                if len(data) < page_size:
                    break

            await asyncio.sleep(MIN_REQUEST_INTERVAL)

    except asyncio.TimeoutError:
        logger.warning(f"MAL list fetch timeout for {username}")
    except Exception as e:
        logger.warning(f"MAL list fetch error for {username}: {type(e).__name__}: {e}")

    return all_entries


async def get_user_animelist(username: str, status: str = None, limit: int = 300) -> List[Dict]:
    """
    Fetch a user's anime list from MyAnimeList via Jikan API.
    Returns empty list if unavailable.
    """
    all_entries: List[Dict] = []
    offset = 0
    page_size = 25

    while len(all_entries) < limit:
        url = f"{JIKAN_BASE_URL}/users/{username}/animelist"
        params = {"limit": min(page_size, limit - len(all_entries))}
        if offset > 0:
            params["page"] = (offset // page_size) + 1
        if status:
            params["status"] = status

        data = await _get_json(url, params=params)
        if not data or "data" not in data:
            if len(all_entries) == 0:
                logger.warning(f"Failed to fetch animelist for {username} (MAL/Jikan may be down)")
            break

        entries = data["data"]
        if not entries:
            break

        for item in entries:
            anime = item.get("anime", {})
            n = normalize_anime(anime)

            all_entries.append({
                "mal_id": n.get("mal_id"),
                "title": n.get("title") or "Unknown",
                "episodes": n.get("episodes"),
                "image_url": n.get("image_url") or "",
                "score": item.get("score"),
                "status": item.get("status"),
                "episodes_watched": item.get("episodes_watched", 0),
            })

        offset += len(entries)

        pagination = data.get("pagination", {})
        if not pagination.get("has_next_page", False):
            break

        await asyncio.sleep(MIN_REQUEST_INTERVAL)

    return all_entries


async def warmup_session():
    """Pre-warm the session to reduce first-request latency."""
    try:
        session = await get_session()
        async with session.get(f"{JIKAN_BASE_URL}/anime/1", timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                logger.info("Jikan API session pre-warmed")
            else:
                logger.warning(f"Jikan API warmup got status {resp.status}")
    except Exception as e:
        logger.warning(f"Failed to pre-warm Jikan session: {type(e).__name__}: {e}")
