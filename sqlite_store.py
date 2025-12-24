"""
SQLite-based data storage for CharlieMovieBot
Memory-efficient replacement for JSON file storage
"""

import aiosqlite
import asyncio
import json
import logging
import os
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Database file path
DB_FILE = os.path.join(os.path.dirname(__file__), "movie_data.db")

# Connection pool
_db: Optional[aiosqlite.Connection] = None
_lock = asyncio.Lock()


async def get_db() -> aiosqlite.Connection:
    """Get or create database connection"""
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_FILE)
        _db.row_factory = aiosqlite.Row
        await _init_tables(_db)
        logger.info(f"Connected to SQLite database: {DB_FILE}")
    return _db


async def close_db():
    """Close database connection"""
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("Closed SQLite database connection")


async def _init_tables(db: aiosqlite.Connection):
    """Initialize database tables"""
    await db.executescript("""
        -- User watchlists (unified: includes watched status)
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            movie_id INTEGER NOT NULL,
            title TEXT,
            year TEXT,
            overview TEXT,
            rating REAL,
            poster_path TEXT,
            added_at REAL,
            watched_at REAL,
            UNIQUE(user_id, movie_id)
        );

        -- Legacy watched table (kept for migration, will be deprecated)
        CREATE TABLE IF NOT EXISTS watched (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            movie_id INTEGER NOT NULL,
            title TEXT,
            year TEXT,
            overview TEXT,
            rating REAL,
            poster_path TEXT,
            watched_at REAL,
            UNIQUE(user_id, movie_id)
        );

        -- Pending suggestions from other users
        CREATE TABLE IF NOT EXISTS pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            from_user_id TEXT,
            from_username TEXT,
            movie_id INTEGER NOT NULL,
            movie_data TEXT,
            suggested_at REAL
        );

        -- Movie reviews
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_id INTEGER NOT NULL,
            movie_title TEXT,
            movie_year TEXT,
            user_id TEXT NOT NULL,
            username TEXT,
            score REAL,
            review_text TEXT,
            timestamp REAL,
            UNIQUE(movie_id, user_id)
        );

        -- Anime watchlist (unified: includes watched status)
        CREATE TABLE IF NOT EXISTS anime_watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            mal_id INTEGER NOT NULL,
            title TEXT,
            title_japanese TEXT,
            episodes INTEGER,
            status TEXT,
            score REAL,
            image_url TEXT,
            year INTEGER,
            anime_type TEXT,
            added_at REAL,
            watched_at REAL,
            UNIQUE(user_id, mal_id)
        );

        -- Anime reviews
        CREATE TABLE IF NOT EXISTS anime_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mal_id INTEGER NOT NULL,
            anime_title TEXT,
            user_id TEXT NOT NULL,
            username TEXT,
            score REAL,
            review_text TEXT,
            timestamp REAL,
            UNIQUE(mal_id, user_id)
        );

        -- Indexes for fast lookups
        CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id);
        CREATE INDEX IF NOT EXISTS idx_watchlist_watched ON watchlist(user_id, watched_at);
        CREATE INDEX IF NOT EXISTS idx_watched_user ON watched(user_id);
        CREATE INDEX IF NOT EXISTS idx_pending_user ON pending(user_id);
        CREATE INDEX IF NOT EXISTS idx_reviews_movie ON reviews(movie_id);
        CREATE INDEX IF NOT EXISTS idx_anime_watchlist_user ON anime_watchlist(user_id);
        CREATE INDEX IF NOT EXISTS idx_anime_watchlist_watched ON anime_watchlist(user_id, watched_at);
        CREATE INDEX IF NOT EXISTS idx_anime_reviews_mal ON anime_reviews(mal_id);
    """)

    # Add watched_at column if it doesn't exist (for existing databases)
    try:
        await db.execute("ALTER TABLE watchlist ADD COLUMN watched_at REAL")
        await db.commit()
        logger.info("Added watched_at column to watchlist table")
    except Exception:
        pass  # Column already exists

    await db.commit()


# ============== Watchlist Operations ==============

