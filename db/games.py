"""
Game-related database operations for gamelog feature
"""

import aiosqlite
import json
import time
from typing import Optional, List, Dict

from db.connection import get_db, get_lock


# ============== Gamelog Operations ==============

async def get_gamelog(user_id: str, filter_mode: str = "all") -> List[Dict]:
    """
    Get a user's game log with optional filtering.

    Args:
        user_id: The user's ID
        filter_mode: "all" (default), "backlog" (not played), or "played"
    """
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        if filter_mode == "backlog":
            query = """
                SELECT igdb_id, name, cover_url, release_date, platforms,
                       genres, developer, summary, added_at, played_at
                FROM gamelog WHERE user_id = ? AND played_at IS NULL
                ORDER BY added_at DESC
            """
        elif filter_mode == "played":
            query = """
                SELECT igdb_id, name, cover_url, release_date, platforms,
                       genres, developer, summary, added_at, played_at
                FROM gamelog WHERE user_id = ? AND played_at IS NOT NULL
                ORDER BY played_at DESC
            """
        else:  # "all"
            query = """
                SELECT igdb_id, name, cover_url, release_date, platforms,
                       genres, developer, summary, added_at, played_at
                FROM gamelog WHERE user_id = ?
                ORDER BY added_at DESC
            """

        cursor = await db.execute(query, (user_id,))
        rows = await cursor.fetchall()
        return [
            {
                "igdb_id": row["igdb_id"],
                "name": row["name"],
                "cover_url": row["cover_url"],
                "release_date": row["release_date"],
                "platforms": json.loads(row["platforms"]) if row["platforms"] else [],
                "genres": json.loads(row["genres"]) if row["genres"] else [],
                "developer": row["developer"],
                "summary": row["summary"],
                "added_at": row["added_at"],
                "played_at": row["played_at"]
            }
            for row in rows
        ]


async def get_gamelog_counts(user_id: str) -> Dict[str, int]:
    """Get counts of total, played, and backlog games."""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN played_at IS NOT NULL THEN 1 ELSE 0 END) as played,
                SUM(CASE WHEN played_at IS NULL THEN 1 ELSE 0 END) as backlog
            FROM gamelog WHERE user_id = ?
            """,
            (user_id,)
        )
        row = await cursor.fetchone()
        return {
            "total": row["total"] or 0,
            "played": row["played"] or 0,
            "backlog": row["backlog"] or 0
        }


async def add_to_gamelog(user_id: str, game: Dict) -> bool:
    """Add a game to user's gamelog. Returns False if already exists."""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        try:
            await db.execute(
                """INSERT INTO gamelog
                   (user_id, igdb_id, name, cover_url, release_date, platforms,
                    genres, developer, summary, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    game.get("id"),
                    game.get("name"),
                    game.get("cover_url"),
                    game.get("release_date"),
                    json.dumps(game.get("platforms", [])),
                    json.dumps(game.get("genres", [])),
                    game.get("developer"),
                    game.get("summary"),
                    time.time()
                )
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_from_gamelog(user_id: str, igdb_id: int) -> bool:
    """Remove a game from user's gamelog. Returns True if removed."""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "DELETE FROM gamelog WHERE user_id = ? AND igdb_id = ?",
            (user_id, igdb_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def is_in_gamelog(user_id: str, igdb_id: int) -> bool:
    """Check if a game is in user's gamelog"""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "SELECT 1 FROM gamelog WHERE user_id = ? AND igdb_id = ?",
            (user_id, igdb_id)
        )
        return await cursor.fetchone() is not None


async def get_gamelog_entry(user_id: str, igdb_id: int) -> Optional[Dict]:
    """Get a specific game from user's gamelog"""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            """SELECT igdb_id, name, cover_url, release_date, platforms,
                      genres, developer, summary, added_at, played_at
               FROM gamelog WHERE user_id = ? AND igdb_id = ?""",
            (user_id, igdb_id)
        )
        row = await cursor.fetchone()
        if row:
            return {
                "igdb_id": row["igdb_id"],
                "name": row["name"],
                "cover_url": row["cover_url"],
                "release_date": row["release_date"],
                "platforms": json.loads(row["platforms"]) if row["platforms"] else [],
                "genres": json.loads(row["genres"]) if row["genres"] else [],
                "developer": row["developer"],
                "summary": row["summary"],
                "added_at": row["added_at"],
                "played_at": row["played_at"]
            }
        return None


