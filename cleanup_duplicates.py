#!/usr/bin/env python3
"""
Cleanup script to remove duplicate movies from user watchlists.
Run this once to fix any existing duplicates in your data.
"""

import json
from config import DATA_FILE


def dedupe_by_id(movie_list):
    """Remove duplicate movies, keeping the first occurrence of each ID."""
    seen_ids = set()
    unique = []
    for movie in movie_list:
        movie_id = movie.get("id")
        if movie_id and movie_id not in seen_ids:
            seen_ids.add(movie_id)
            unique.append(movie)
    return unique


def dedupe_pending_by_id(pending_list):
    """Remove duplicate pending suggestions, keeping the first occurrence of each movie ID."""
    seen_ids = set()
    unique = []
    for suggestion in pending_list:
        movie_id = suggestion.get("movie", {}).get("id")
        if movie_id and movie_id not in seen_ids:
            seen_ids.add(movie_id)
            unique.append(suggestion)
    return unique


def main():
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Data file '{DATA_FILE}' not found.")
        return

    total_removed = 0

    for user_id, user_data in data.items():
        # Dedupe watchlist
        if "watchlist" in user_data:
            original_count = len(user_data["watchlist"])
            user_data["watchlist"] = dedupe_by_id(user_data["watchlist"])
            removed = original_count - len(user_data["watchlist"])
            if removed > 0:
                print(f"User {user_id}: Removed {removed} duplicate(s) from watchlist")
                total_removed += removed

        # Dedupe watched
        if "watched" in user_data:
            original_count = len(user_data["watched"])
            user_data["watched"] = dedupe_by_id(user_data["watched"])
            removed = original_count - len(user_data["watched"])
            if removed > 0:
                print(f"User {user_id}: Removed {removed} duplicate(s) from watched")
                total_removed += removed

        # Dedupe pending
        if "pending" in user_data:
            original_count = len(user_data["pending"])
            user_data["pending"] = dedupe_pending_by_id(user_data["pending"])
            removed = original_count - len(user_data["pending"])
            if removed > 0:
                print(f"User {user_id}: Removed {removed} duplicate(s) from pending")
                total_removed += removed

    if total_removed > 0:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print(f"\nTotal: Removed {total_removed} duplicate(s). Data saved.")
    else:
        print("No duplicates found.")


if __name__ == "__main__":
    main()
