#!/usr/bin/env python3
"""
Migration script: Merge watched table into watchlist

This script merges all entries from the 'watched' table into the 'watchlist' table,
setting the watched_at timestamp. Movies already in both tables will have their
watchlist entry updated with the watched_at timestamp.

Run this once after updating to the unified watchlist system.

Usage:
    python scripts/migrate_watched_to_watchlist.py
"""

import asyncio
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "movie_data.db")


async def migrate():
    """Merge watched table into watchlist table"""
    print("=" * 50)
    print("Merge Watched into Watchlist Migration")
    print("=" * 50)
    print()

    if not os.path.exists(DB_FILE):
        print(f"Database not found: {DB_FILE}")
        print("Nothing to migrate.")
        return

    db = await aiosqlite.connect(DB_FILE)
    db.row_factory = aiosqlite.Row

    try:
        # First, ensure watchlist table has watched_at column
        print("Ensuring watched_at column exists...")
        try:
            await db.execute("ALTER TABLE watchlist ADD COLUMN watched_at REAL")
            await db.commit()
            print("  Added watched_at column to watchlist table")
        except Exception:
            print("  watched_at column already exists")

        # Get counts before migration
        cursor = await db.execute("SELECT COUNT(*) as count FROM watched")
        watched_count = (await cursor.fetchone())["count"]
        print(f"\nFound {watched_count} entries in watched table")

        if watched_count == 0:
            print("Nothing to migrate.")
            return

        # Get all watched entries
        cursor = await db.execute("""
            SELECT user_id, movie_id, title, year, overview, rating, poster_path, watched_at
            FROM watched
        """)
        watched_entries = await cursor.fetchall()

        migrated = 0
        updated = 0
        skipped = 0

        for entry in watched_entries:
            user_id = entry["user_id"]
            movie_id = entry["movie_id"]
            watched_at = entry["watched_at"]

            # Check if movie already exists in watchlist
            cursor = await db.execute(
                "SELECT id, watched_at FROM watchlist WHERE user_id = ? AND movie_id = ?",
                (user_id, movie_id)
            )
            existing = await cursor.fetchone()

            if existing:
                # Movie already in watchlist - update watched_at if not set
                if existing["watched_at"] is None:
                    await db.execute(
                        "UPDATE watchlist SET watched_at = ? WHERE id = ?",
                        (watched_at, existing["id"])
                    )
                    updated += 1
                else:
                    skipped += 1  # Already has watched_at
            else:
                # Movie not in watchlist - insert it
                await db.execute(
                    """INSERT INTO watchlist
                       (user_id, movie_id, title, year, overview, rating, poster_path, added_at, watched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        movie_id,
                        entry["title"],
                        entry["year"],
                        entry["overview"],
                        entry["rating"],
                        entry["poster_path"],
                        watched_at,  # Use watched_at as added_at since that's when they interacted
                        watched_at
                    )
                )
                migrated += 1

        await db.commit()

        # Summary
        print()
        print("=" * 50)
        print("Migration Complete!")
        print("=" * 50)
        print()
        print("Summary:")
        print(f"  - Newly added to watchlist: {migrated}")
        print(f"  - Updated existing entries: {updated}")
        print(f"  - Already migrated (skipped): {skipped}")
        print()

        # Verify
        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM watchlist WHERE watched_at IS NOT NULL"
        )
        total_watched = (await cursor.fetchone())["count"]
        print(f"Total watched movies in unified watchlist: {total_watched}")
        print()

        # Ask about deleting watched table
        print("The 'watched' table is no longer needed.")
        print("You can safely delete it with:")
        print("  DROP TABLE watched;")
        print()
        print("Or keep it as a backup. The bot will no longer use it.")

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(migrate())