async def get_user_watchlist(user_id: str, filter_mode: str = "all") -> List[Dict]:
    """
    Get a user's watchlist with optional filtering.

    Args:
        user_id: The user's ID
        filter_mode: "all" (default), "unwatched", or "watched"

    Returns:
        List of movies sorted by added_at DESC (most recent first)
    """
    db = await get_db()
    async with _lock:
        if filter_mode == "unwatched":
            query = """
                SELECT movie_id, title, year, overview, rating, poster_path, added_at, watched_at
                FROM watchlist WHERE user_id = ? AND watched_at IS NULL
                ORDER BY added_at DESC
            """
        elif filter_mode == "watched":
            query = """
                SELECT movie_id, title, year, overview, rating, poster_path, added_at, watched_at
                FROM watchlist WHERE user_id = ? AND watched_at IS NOT NULL
                ORDER BY watched_at DESC
            """
        else:  # "all"
            query = """
                SELECT movie_id, title, year, overview, rating, poster_path, added_at, watched_at
                FROM watchlist WHERE user_id = ?
                ORDER BY added_at DESC
            """

        cursor = await db.execute(query, (user_id,))
        rows = await cursor.fetchall()
        return [
            {
                "id": row["movie_id"],
                "title": row["title"],
                "year": row["year"],
                "overview": row["overview"],
                "rating": row["rating"],
                "poster_path": row["poster_path"],
                "added_at": row["added_at"],
                "watched_at": row["watched_at"]
            }
            for row in rows
        ]


async def get_watchlist_counts(user_id: str) -> Dict[str, int]:
    """Get counts of total, watched, and unwatched movies."""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN watched_at IS NOT NULL THEN 1 ELSE 0 END) as watched,
                SUM(CASE WHEN watched_at IS NULL THEN 1 ELSE 0 END) as unwatched
            FROM watchlist WHERE user_id = ?
            """,
            (user_id,)
        )
        row = await cursor.fetchone()
        return {
            "total": row["total"] or 0,
            "watched": row["watched"] or 0,
            "unwatched": row["unwatched"] or 0
        }


async def add_to_watchlist(user_id: str, movie: Dict) -> bool:
    """Add a movie to user's watchlist. Returns False if already exists."""
    db = await get_db()
    async with _lock:
        try:
            import time
            await db.execute(
                "INSERT INTO watchlist (user_id, movie_id, title, year, overview, rating, poster_path, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    movie.get("id"),
                    movie.get("title"),
                    movie.get("year"),
                    movie.get("overview"),
                    movie.get("rating"),
                    movie.get("poster_path"),
                    time.time()
                )
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_from_watchlist(user_id: str, movie_id: int) -> bool:
    """Remove a movie from user's watchlist. Returns True if removed."""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def is_in_watchlist(user_id: str, movie_id: int) -> bool:
    """Check if a movie is in user's watchlist"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT 1 FROM watchlist WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        return await cursor.fetchone() is not None


async def get_watchlist_movie(user_id: str, movie_id: int) -> Optional[Dict]:
    """Get a specific movie from user's watchlist"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT movie_id, title, year, overview, rating, poster_path, added_at, watched_at "
            "FROM watchlist WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        row = await cursor.fetchone()
        if row:
            return {
                "id": row["movie_id"],
                "title": row["title"],
                "year": row["year"],
                "overview": row["overview"],
                "rating": row["rating"],
                "poster_path": row["poster_path"],
                "added_at": row["added_at"],
                "watched_at": row["watched_at"]
            }
        return None


