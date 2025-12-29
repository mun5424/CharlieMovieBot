"""
Anime-related database operations
"""

import aiosqlite
import time
from typing import Optional, List, Dict

from db.connection import get_db, get_lock


# ============== Anime Watchlist Operations ==============

async def get_anime_watchlist(user_id: str, filter_mode: str = "all") -> List[Dict]:
    """
    Get a user's anime watchlist with optional filtering.

    Args:
        user_id: The user's ID
        filter_mode: "all" (default), "unwatched", or "watched"
    """
    db = await get_db()
    _lock = get_lock()
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
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
        try:
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
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "SELECT 1 FROM anime_watchlist WHERE user_id = ? AND mal_id = ?",
            (user_id, mal_id)
        )
        return await cursor.fetchone() is not None


async def get_anime_watchlist_entry(user_id: str, mal_id: int) -> Optional[Dict]:
    """Get a specific anime from user's watchlist"""
    db = await get_db()
    _lock = get_lock()
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
    db = await get_db()
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "UPDATE anime_watchlist SET watched_at = NULL WHERE user_id = ? AND mal_id = ?",
            (user_id, mal_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def batch_import_anime(user_id: str, anime_list: List[Dict]) -> Dict[str, int]:
    """
    Batch import anime to user's watchlist efficiently.

    Args:
        user_id: The user's ID
        anime_list: List of anime dicts with mal_id, title, episodes, etc.
                   Each dict can have 'mark_watched': True to mark as watched

    Returns:
        Dict with counts: {"added": X, "skipped": Y, "watched": Z}
    """
    db = await get_db()
    _lock = get_lock()

    added = 0
    skipped = 0
    watched = 0
    now = time.time()

    async with _lock:
        # Get existing mal_ids for this user in one query
        cursor = await db.execute(
            "SELECT mal_id FROM anime_watchlist WHERE user_id = ?",
            (user_id,)
        )
        rows = await cursor.fetchall()
        existing_ids = {row["mal_id"] for row in rows}

        # Prepare batch inserts
        for anime in anime_list:
            mal_id = anime.get("mal_id")
            if not mal_id or mal_id in existing_ids:
                skipped += 1
                continue

            mark_watched = anime.get("mark_watched", False)
            watched_at = now if mark_watched else None

            try:
                await db.execute(
                    """INSERT INTO anime_watchlist
                       (user_id, mal_id, title, title_japanese, episodes, status,
                        score, image_url, year, anime_type, added_at, watched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        mal_id,
                        anime.get("title"),
                        anime.get("title_japanese"),
                        anime.get("episodes"),
                        anime.get("status"),
                        anime.get("score"),
                        anime.get("image_url"),
                        anime.get("year"),
                        anime.get("type"),
                        now,
                        watched_at
                    )
                )
                added += 1
                if mark_watched:
                    watched += 1
            except Exception:
                skipped += 1

        # Single commit for all inserts
        await db.commit()

    return {"added": added, "skipped": skipped, "watched": watched}


# ============== Anime Review Operations ==============

async def get_anime_reviews(mal_id: int) -> List[Dict]:
    """Get all reviews for an anime"""
    db = await get_db()
    _lock = get_lock()
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
    _lock = get_lock()
    async with _lock:
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
    _lock = get_lock()
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
