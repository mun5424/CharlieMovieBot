import requests
import asyncio
from config import TMDB_API_KEY

async def search_movies_autocomplete(query: str, limit: int = 25):
    """Search for movies and return a list for autocomplete - ASYNC VERSION"""
    if len(query) < 2:  # Don't search for very short queries
        return []
    
    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": query, "page": 1}
    
    try:
        # Run the blocking request in a thread to make it async
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: requests.get(url, params=params).json())
        hits = res.get("results", [])
        
        movies = []
        for movie in hits[:limit]:  # Limit results for autocomplete
            title = movie.get("title", "Unknown")
            year = movie.get("release_date", "").split("-")[0] if movie.get("release_date") else ""
            
            # Format as "Title (Year)" or just "Title" if no year
            display_name = f"{title} ({year})" if year else title
            movies.append({
                "name": display_name,  # What shows in the dropdown
                "value": title        # What gets passed to the command
            })
        
        return movies
    except Exception as e:
        print(f"Error in autocomplete search: {e}")
        return []

def search_movie(title: str):
    """Regular sync function for searching movies"""
    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title}
    res = requests.get(url, params=params).json()
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

def get_movie_details(movie_id: int):
    """Get detailed movie information including director and genres"""
    url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "append_to_response": "credits"
    }
    
    try:
        res = requests.get(url, params=params).json()
        
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
    except Exception as e:
        print(f"Error getting movie details: {e}")
        return None