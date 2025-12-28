"""
SQLite database connection management
"""

import aiosqlite
import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Database file path
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "movie_data.db")

# Connection pool
_db: Optional[aiosqlite.Connection] = None
_lock = asyncio.Lock()


def get_lock():
    """Get the database lock for use in other modules"""
    return _lock


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

        -- Game log (backlog + played status)
        CREATE TABLE IF NOT EXISTS gamelog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            igdb_id INTEGER NOT NULL,
            name TEXT,
            cover_url TEXT,
            release_date INTEGER,
            platforms TEXT,
            genres TEXT,
            developer TEXT,
            summary TEXT,
            added_at REAL,
            played_at REAL,
            UNIQUE(user_id, igdb_id)
        );

        -- Game reviews
        CREATE TABLE IF NOT EXISTS game_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            igdb_id INTEGER NOT NULL,
            game_name TEXT,
            user_id TEXT NOT NULL,
            username TEXT,
            score REAL,
            review_text TEXT,
            timestamp REAL,
            UNIQUE(igdb_id, user_id)
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
        CREATE INDEX IF NOT EXISTS idx_gamelog_user ON gamelog(user_id);
        CREATE INDEX IF NOT EXISTS idx_gamelog_played ON gamelog(user_id, played_at);
        CREATE INDEX IF NOT EXISTS idx_game_reviews_igdb ON game_reviews(igdb_id);
    """)

    # Add watched_at column if it doesn't exist (for existing databases)
    try:
        await db.execute("ALTER TABLE watchlist ADD COLUMN watched_at REAL")
        await db.commit()
        logger.info("Added watched_at column to watchlist table")
    except Exception:
        pass  # Column already exists

    await db.commit()
