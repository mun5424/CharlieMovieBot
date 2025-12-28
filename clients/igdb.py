"""
IGDB API client for game search
https://api-docs.igdb.com/
"""

import aiohttp
import asyncio
import logging
import time
from typing import Optional, List, Dict

import config

logger = logging.getLogger(__name__)

# IGDB API configuration
IGDB_API_URL = "https://api.igdb.com/v4"
TWITCH_AUTH_URL = "https://id.twitch.tv/oauth2/token"
IGDB_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3)
IGDB_AUTOCOMPLETE_TIMEOUT = aiohttp.ClientTimeout(total=2.8, connect=1.5)

# Rate limiting: 4 requests per second
_last_request_time = 0
_request_lock = asyncio.Lock()
MIN_REQUEST_INTERVAL = 0.25  # ~4 requests per second

# Shared session and token
_session: Optional[aiohttp.ClientSession] = None
_access_token: Optional[str] = None
_token_expires_at: float = 0

# Search cache with TTL
_search_cache: Dict[str, tuple] = {}
CACHE_TTL = 60  # Cache results for 60 seconds
MAX_CACHE_SIZE = 50


async def get_session() -> aiohttp.ClientSession:
    """Get or create the shared aiohttp session."""
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=5, keepalive_timeout=30)
        _session = aiohttp.ClientSession(
            timeout=IGDB_TIMEOUT,
            connector=connector,
        )
    return _session


async def close_session():
    """Close the shared session."""
    global _session, _access_token
    if _session and not _session.closed:
        await _session.close()
        _session = None
    _access_token = None
    logger.info("Closed IGDB API session")


async def _get_access_token() -> Optional[str]:
    """Get or refresh the Twitch OAuth access token for IGDB."""
    global _access_token, _token_expires_at

    # Return cached token if still valid (with 60s buffer)
    if _access_token and time.time() < _token_expires_at - 60:
        return _access_token

    client_id = getattr(config, 'TWITCH_API_CLIENT', '')
    client_secret = getattr(config, 'TWITCH_API_CLIENT_SECRET', '')

    if not client_id or not client_secret:
        logger.warning("IGDB: Twitch API credentials not configured")
        return None

    try:
        session = await get_session()
        async with session.post(
            TWITCH_AUTH_URL,
            params={
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'client_credentials'
            }
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                _access_token = data['access_token']
                _token_expires_at = time.time() + data.get('expires_in', 3600)
                logger.info("IGDB: Obtained new access token")
                return _access_token
            else:
                logger.error(f"IGDB: Failed to get access token: {resp.status}")
                return None
    except Exception as e:
        logger.error(f"IGDB: Error getting access token: {e}")
        return None


async def _api_request(endpoint: str, body: str, timeout: aiohttp.ClientTimeout = None, _retry: bool = False) -> Optional[dict]:
    """Make an authenticated request to IGDB API."""
    global _last_request_time

    token = await _get_access_token()
    if not token:
        return None

    client_id = getattr(config, 'TWITCH_API_CLIENT', '')

    # Rate limiting: acquire lock, wait if needed, then release before making request
    async with _request_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()

    try:
        session = await get_session()
        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {token}',
        }
        async with session.post(
            f"{IGDB_API_URL}/{endpoint}",
            headers=headers,
            data=body,
            timeout=timeout or IGDB_TIMEOUT
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 401 and not _retry:
                # Token expired, clear it and retry once (prevent infinite recursion)
                global _access_token
                _access_token = None
                logger.warning("IGDB: Token expired, retrying...")
                return await _api_request(endpoint, body, timeout, _retry=True)
            else:
                logger.error(f"IGDB API error: {resp.status}")
                return None
    except asyncio.TimeoutError:
        logger.debug("IGDB API timeout")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"IGDB API client error: {e}")
        return None


