import aiohttp
import asyncio
from config import TMDB_API_KEY

# Shared timeout configuration
TMDB_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)


async def search_movies_autocomplete(query: str, limit: int = 25):
    if len(query) < 2:  # Don't search for very short queries
        return []

    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": query, "page": 1}

    try:
        async with aiohttp.ClientSession(timeout=TMDB_TIMEOUT) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                res = await resp.json()

        hits = res.get("results", [])

        movies = []
        for movie in hits[:limit]:  # Limit results for autocomplete
            title = movie.get("title", "Unknown")
            year = movie.get("release_date", "").split("-")[0] if movie.get("release_date") else ""

            # Format as "Title (Year)" or just "Title" if no year
            display_name = f"{title} ({year})" if year else title

            # Use the full display_name as value so each choice is unique
            movies.append({
                "name": display_name,      # What shows in the dropdown
                "value": display_name      # What gets passed to the command
            })

        return movies
    except asyncio.TimeoutError:
        print("TMDB autocomplete request timed out")
        return []
    except Exception as e:
        print(f"Error in autocomplete search: {e}")
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

    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title}

    # Add year to search if we have it for better accuracy
    if year and year.isdigit():
        params["year"] = year

    try:
        async with aiohttp.ClientSession(timeout=TMDB_TIMEOUT) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                res = await resp.json()

        hits = res.get("results", [])
        if hits:
            m = hits[0]
            return {
                "id": m["id"],
                "title": m["title"],
                "year": m.get("release_date", "").split("-")[0] if m.get("release_date") else "Unknown",
                "overview": m.get("overview", "No description available"),
                "rating": m.get("vote_average", 0),
                "poster_path": m.get("poster_path"),
                "genre_ids": m.get("genre_ids", [])
            }
        return None
    except asyncio.TimeoutError:
        print("TMDB search request timed out")
        return None
    except Exception as e:
        print(f"Error searching movie: {e}")
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
        async with aiohttp.ClientSession(timeout=TMDB_TIMEOUT) as session:
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