async def mark_as_watched(user_id: str, movie_id: int, movie: Optional[Dict] = None) -> str:
    """
    Mark a movie as watched. If not in watchlist, adds it first.

    Args:
        user_id: The user's ID
        movie_id: The movie's TMDB ID
        movie: Movie data dict (required if movie not already in watchlist)

    Returns:
        "marked" if already in watchlist and marked
        "added_and_marked" if added to watchlist and marked
        "already_watched" if already marked as watched
        "error" if movie data required but not provided
    """
    import time
    db = await get_db()
    async with _lock:
        # Check if movie is already in watchlist
        cursor = await db.execute(
            "SELECT watched_at FROM watchlist WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        row = await cursor.fetchone()

        if row:
            # Movie exists in watchlist
            if row["watched_at"] is not None:
                return "already_watched"
            # Mark as watched
            await db.execute(
                "UPDATE watchlist SET watched_at = ? WHERE user_id = ? AND movie_id = ?",
                (time.time(), user_id, movie_id)
            )
            await db.commit()
            return "marked"
        else:
            # Movie not in watchlist - need to add it
            if not movie:
                return "error"
            await db.execute(
                "INSERT INTO watchlist (user_id, movie_id, title, year, overview, rating, poster_path, added_at, watched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    movie.get("id"),
                    movie.get("title"),
                    movie.get("year"),
                    movie.get("overview"),
                    movie.get("rating"),
                    movie.get("poster_path"),
                    time.time(),
                    time.time()  # Also set watched_at since we're marking as watched
                )
            )
            await db.commit()
            return "added_and_marked"


async def mark_as_unwatched(user_id: str, movie_id: int) -> bool:
    """Mark a movie as unwatched. Returns True if updated."""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "UPDATE watchlist SET watched_at = NULL WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        await db.commit()
        return cursor.rowcount > 0


# ============== Watched Operations ==============

async def get_user_watched(user_id: str) -> List[Dict]:
    """Get a user's watched list"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT movie_id, title, year, overview, rating, poster_path "
            "FROM watched WHERE user_id = ? ORDER BY watched_at DESC",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["movie_id"],
                "title": row["title"],
                "year": row["year"],
                "overview": row["overview"],
                "rating": row["rating"],
                "poster_path": row["poster_path"]
            }
            for row in rows
        ]


async def add_to_watched(user_id: str, movie: Dict) -> bool:
    """Add a movie to user's watched list. Returns False if already exists."""
    db = await get_db()
    async with _lock:
        try:
            import time
            await db.execute(
                "INSERT INTO watched (user_id, movie_id, title, year, overview, rating, poster_path, watched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    movie.get("id"),
                    movie.get("title"),
                    movie.get("year"),
                    movie.get("overview"),
                    movie.get("rating"),
                    movie.get("poster_path"),
                    time.time()
                )
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_from_watched(user_id: str, movie_id: int) -> bool:
    """Remove a movie from user's watched list. Returns True if removed."""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "DELETE FROM watched WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def is_in_watched(user_id: str, movie_id: int) -> bool:
    """Check if a movie is in user's watched list"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT 1 FROM watched WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        return await cursor.fetchone() is not None


# ============== Pending Suggestions Operations ==============

async def get_user_pending(user_id: str) -> List[Dict]:
    """Get pending suggestions for a user"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT id, from_user_id, from_username, movie_id, movie_data "
            "FROM pending WHERE user_id = ? ORDER BY suggested_at DESC",
            (user_id,)
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            movie_data = json.loads(row["movie_data"]) if row["movie_data"] else {}
            result.append({
                "id": row["id"],
                "from_user_id": row["from_user_id"],
                "from_username": row["from_username"],
                "movie": movie_data
            })
        return result


async def add_pending_suggestion(user_id: str, from_user_id: str, from_username: str, movie: Dict) -> bool:
    """Add a pending suggestion"""
    db = await get_db()
    async with _lock:
        import time
        await db.execute(
            "INSERT INTO pending (user_id, from_user_id, from_username, movie_id, movie_data, suggested_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                from_user_id,
                from_username,
                movie.get("id"),
                json.dumps(movie),
                time.time()
            )
        )
        await db.commit()
        return True


async def remove_pending_by_movie_id(user_id: str, movie_id: int) -> bool:
    """Remove a pending suggestion by movie ID"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "DELETE FROM pending WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_pending_by_movie_id(user_id: str, movie_id: int) -> Optional[Dict]:
    """Get a pending suggestion by movie ID"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT id, from_user_id, from_username, movie_data "
            "FROM pending WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        row = await cursor.fetchone()
        if row:
            movie_data = json.loads(row["movie_data"]) if row["movie_data"] else {}
            return {
                "id": row["id"],
                "from_user_id": row["from_user_id"],
                "from_username": row["from_username"],
                "movie": movie_data
            }
        return None


# ============== Review Operations ==============

