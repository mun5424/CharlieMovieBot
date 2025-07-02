import requests
from config import TMDB_API_KEY

def search_movie(title: str):
    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title}
    res = requests.get(url, params=params).json()
    hits = res.get("results", [])
    if hits:
        m = hits[0]
        return {
            "id": m["id"],
            "title": m["title"],
            "year": m.get("release_date", "").split("-")[0]
        }