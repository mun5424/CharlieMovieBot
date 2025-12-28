# clients/ - External API clients
from clients.tmdb import (
    search_movie_async,
    search_movie,
    search_movies_autocomplete,
    get_movie_details_async,
    get_movie_details,
    get_session,
    warmup_session,
    close_session,
)
from clients.jikan import (
    search_anime,
    search_anime_async,
    search_anime_autocomplete,
    get_anime_by_id,
    warmup_session as warmup_jikan_session,
    close_session as close_jikan_session,
)
from clients.igdb import (
    search_games,
    search_games_async,
    search_games_autocomplete,
    get_game_by_id,
    warmup_session as warmup_igdb_session,
    close_session as close_igdb_session,
)