async def get_movie_reviews(movie_id: int) -> List[Dict]:
    """Get all reviews for a movie"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT user_id, username, score, review_text, movie_title, movie_year, timestamp "
            "FROM reviews WHERE movie_id = ? ORDER BY timestamp DESC",
            (movie_id,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "score": row["score"],
                "review_text": row["review_text"],
                "movie_title": row["movie_title"],
                "movie_year": row["movie_year"],
                "timestamp": row["timestamp"]
            }
            for row in rows
        ]


async def add_movie_review(movie_id: int, movie_title: str, movie_year: str,
                           user_id: str, username: str, score: float, review_text: str) -> str:
    """Add or update a review. Returns 'added' or 'updated'."""
    db = await get_db()
    async with _lock:
        import time
        # Check if review exists
        cursor = await db.execute(
            "SELECT 1 FROM reviews WHERE movie_id = ? AND user_id = ?",
            (movie_id, user_id)
        )
        exists = await cursor.fetchone() is not None

        if exists:
            await db.execute(
                "UPDATE reviews SET username = ?, score = ?, review_text = ?, "
                "movie_title = ?, movie_year = ?, timestamp = ? "
                "WHERE movie_id = ? AND user_id = ?",
                (username, score, review_text, movie_title, movie_year, time.time(), movie_id, user_id)
            )
            await db.commit()
            return "updated"
        else:
            await db.execute(
                "INSERT INTO reviews (movie_id, movie_title, movie_year, user_id, username, score, review_text, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (movie_id, movie_title, movie_year, user_id, username, score, review_text, time.time())
            )
            await db.commit()
            return "added"


async def get_random_review() -> Optional[Dict]:
    """Get a random review from all movies"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT movie_id, user_id, username, score, review_text, movie_title, movie_year, timestamp "
            "FROM reviews ORDER BY RANDOM() LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            return {
                "movie_id": str(row["movie_id"]),
                "review": {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "score": row["score"],
                    "review_text": row["review_text"],
                    "movie_title": row["movie_title"],
                    "movie_year": row["movie_year"],
                    "timestamp": row["timestamp"]
                }
            }
        return None


async def get_all_reviews() -> Dict[str, List[Dict]]:
    """Get all reviews grouped by movie_id (for compatibility)"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT movie_id, user_id, username, score, review_text, movie_title, movie_year, timestamp "
            "FROM reviews ORDER BY movie_id, timestamp DESC"
        )
        rows = await cursor.fetchall()

        reviews = {}
        for row in rows:
            movie_key = str(row["movie_id"])
            if movie_key not in reviews:
                reviews[movie_key] = []
            reviews[movie_key].append({
                "user_id": row["user_id"],
                "username": row["username"],
                "score": row["score"],
                "review_text": row["review_text"],
                "movie_title": row["movie_title"],
                "movie_year": row["movie_year"],
                "timestamp": row["timestamp"]
            })
        return reviews


def format_reviewers_text(reviews: List[Dict]) -> str:
    """Format the reviewer names for display"""
    if not reviews:
        return ""

    usernames = [r["username"] for r in reviews]

    if len(usernames) == 1:
        return f"**{usernames[0]}** has reviewed and rated this movie"
    elif len(usernames) == 2:
        return f"**{usernames[0]}** and **{usernames[1]}** have reviewed and rated this movie"
    else:
        all_but_last = ", ".join(f"**{name}**" for name in usernames[:-1])
        return f"{all_but_last}, and **{usernames[-1]}** have reviewed and rated this movie"


# ============== Anime Watchlist Operations ==============

async def get_anime_watchlist(user_id: str, filter_mode: str = "all") -> List[Dict]:
    """
    Get a user's anime watchlist with optional filtering.

    Args:
        user_id: The user's ID
        filter_mode: "all" (default), "unwatched", or "watched"
    """
    db = await get_db()
    async with _lock:
        if filter_mode == "unwatched":
            query = """
                SELECT mal_id, title, title_japanese, episodes, status, score,
                       image_url, year, anime_type, added_at, watched_at
                FROM anime_watchlist WHERE user_id = ? AND watched_at IS NULL
                ORDER BY added_at DESC
            """
        elif filter_mode == "watched":
            query = """
                SELECT mal_id, title, title_japanese, episodes, status, score,
                       image_url, year, anime_type, added_at, watched_at
                FROM anime_watchlist WHERE user_id = ? AND watched_at IS NOT NULL
                ORDER BY watched_at DESC
            """
        else:  # "all"
            query = """
                SELECT mal_id, title, title_japanese, episodes, status, score,
                       image_url, year, anime_type, added_at, watched_at
                FROM anime_watchlist WHERE user_id = ?
                ORDER BY added_at DESC
            """

        cursor = await db.execute(query, (user_id,))
        rows = await cursor.fetchall()
        return [
            {
                "mal_id": row["mal_id"],
                "title": row["title"],
                "title_japanese": row["title_japanese"],
                "episodes": row["episodes"],
                "status": row["status"],
                "score": row["score"],
                "image_url": row["image_url"],
                "year": row["year"],
                "type": row["anime_type"],
                "added_at": row["added_at"],
                "watched_at": row["watched_at"]
            }
            for row in rows
        ]


async def get_anime_watchlist_counts(user_id: str) -> Dict[str, int]:
    """Get counts of total, watched, and unwatched anime."""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN watched_at IS NOT NULL THEN 1 ELSE 0 END) as watched,
                SUM(CASE WHEN watched_at IS NULL THEN 1 ELSE 0 END) as unwatched
            FROM anime_watchlist WHERE user_id = ?
            """,
            (user_id,)
        )
        row = await cursor.fetchone()
        return {
            "total": row["total"] or 0,
            "watched": row["watched"] or 0,
            "unwatched": row["unwatched"] or 0
        }


