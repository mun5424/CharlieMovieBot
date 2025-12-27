"""
Movie-related database operations
"""

import aiosqlite
import json
import time
from typing import Optional, List, Dict

from db.connection import get_db, get_lock


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
    _lock = get_lock()
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
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
        try:
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
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "SELECT 1 FROM watchlist WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        return await cursor.fetchone() is not None


async def get_watchlist_movie(user_id: str, movie_id: int) -> Optional[Dict]:
    """Get a specific movie from user's watchlist"""
    db = await get_db()
    _lock = get_lock()
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
    db = await get_db()
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "UPDATE watchlist SET watched_at = NULL WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        await db.commit()
        return cursor.rowcount > 0


# ============== Legacy Watched Operations ==============

async def get_user_watched(user_id: str) -> List[Dict]:
    """Get a user's watched list (legacy table)"""
    db = await get_db()
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
        try:
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
    _lock = get_lock()
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
    _lock = get_lock()
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
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
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
    _lock = get_lock()
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
    _lock = get_lock()
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
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
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
    _lock = get_lock()
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
    _lock = get_lock()
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
