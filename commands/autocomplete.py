# commands/autocomplete.py - Shared autocomplete functions
import logging
from discord import app_commands
from tmdb_client import search_movies_autocomplete

logger = logging.getLogger(__name__)

# Constants - reduced for Pi Zero 2 W memory efficiency
AUTOCOMPLETE_LIMIT = 10  # Reduced from 25 for faster response


async def movie_search_autocomplete(interaction, current: str):
    """Shared autocomplete function for movie titles"""
    if len(current) < 2:
        return []

    try:
        movies = await search_movies_autocomplete(current, limit=AUTOCOMPLETE_LIMIT)
        return [
            app_commands.Choice(name=movie["name"], value=movie["value"])
            for movie in movies
        ]
    except Exception as e:
        logger.error(f"Autocomplete error: {e}")
        return []