async def add_to_anime_watchlist(user_id: str, anime: Dict) -> bool:
    """Add an anime to user's watchlist. Returns False if already exists."""
    db = await get_db()
    async with _lock:
        try:
            import time
            await db.execute(
                """INSERT INTO anime_watchlist
                   (user_id, mal_id, title, title_japanese, episodes, status,
                    score, image_url, year, anime_type, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    anime.get("mal_id"),
                    anime.get("title"),
                    anime.get("title_japanese"),
                    anime.get("episodes"),
                    anime.get("status"),
                    anime.get("score"),
                    anime.get("image_url"),
                    anime.get("year"),
                    anime.get("type"),
                    time.time()
                )
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_from_anime_watchlist(user_id: str, mal_id: int) -> bool:
    """Remove an anime from user's watchlist. Returns True if removed."""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "DELETE FROM anime_watchlist WHERE user_id = ? AND mal_id = ?",
            (user_id, mal_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def is_in_anime_watchlist(user_id: str, mal_id: int) -> bool:
    """Check if an anime is in user's watchlist"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT 1 FROM anime_watchlist WHERE user_id = ? AND mal_id = ?",
            (user_id, mal_id)
        )
        return await cursor.fetchone() is not None


async def get_anime_watchlist_entry(user_id: str, mal_id: int) -> Optional[Dict]:
    """Get a specific anime from user's watchlist"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            """SELECT mal_id, title, title_japanese, episodes, status, score,
                      image_url, year, anime_type, added_at, watched_at
               FROM anime_watchlist WHERE user_id = ? AND mal_id = ?""",
            (user_id, mal_id)
        )
        row = await cursor.fetchone()
        if row:
            return {
                "mal_id": row["mal_id"],
                "title": row["title"],
                "title_japanese": row["title_japanese"],
                "episodes": row["episodes"],
                "status": row["status"],
                "score": row["score"],
                "image_url": row["image_url"],
                "year": row["year"],
                "type": row["anime_type"],
                "added_at": row["added_at"],
                "watched_at": row["watched_at"]
            }
        return None


async def mark_anime_as_watched(user_id: str, mal_id: int, anime: Optional[Dict] = None) -> str:
    """
    Mark an anime as watched. If not in watchlist, adds it first.

    Returns:
        "marked" if already in watchlist and marked
        "added_and_marked" if added to watchlist and marked
        "already_watched" if already marked as watched
        "error" if anime data required but not provided
    """
    import time
    db = await get_db()
    async with _lock:
        # Check if anime is already in watchlist
        cursor = await db.execute(
            "SELECT watched_at FROM anime_watchlist WHERE user_id = ? AND mal_id = ?",
            (user_id, mal_id)
        )
        row = await cursor.fetchone()

        if row:
            # Anime exists in watchlist
            if row["watched_at"] is not None:
                return "already_watched"
            # Mark as watched
            await db.execute(
                "UPDATE anime_watchlist SET watched_at = ? WHERE user_id = ? AND mal_id = ?",
                (time.time(), user_id, mal_id)
            )
            await db.commit()
            return "marked"
        else:
            # Anime not in watchlist - need to add it
            if not anime:
                return "error"
            await db.execute(
                """INSERT INTO anime_watchlist
                   (user_id, mal_id, title, title_japanese, episodes, status,
                    score, image_url, year, anime_type, added_at, watched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    anime.get("mal_id"),
                    anime.get("title"),
                    anime.get("title_japanese"),
                    anime.get("episodes"),
                    anime.get("status"),
                    anime.get("score"),
                    anime.get("image_url"),
                    anime.get("year"),
                    anime.get("type"),
                    time.time(),
                    time.time()
                )
            )
            await db.commit()
            return "added_and_marked"


async def mark_anime_as_unwatched(user_id: str, mal_id: int) -> bool:
    """Mark an anime as unwatched. Returns True if updated."""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "UPDATE anime_watchlist SET watched_at = NULL WHERE user_id = ? AND mal_id = ?",
            (user_id, mal_id)
        )
        await db.commit()
        return cursor.rowcount > 0


