from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS blackjack_wallets (
    user_id INTEGER PRIMARY KEY,
    balance_cents INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blackjack_daily_bonus (
    user_id INTEGER NOT NULL,
    claim_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, claim_date)
);

CREATE TABLE IF NOT EXISTS blackjack_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    reason TEXT NOT NULL,
    balance_after_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blackjack_shoes (
    user_id INTEGER PRIMARY KEY,
    deck_json TEXT NOT NULL,
    discard_json TEXT NOT NULL,
    hands_played INTEGER NOT NULL DEFAULT 0,
    last_shuffle_reason TEXT NOT NULL DEFAULT 'new shoe',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blackjack_active_games (
    user_id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

class BlackjackDB:
    def __init__(self, path: str | Path):
        self.path = str(path)

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    async def get_balance(self, user_id: int) -> int:
        return await asyncio.to_thread(self._get_balance_sync, user_id)

    def _get_balance_sync(self, user_id: int) -> int:
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT OR IGNORE INTO blackjack_wallets(user_id, balance_cents) VALUES (?, 0)", (user_id,))
            row = conn.execute("SELECT balance_cents FROM blackjack_wallets WHERE user_id = ?", (user_id,)).fetchone()
            conn.commit()
            return int(row[0])

    async def add_balance(self, user_id: int, delta_cents: int, reason: str = "adjustment") -> int:
        return await asyncio.to_thread(self._add_balance_sync, user_id, delta_cents, reason)

    def _add_balance_sync(self, user_id: int, delta_cents: int, reason: str) -> int:
        with sqlite3.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                "INSERT OR IGNORE INTO blackjack_wallets(user_id, balance_cents) VALUES (?, 0)",
                (user_id,),
            )

            conn.execute(
                """
                UPDATE blackjack_wallets
                SET balance_cents = balance_cents + ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (delta_cents, user_id),
            )

            row = conn.execute(
                "SELECT balance_cents FROM blackjack_wallets WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            balance_after = int(row[0])

            conn.execute(
                """
                INSERT INTO blackjack_transactions(user_id, amount_cents, reason, balance_after_cents)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, delta_cents, reason, balance_after),
            )

            conn.commit()
            return balance_after

    async def try_claim_daily_bonus(
            self, 
            user_id: int, 
            claim_date: str,
            amount_cents: int, 
            reason: str = "blackjack_daily_bonus",) -> bool:
        return await asyncio.to_thread(
            self._try_claim_daily_bonus_sync,
            user_id,
            claim_date,
            amount_cents,
            reason,
        )

    def _try_claim_daily_bonus_sync(
    self,
    user_id: int,
    claim_date: str,
    amount_cents: int,
    reason: str,
) -> bool:
        with sqlite3.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                """
                INSERT OR IGNORE INTO blackjack_wallets(user_id, balance_cents)
                VALUES (?, 0)
                """,
                (user_id,),
            )

            try:
                conn.execute(
                    """
                    INSERT INTO blackjack_daily_bonus(user_id, claim_date, amount_cents)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, claim_date, amount_cents),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                return False

            conn.execute(
                """
                UPDATE blackjack_wallets
                SET balance_cents = balance_cents + ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (amount_cents, user_id),
            )

            row = conn.execute(
                """
                SELECT balance_cents
                FROM blackjack_wallets
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

            balance_after_cents = int(row[0])

            conn.execute(
                """
                INSERT INTO blackjack_transactions(
                    user_id,
                    amount_cents,
                    reason,
                    balance_after_cents
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    amount_cents,
                    reason,
                    balance_after_cents,
                ),
            )

            conn.commit()
            return True

    async def save_active_game(self, user_id: int, message_id: int, state: dict) -> None:
        await asyncio.to_thread(self._save_active_game_sync, user_id, message_id, state)

    def _save_active_game_sync(self, user_id: int, message_id: int, state: dict) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO blackjack_active_games(user_id, message_id, state_json, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    message_id = excluded.message_id,
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, message_id, json.dumps(state)),
            )
            conn.commit()

    async def get_active_game(self, user_id: int) -> dict | None:
        return await asyncio.to_thread(self._get_active_game_sync, user_id)

    def _get_active_game_sync(self, user_id: int) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                """
                SELECT state_json
                FROM blackjack_active_games
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

        if row is None:
            return None
        return json.loads(row[0])

    async def delete_active_game(self, user_id: int) -> None:
        await asyncio.to_thread(self._delete_active_game_sync, user_id)

    def _delete_active_game_sync(self, user_id: int) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM blackjack_active_games WHERE user_id = ?", (user_id,))
            conn.commit()

    async def get_shoe_state(self, user_id: int) -> dict | None:
        return await asyncio.to_thread(self._get_shoe_state_sync, user_id)

    def _get_shoe_state_sync(self, user_id: int) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                """
                SELECT deck_json, discard_json, hands_played, last_shuffle_reason
                FROM blackjack_shoes
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

        if row is None:
            return None

        return {
            "deck": json.loads(row[0]),
            "discard": json.loads(row[1]),
            "hands_played": int(row[2]),
            "last_shuffle_reason": str(row[3]),
        }

    async def save_shoe_state(
        self,
        user_id: int,
        deck_codes: list[str],
        discard_codes: list[str],
        hands_played: int,
        last_shuffle_reason: str,
    ) -> None:
        await asyncio.to_thread(
            self._save_shoe_state_sync,
            user_id,
            deck_codes,
            discard_codes,
            hands_played,
            last_shuffle_reason,
        )

    def _save_shoe_state_sync(
        self,
        user_id: int,
        deck_codes: list[str],
        discard_codes: list[str],
        hands_played: int,
        last_shuffle_reason: str,
    ) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO blackjack_shoes(
                    user_id, deck_json, discard_json, hands_played, last_shuffle_reason, updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    deck_json = excluded.deck_json,
                    discard_json = excluded.discard_json,
                    hands_played = excluded.hands_played,
                    last_shuffle_reason = excluded.last_shuffle_reason,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    json.dumps(deck_codes),
                    json.dumps(discard_codes),
                    hands_played,
                    last_shuffle_reason,
                ),
            )
            conn.commit()
