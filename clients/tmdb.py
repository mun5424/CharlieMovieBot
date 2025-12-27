import aiohttp
import asyncio
import logging
import time
from config import TMDB_API_KEY

logger = logging.getLogger(__name__)

# Shared timeout configuration
# Autocomplete has 3s Discord limit, so use shorter timeouts
TMDB_TIMEOUT = aiohttp.ClientTimeout(total=8, connect=3)
TMDB_AUTOCOMPLETE_TIMEOUT = aiohttp.ClientTimeout(total=2.8, connect=1.5)  # Must respond within 3s

# Shared session for connection reuse (avoids cold-start latency)
_session: aiohttp.ClientSession = None

# Search cache with TTL (query -> (results, timestamp))
_search_cache: dict = {}
CACHE_TTL = 30  # Cache results for 30 seconds (reduced for Pi memory)
MAX_CACHE_SIZE = 50  # Maximum cached queries (reduced for Pi memory)


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


async def get_session() -> aiohttp.ClientSession:
    """Get or create the shared aiohttp session"""
    global _session
    if _session is None or _session.closed:
        # Use a connector with connection pooling
        connector = aiohttp.TCPConnector(
            limit=5,  # Max connections
            keepalive_timeout=30,  # Keep connections alive
            enable_cleanup_closed=True
        )
        _session = aiohttp.ClientSession(
            timeout=TMDB_TIMEOUT,
            connector=connector
        )
        logger.info("Created new TMDB aiohttp session")
    return _session


async def warmup_session():
    """Pre-warm the session by making a lightweight request"""
    try:
        session = await get_session()
        # Make a simple request to establish connection
        url = "https://api.themoviedb.org/3/configuration"
        params = {"api_key": TMDB_API_KEY}
        async with session.get(url, params=params) as resp:
            await resp.read()
        logger.info("TMDB session pre-warmed successfully")
    except Exception as e:
        logger.warning(f"Failed to pre-warm TMDB session: {e}")


async def close_session():
    """Close the shared session (call on bot shutdown)"""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
        logger.info("Closed TMDB aiohttp session")


async def search_movies_autocomplete(query: str, limit: int = 25):
    if len(query) < 2:  # Don't search for very short queries
        return []

    # Normalize query for cache key
    cache_key = f"ac:{query.lower().strip()}:{limit}"

    # Check cache first
    if cache_key in _search_cache:
        results, timestamp = _search_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return results

    # Clean cache periodically
    _clean_cache()

    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": query, "page": 1}

    try:
        session = await get_session()
        # Use shorter timeout for autocomplete (Discord has 3s limit)
        async with session.get(url, params=params, timeout=TMDB_AUTOCOMPLETE_TIMEOUT) as resp:
            if resp.status != 200:
                return []
            res = await resp.json()

        hits = res.get("results", [])

        movies = []
        for movie in hits[:limit]:  # Limit results for autocomplete
            title = movie.get("title", "")
            if not title:
                continue  # Skip movies with no title

            year = movie.get("release_date", "").split("-")[0] if movie.get("release_date") else ""

            # Format as "Title (Year)" or just "Title" if no year
            display_name = f"{title} ({year})" if year else title

            # Discord requires name to be 1-100 characters
            if len(display_name) > 100:
                display_name = display_name[:97] + "..."
            if len(display_name) < 1:
                continue  # Skip empty names

            # Use the full display_name as value so each choice is unique
            movies.append({
                "name": display_name,      # What shows in the dropdown
                "value": display_name      # What gets passed to the command
            })

        # Cache the results
        _search_cache[cache_key] = (movies, time.time())

        return movies
    except asyncio.TimeoutError:
        logger.debug("TMDB autocomplete timed out (expected under load)")
        return []
    except Exception as e:
        logger.error(f"Error in autocomplete search: {e}")
        return []


async def search_movie_async(title_with_year: str):
    """Async function for searching movies - use this in async contexts"""
    # Extract just the title if it has (Year) format
    if " (" in title_with_year and title_with_year.endswith(")"):
        # Split on " (" and take the first part
        title = title_with_year.split(" (")[0]
        # Extract the year for better matching
        year = title_with_year.split(" (")[1].rstrip(")")
    else:
        title = title_with_year
        year = None

    # Check cache first
    cache_key = f"search:{title.lower().strip()}:{year or ''}"
    if cache_key in _search_cache:
        result, timestamp = _search_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return result

    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title}

    # Add year to search if we have it for better accuracy
    if year and year.isdigit():
        params["year"] = year

    try:
        session = await get_session()
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            res = await resp.json()

        hits = res.get("results", [])
        if hits:
            m = hits[0]
            result = {
                "id": m["id"],
                "title": m["title"],
                "year": m.get("release_date", "").split("-")[0] if m.get("release_date") else "Unknown",
                "overview": m.get("overview", "No description available"),
                "rating": m.get("vote_average", 0),
                "poster_path": m.get("poster_path"),
                "genre_ids": m.get("genre_ids", [])
            }
            # Cache the result
            _search_cache[cache_key] = (result, time.time())
            return result
        return None
    except asyncio.TimeoutError:
        logger.warning("TMDB search request timed out")
        return None
    except Exception as e:
        logger.error(f"Error searching movie: {e}")
        return None


def search_movie(title_with_year: str):
    """Sync wrapper for search_movie_async - runs async code in new event loop.

    WARNING: This is a compatibility shim. Prefer search_movie_async in async contexts.
    """
    try:
        # Try to get running loop and use run_in_executor pattern
        loop = asyncio.get_running_loop()
        # If we're already in an async context, we can't use asyncio.run()
        # This shouldn't happen if code is properly async, but as fallback:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, search_movie_async(title_with_year))
            return future.result(timeout=15)
    except RuntimeError:
        # No running loop, safe to use asyncio.run()
        return asyncio.run(search_movie_async(title_with_year))
    except Exception as e:
        print(f"Error in sync search_movie wrapper: {e}")
        return None


async def get_movie_details_async(movie_id: int):
    """Async function to get detailed movie information including director and genres"""
    url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "append_to_response": "credits"
    }

    try:
        session = await get_session()
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            res = await resp.json()

        # Get director from credits
        director = "Unknown"
        if "credits" in res and "crew" in res["credits"]:
            for person in res["credits"]["crew"]:
                if person["job"] == "Director":
                    director = person["name"]
                    break

        # Get genres
        genres = [genre["name"] for genre in res.get("genres", [])]
        genre_str = ", ".join(genres) if genres else "Unknown"

        return {
            "id": res["id"],
            "title": res["title"],
            "year": res.get("release_date", "").split("-")[0] if res.get("release_date") else "Unknown",
            "overview": res.get("overview", "No description available"),
            "rating": res.get("vote_average", 0),
            "director": director,
            "genre": genre_str,
            "runtime": res.get("runtime", 0),
            "poster_path": res.get("poster_path")
        }
    except asyncio.TimeoutError:
        print("TMDB details request timed out")
        return None
    except Exception as e:
        print(f"Error getting movie details: {e}")
        return None


def get_movie_details(movie_id: int):
    """Sync wrapper for get_movie_details_async - runs async code in new event loop.

    WARNING: This is a compatibility shim. Prefer get_movie_details_async in async contexts.
    """
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, get_movie_details_async(movie_id))
            return future.result(timeout=15)
    except RuntimeError:
        return asyncio.run(get_movie_details_async(movie_id))
    except Exception as e:
        print(f"Error in sync get_movie_details wrapper: {e}")
        return None