# ============== Anime Review Operations ==============

async def get_anime_reviews(mal_id: int) -> List[Dict]:
    """Get all reviews for an anime"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT user_id, username, score, review_text, anime_title, timestamp "
            "FROM anime_reviews WHERE mal_id = ? ORDER BY timestamp DESC",
            (mal_id,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "score": row["score"],
                "review_text": row["review_text"],
                "anime_title": row["anime_title"],
                "timestamp": row["timestamp"]
            }
            for row in rows
        ]


async def add_anime_review(mal_id: int, anime_title: str,
                           user_id: str, username: str, score: float, review_text: str) -> str:
    """Add or update an anime review. Returns 'added' or 'updated'."""
    db = await get_db()
    async with _lock:
        import time
        # Check if review exists
        cursor = await db.execute(
            "SELECT 1 FROM anime_reviews WHERE mal_id = ? AND user_id = ?",
            (mal_id, user_id)
        )
        exists = await cursor.fetchone() is not None

        if exists:
            await db.execute(
                "UPDATE anime_reviews SET username = ?, score = ?, review_text = ?, "
                "anime_title = ?, timestamp = ? "
                "WHERE mal_id = ? AND user_id = ?",
                (username, score, review_text, anime_title, time.time(), mal_id, user_id)
            )
            await db.commit()
            return "updated"
        else:
            await db.execute(
                "INSERT INTO anime_reviews (mal_id, anime_title, user_id, username, score, review_text, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mal_id, anime_title, user_id, username, score, review_text, time.time())
            )
            await db.commit()
            return "added"


async def get_random_anime_review() -> Optional[Dict]:
    """Get a random anime review"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT mal_id, user_id, username, score, review_text, anime_title, timestamp "
            "FROM anime_reviews ORDER BY RANDOM() LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            return {
                "mal_id": row["mal_id"],
                "review": {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "score": row["score"],
                    "review_text": row["review_text"],
                    "anime_title": row["anime_title"],
                    "timestamp": row["timestamp"]
                }
            }
        return None


def format_anime_reviewers_text(reviews: List[Dict]) -> str:
    """Format the reviewer names for display"""
    if not reviews:
        return ""

    usernames = [r["username"] for r in reviews]

    if len(usernames) == 1:
        return f"**{usernames[0]}** has reviewed this anime"
    elif len(usernames) == 2:
        return f"**{usernames[0]}** and **{usernames[1]}** have reviewed this anime"
    else:
        all_but_last = ", ".join(f"**{name}**" for name in usernames[:-1])
        return f"{all_but_last}, and **{usernames[-1]}** have reviewed this anime"
