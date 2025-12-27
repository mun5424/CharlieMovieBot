#!/usr/bin/env python3
"""
Migration script: JSON to SQLite

Migrates existing movie_data.json to SQLite database.
Run this once before switching to SQLite-based storage.

Usage:
    python scripts/migrate_to_sqlite.py
"""

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATA_FILE
from db.connection import get_db, close_db, DB_FILE
from db import movies as sqlite_store


async def migrate():
    """Migrate JSON data to SQLite"""
    print("=" * 50)
    print("CharlieMovieBot: JSON to SQLite Migration")
    print("=" * 50)
    print()

    # Check if JSON file exists
    if not os.path.exists(DATA_FILE):
        print(f"No JSON file found at: {DATA_FILE}")
        print("Nothing to migrate. Creating empty database...")
        await get_db()
        print("Done!")
        return

    # Load JSON data
    print(f"Loading JSON data from: {DATA_FILE}")
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)

    # Initialize SQLite database
    print(f"Initializing SQLite database: {DB_FILE}")
    db = await get_db()

    # Count items to migrate
    user_count = 0
    watchlist_count = 0
    watched_count = 0
    pending_count = 0
    review_count = 0

    # Migrate user data (watchlist, watched, pending)
    print()
    print("Migrating user data...")

    for key, value in data.items():
        # Skip the reviews key (handled separately)
        if key == "reviews":
            continue

        # Skip non-dict values
        if not isinstance(value, dict):
            continue

        user_id = key
        user_count += 1

        # Migrate watchlist
        watchlist = value.get("watchlist", [])
        for movie in watchlist:
            if isinstance(movie, dict) and movie.get("id"):
                try:
                    await sqlite_store.add_to_watchlist(user_id, movie)
                    watchlist_count += 1
                except Exception as e:
                    print(f"  Warning: Could not add watchlist item: {e}")

        # Migrate watched
        watched = value.get("watched", [])
        for movie in watched:
            if isinstance(movie, dict) and movie.get("id"):
                try:
                    await sqlite_store.add_to_watched(user_id, movie)
                    watched_count += 1
                except Exception as e:
                    print(f"  Warning: Could not add watched item: {e}")

        # Migrate pending suggestions
        pending = value.get("pending", [])
        for suggestion in pending:
            if isinstance(suggestion, dict) and suggestion.get("movie"):
                try:
                    movie = suggestion.get("movie", {})
                    await sqlite_store.add_pending_suggestion(
                        user_id,
                        suggestion.get("from_user_id", ""),
                        suggestion.get("from_username", "Unknown"),
                        movie
                    )
                    pending_count += 1
                except Exception as e:
                    print(f"  Warning: Could not add pending item: {e}")

    print(f"  Users: {user_count}")
    print(f"  Watchlist items: {watchlist_count}")
    print(f"  Watched items: {watched_count}")
    print(f"  Pending suggestions: {pending_count}")

    # Migrate reviews
    print()
    print("Migrating reviews...")

    reviews = data.get("reviews", {})
    for movie_id, movie_reviews in reviews.items():
        if not isinstance(movie_reviews, list):
            continue

        for review in movie_reviews:
            if not isinstance(review, dict):
                continue

            try:
                await sqlite_store.add_movie_review(
                    movie_id=int(movie_id),
                    movie_title=review.get("movie_title", "Unknown"),
                    movie_year=review.get("movie_year", "Unknown"),
                    user_id=review.get("user_id", ""),
                    username=review.get("username", "Unknown"),
                    score=review.get("score", 0),
                    review_text=review.get("review_text", "")
                )
                review_count += 1
            except Exception as e:
                print(f"  Warning: Could not add review: {e}")

    print(f"  Reviews: {review_count}")

    # Backup JSON file
    print()
    backup_file = DATA_FILE + f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"Backing up JSON file to: {backup_file}")
    shutil.copy2(DATA_FILE, backup_file)

    # Close database
    await close_db()

    # Summary
    print()
    print("=" * 50)
    print("Migration Complete!")
    print("=" * 50)
    print()
    print("Summary:")
    print(f"  - Users migrated: {user_count}")
    print(f"  - Watchlist items: {watchlist_count}")
    print(f"  - Watched items: {watched_count}")
    print(f"  - Pending suggestions: {pending_count}")
    print(f"  - Reviews: {review_count}")
    print()
    print(f"SQLite database: {DB_FILE}")
    print(f"JSON backup: {backup_file}")
    print()
    print("You can now restart the bot to use SQLite storage.")
    print("The original JSON file has been preserved as a backup.")


if __name__ == "__main__":
    asyncio.run(migrate())
