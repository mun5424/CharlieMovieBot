#!/usr/bin/env python3
"""
Give (credit) a user's blackjack balance a given dollar amount.

Usage:
    python scripts/charge_blackjack_user.py <user_id> <amount>

Example:
    python scripts/charge_blackjack_user.py 175365910519873538 10.50

Uses the same BlackjackDB.add_balance() path the bot itself uses, so the
credit is recorded in blackjack_transactions like any other balance change.
"""

import asyncio
import os
import sys
from decimal import Decimal, InvalidOperation

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blackjack.db import BlackjackDB
from blackjack.renderer import money

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot.db")


async def give_user(user_id: int, amount_cents: int, db_path: str = DB_FILE) -> None:
    db = BlackjackDB(db_path)
    await db.init()

    balance_before = await db.get_balance(user_id)
    balance_after = await db.add_balance(user_id, amount_cents, "manual_admin_credit")

    print(f"User {user_id}")
    print(f"  Balance before: {money(balance_before)}")
    print(f"  Given:          {money(amount_cents)}")
    print(f"  Balance after:  {money(balance_after)}")


def parse_amount_cents(amount_str: str) -> int:
    try:
        dollars = Decimal(amount_str)
    except InvalidOperation:
        raise SystemExit(f"Invalid amount: {amount_str!r}. Use a dollar amount like 10 or 10.50.")

    if dollars <= 0:
        raise SystemExit("Amount to give must be greater than 0.")

    return int((dollars * 100).to_integral_value())


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)

    try:
        user_id = int(sys.argv[1])
    except ValueError:
        raise SystemExit(f"Invalid user_id: {sys.argv[1]!r}. Must be an integer Discord user ID.")

    amount_cents = parse_amount_cents(sys.argv[2])

    asyncio.run(give_user(user_id, amount_cents))


if __name__ == "__main__":
    main()
