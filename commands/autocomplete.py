# commands/autocomplete.py - Shared autocomplete functions
import logging
from discord import app_commands
from clients.tmdb import search_movies_autocomplete

logger = logging.getLogger(__name__)

# Constants - reduced for Pi Zero 2 W memory efficiency
AUTOCOMPLETE_LIMIT = 10  # Reduced from 25 for faster response


async def movie_search_autocomplete(interaction, current: str):
    """Shared autocomplete function for movie titles.

    Note: Autocomplete has a 3s Discord timeout. On slow connections/hardware,
    this may timeout and return empty results. That's expected - user can
    still type the full title and the command will search properly.
    """
    if len(current) < 2:
        return []

    try:
        movies = await search_movies_autocomplete(current, limit=AUTOCOMPLETE_LIMIT)
        choices = []
        for movie in movies:
            name = movie.get("name", "")
            value = movie.get("value", "")
            # Discord requires name to be 1-100 characters
            if not name or len(name) > 100:
                continue
            choices.append(app_commands.Choice(name=name, value=value))
        return choices
    except Exception as e:
        # Timeouts are expected on slow hardware - don't spam logs
        logger.debug(f"Autocomplete timeout/error (expected): {e}")
        return []