def _clean_cache():
    """Remove expired entries from cache."""
    global _search_cache
    now = time.time()
    expired = [k for k, (_, ts) in _search_cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        del _search_cache[k]

    if len(_search_cache) > MAX_CACHE_SIZE:
        sorted_keys = sorted(_search_cache.keys(), key=lambda k: _search_cache[k][1])
        for k in sorted_keys[:len(_search_cache) - MAX_CACHE_SIZE]:
            del _search_cache[k]


def _parse_game_response(game: Dict) -> Dict:
    """Parse IGDB game response into standardized format."""
    # Build cover URL if available
    cover_url = None
    if game.get('cover') and game['cover'].get('image_id'):
        cover_url = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{game['cover']['image_id']}.jpg"

    # Extract platform names
    platforms = []
    if game.get('platforms'):
        platforms = [p.get('name', '') for p in game['platforms'] if p.get('name')]

    # Extract genre names
    genres = []
    if game.get('genres'):
        genres = [g.get('name', '') for g in game['genres'] if g.get('name')]

    # Extract developer/publisher (first company found)
    developer = None
    if game.get('involved_companies'):
        for company in game['involved_companies']:
            if company.get('company', {}).get('name'):
                developer = company['company']['name']
                break

    return {
        'id': game.get('id'),
        'name': game.get('name', 'Unknown'),
        'cover_url': cover_url,
        'release_date': game.get('first_release_date'),
        'platforms': platforms,
        'genres': genres,
        'summary': game.get('summary', ''),
        'rating': game.get('rating'),
        'developer': developer,
        'url': game.get('url'),
    }


async def search_games(query: str, limit: int = 10) -> List[Dict]:
    """
    Search for games by title.

    Returns list of games with:
    - id: IGDB game ID
    - name: Game title
    - cover_url: Cover image URL
    - release_date: Unix timestamp
    - platforms: List of platform names
    - summary: Game description
    - rating: IGDB rating (0-100)
    """
    cache_key = f"{query.lower().strip()}:{limit}"

    if cache_key in _search_cache:
        results, timestamp = _search_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return results

    _clean_cache()

    # IGDB uses Apicalypse query language
    body = f'''
        search "{query}";
        fields name, cover.image_id, first_release_date, platforms.name,
               summary, rating, genres.name, involved_companies.company.name;
        limit {limit};
    '''

    data = await _api_request("games", body)
    if not data:
        return []

    results = [_parse_game_response(game) for game in data]
    _search_cache[cache_key] = (results, time.time())
    return results


async def search_games_async(query: str) -> Optional[Dict]:
    """Search for a single game (first result)."""
    results = await search_games(query, limit=1)
    return results[0] if results else None


async def search_games_autocomplete(query: str, limit: int = 10) -> List[Dict]:
    """
    Fast game search for autocomplete (shorter timeout, cache-first).
    """
    if len(query) < 2:
        return []

    cache_key = f"ac:{query.lower().strip()}:{limit}"

    if cache_key in _search_cache:
        results, timestamp = _search_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return results

    body = f'''
        search "{query}";
        fields name, first_release_date, platforms.abbreviation;
        limit {limit};
    '''

    try:
        data = await _api_request("games", body, timeout=IGDB_AUTOCOMPLETE_TIMEOUT)
        if not data:
            return []

        results = []
        for game in data:
            # Extract platform abbreviations
            platforms = []
            if game.get('platforms'):
                platforms = [p.get('abbreviation', '') for p in game['platforms'] if p.get('abbreviation')]

            # Get release year
            year = None
            if game.get('first_release_date'):
                from datetime import datetime
                year = datetime.fromtimestamp(game['first_release_date']).year

            results.append({
                'id': game.get('id'),
                'name': game.get('name', 'Unknown'),
                'year': year,
                'platforms': platforms[:3],  # Limit platforms for display
            })

        _search_cache[cache_key] = (results, time.time())
        return results

    except Exception as e:
        logger.debug(f"IGDB autocomplete error: {e}")
        return []


async def get_game_by_id(game_id: int) -> Optional[Dict]:
    """Get game details by IGDB ID."""
    # Check cache first
    cache_key = f"id:{game_id}"
    if cache_key in _search_cache:
        result, timestamp = _search_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return result

    body = f'''
        fields name, cover.image_id, first_release_date, platforms.name,
               summary, rating, genres.name, involved_companies.company.name,
               url, websites.url, websites.category;
        where id = {game_id};
    '''

    data = await _api_request("games", body)
    if not data or len(data) == 0:
        return None

    result = _parse_game_response(data[0])
    _search_cache[cache_key] = (result, time.time())
    return result


async def warmup_session():
    """Pre-warm the session and get initial access token."""
    try:
        token = await _get_access_token()
        if token:
            logger.info("IGDB API session pre-warmed")
        else:
            logger.warning("IGDB API warmup failed - check Twitch credentials")
    except Exception as e:
        logger.warning(f"Failed to pre-warm IGDB session: {e}")
