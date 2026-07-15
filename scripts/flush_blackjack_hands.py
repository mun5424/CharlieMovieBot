#!/usr/bin/env python3
"""
List and flush stuck/unresolved blackjack hands from blackjack_active_games.

A hand normally clears itself out of blackjack_active_games once it's
finished and its table message has been updated. If that cleanup never runs
(e.g. the table message got deleted so the bot has nothing to update - see
handle_shortcut_action in blackjack/cog.py) the row lingers and can block
that player from starting a new /blackjack hand.

Any bet still staked on a non-finished hand (and any unresolved insurance
bet) is refunded to the player's wallet before the row is deleted, the same
way blackjack/cog.py refunds an unrestorable hand. Already-finished hands
have no money left in them (they were paid out when they finished), so they
are just deleted.

Usage:
    python scripts/flush_blackjack_hands.py                 # list only, no changes
    python scripts/flush_blackjack_hands.py --flush          # flush every stuck hand
    python scripts/flush_blackjack_hands.py --flush <user_id> # flush just one user

After flushing, restart the bot (./restart_bot.sh) so it drops any copy of
the flushed hand it's still holding in memory.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blackjack.db import BlackjackDB
from blackjack.renderer import money
import json
import sqlite3

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot.db")


def load_rows(db_path: str) -> list[tuple[int, int, dict, str]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, message_id, state_json, updated_at FROM blackjack_active_games"
        ).fetchall()
    return [(int(r[0]), int(r[1]), json.loads(r[2]), str(r[3])) for r in rows]


def staked_cents(state: dict) -> int:
    try:
        staked = sum(int(hand.get("bet_cents", 0)) for hand in state.get("hands", []))
        staked += int(state.get("insurance_bet_cents", 0))
        if staked <= 0:
            staked = int(state.get("bet_cents", 0) or 0)
        return staked
    except Exception:
        return int(state.get("bet_cents", 0) or 0)


async def flush(db_path: str, only_user_id: int | None, dry_run: bool) -> None:
    rows = load_rows(db_path)
    if only_user_id is not None:
        rows = [r for r in rows if r[0] == only_user_id]

    if not rows:
        print("No active/unresolved blackjack hands found.")
        return

    db = BlackjackDB(db_path)
    await db.init()

    for user_id, message_id, state, updated_at in rows:
        phase = state.get("phase", "?")
        finished = state.get("settled") or phase == "finished"
        stake = 0 if finished else staked_cents(state)

        print(f"user_id={user_id} phase={phase} message_id={message_id} last_updated={updated_at}")
        if stake:
            print(f"  -> unresolved hand, {money(stake)} still staked")
        else:
            print("  -> already settled, safe to clear")

        if dry_run:
            continue

        if stake:
            balance_after = await db.add_balance(user_id, stake, "blackjack_manual_flush_refund")
            print(f"  refunded {money(stake)} (new balance: {money(balance_after)})")

        await db.delete_active_game(user_id)
        print("  cleared.")

    if dry_run:
        print("\nDry run only - nothing was changed. Re-run with --flush to actually clear these.")
    else:
        print(
            "\nDone. If charlie-bot is currently running, restart it (./restart_bot.sh) so it "
            "drops any in-memory copy of these hands too."
        )


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--flush" not in args
    args = [a for a in args if a != "--flush"]

    only_user_id = None
    if args:
        try:
            only_user_id = int(args[0])
        except ValueError:
            raise SystemExit(f"Invalid user_id: {args[0]!r}. Must be an integer Discord user ID.")

    asyncio.run(flush(DB_FILE, only_user_id, dry_run))


if __name__ == "__main__":
    main()