async def mark_game_as_played(user_id: str, igdb_id: int, game: Optional[Dict] = None) -> str:
    """
    Mark a game as played. If not in gamelog, adds it first.

    Returns:
        "marked" if already in gamelog and marked
        "added_and_marked" if added to gamelog and marked
        "already_played" if already marked as played
        "error" if game data required but not provided
    """
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        # Check if game is already in gamelog
        cursor = await db.execute(
            "SELECT played_at FROM gamelog WHERE user_id = ? AND igdb_id = ?",
            (user_id, igdb_id)
        )
        row = await cursor.fetchone()

        if row:
            # Game exists in gamelog
            if row["played_at"] is not None:
                return "already_played"
            # Mark as played
            await db.execute(
                "UPDATE gamelog SET played_at = ? WHERE user_id = ? AND igdb_id = ?",
                (time.time(), user_id, igdb_id)
            )
            await db.commit()
            return "marked"
        else:
            # Game not in gamelog - need to add it
            if not game:
                return "error"
            await db.execute(
                """INSERT INTO gamelog
                   (user_id, igdb_id, name, cover_url, release_date, platforms,
                    genres, developer, summary, added_at, played_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    game.get("id"),
                    game.get("name"),
                    game.get("cover_url"),
                    game.get("release_date"),
                    json.dumps(game.get("platforms", [])),
                    json.dumps(game.get("genres", [])),
                    game.get("developer"),
                    game.get("summary"),
                    time.time(),
                    time.time()
                )
            )
            await db.commit()
            return "added_and_marked"


async def mark_game_as_unplayed(user_id: str, igdb_id: int) -> bool:
    """Mark a game as unplayed (back to backlog). Returns True if updated."""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "UPDATE gamelog SET played_at = NULL WHERE user_id = ? AND igdb_id = ?",
            (user_id, igdb_id)
        )
        await db.commit()
        return cursor.rowcount > 0


# ============== Game Review Operations ==============

async def get_game_reviews(igdb_id: int) -> List[Dict]:
    """Get all reviews for a game"""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "SELECT user_id, username, score, review_text, game_name, timestamp "
            "FROM game_reviews WHERE igdb_id = ? ORDER BY timestamp DESC",
            (igdb_id,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "score": row["score"],
                "review_text": row["review_text"],
                "game_name": row["game_name"],
                "timestamp": row["timestamp"]
            }
            for row in rows
        ]


async def add_game_review(igdb_id: int, game_name: str,
                          user_id: str, username: str, score: float, review_text: str) -> str:
    """Add or update a game review. Returns 'added' or 'updated'."""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        # Check if review exists
        cursor = await db.execute(
            "SELECT 1 FROM game_reviews WHERE igdb_id = ? AND user_id = ?",
            (igdb_id, user_id)
        )
        exists = await cursor.fetchone() is not None

        if exists:
            await db.execute(
                "UPDATE game_reviews SET username = ?, score = ?, review_text = ?, "
                "game_name = ?, timestamp = ? "
                "WHERE igdb_id = ? AND user_id = ?",
                (username, score, review_text, game_name, time.time(), igdb_id, user_id)
            )
            await db.commit()
            return "updated"
        else:
            await db.execute(
                "INSERT INTO game_reviews (igdb_id, game_name, user_id, username, score, review_text, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (igdb_id, game_name, user_id, username, score, review_text, time.time())
            )
            await db.commit()
            return "added"


async def get_random_game_review() -> Optional[Dict]:
    """Get a random game review"""
    db = await get_db()
    _lock = get_lock()
    async with _lock:
        cursor = await db.execute(
            "SELECT igdb_id, user_id, username, score, review_text, game_name, timestamp "
            "FROM game_reviews ORDER BY RANDOM() LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            return {
                "igdb_id": row["igdb_id"],
                "review": {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "score": row["score"],
                    "review_text": row["review_text"],
                    "game_name": row["game_name"],
                    "timestamp": row["timestamp"]
                }
            }
        return None


def format_game_reviewers_text(reviews: List[Dict]) -> str:
    """Format the reviewer names for display"""
    if not reviews:
        return ""

    usernames = [r["username"] for r in reviews]

    if len(usernames) == 1:
        return f"**{usernames[0]}** has reviewed this game"
    elif len(usernames) == 2:
        return f"**{usernames[0]}** and **{usernames[1]}** have reviewed this game"
    else:
        all_but_last = ", ".join(f"**{name}**" for name in usernames[:-1])
        return f"{all_but_last}, and **{usernames[-1]}** have reviewed this game"
