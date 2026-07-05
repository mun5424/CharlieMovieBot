from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
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

CREATE TABLE IF NOT EXISTS blackjack_daily_streak (
    user_id INTEGER PRIMARY KEY,
    current_streak INTEGER NOT NULL DEFAULT 0,
    last_claim_date TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blackjack_stats (
    user_id INTEGER PRIMARY KEY,
    hands_played INTEGER NOT NULL DEFAULT 0,
    hands_won INTEGER NOT NULL DEFAULT 0,
    hands_lost INTEGER NOT NULL DEFAULT 0,
    hands_pushed INTEGER NOT NULL DEFAULT 0,
    blackjacks_hit INTEGER NOT NULL DEFAULT 0,
    busts INTEGER NOT NULL DEFAULT 0,
    busts_prevented INTEGER NOT NULL DEFAULT 0,
    current_win_streak INTEGER NOT NULL DEFAULT 0,
    best_win_streak INTEGER NOT NULL DEFAULT 0,
    total_wagered_cents INTEGER NOT NULL DEFAULT 0,
    total_profit_cents INTEGER NOT NULL DEFAULT 0,
    biggest_win_cents INTEGER NOT NULL DEFAULT 0,
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

    async def claim_daily_bonus(
        self,
        user_id: int,
        claim_date: str,
        *,
        base_cents: int,
        streak_step_cents: int,
        max_cents: int,
        reason: str = "blackjack_daily_bonus",
    ) -> tuple[bool, int, int]:
        """Attempt to claim the daily sign-in bonus for claim_date.

        Returns (claimed, amount_cents_awarded, streak_after_claim). If the
        bonus was already claimed for this date, returns (False, 0, current_streak).
        """
        return await asyncio.to_thread(
            self._claim_daily_bonus_sync,
            user_id,
            claim_date,
            base_cents,
            streak_step_cents,
            max_cents,
            reason,
        )

    def _claim_daily_bonus_sync(
        self,
        user_id: int,
        claim_date: str,
        base_cents: int,
        streak_step_cents: int,
        max_cents: int,
        reason: str,
    ) -> tuple[bool, int, int]:
        yesterday = (
            datetime.strptime(claim_date, "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")

        with sqlite3.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                "INSERT OR IGNORE INTO blackjack_wallets(user_id, balance_cents) VALUES (?, 0)",
                (user_id,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO blackjack_daily_streak(user_id, current_streak, last_claim_date) VALUES (?, 0, '')",
                (user_id,),
            )

            streak_row = conn.execute(
                "SELECT current_streak, last_claim_date FROM blackjack_daily_streak WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current_streak, last_claim_date = int(streak_row[0]), str(streak_row[1])

            new_streak = current_streak + 1 if last_claim_date == yesterday else 1
            amount_cents = min(base_cents + (new_streak - 1) * streak_step_cents, max_cents)

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
                return False, 0, current_streak

            conn.execute(
                """
                UPDATE blackjack_daily_streak
                SET current_streak = ?, last_claim_date = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (new_streak, claim_date, user_id),
            )

            conn.execute(
                """
                UPDATE blackjack_wallets
                SET balance_cents = balance_cents + ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (amount_cents, user_id),
            )

            row = conn.execute(
                "SELECT balance_cents FROM blackjack_wallets WHERE user_id = ?",
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
            return True, amount_cents, new_streak

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

    async def record_round_stats(
        self,
        user_id: int,
        *,
        hand_results: list[str],
        blackjacks_hit: int,
        busts: int,
        busts_prevented: int,
        wagered_cents: int,
        profit_cents: int,
    ) -> None:
        await asyncio.to_thread(
            self._record_round_stats_sync,
            user_id,
            hand_results,
            blackjacks_hit,
            busts,
            busts_prevented,
            wagered_cents,
            profit_cents,
        )

    def _record_round_stats_sync(
        self,
        user_id: int,
        hand_results: list[str],
        blackjacks_hit: int,
        busts: int,
        busts_prevented: int,
        wagered_cents: int,
        profit_cents: int,
    ) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO blackjack_stats(user_id) VALUES (?)", (user_id,))

            row = conn.execute(
                "SELECT current_win_streak, best_win_streak, biggest_win_cents FROM blackjack_stats WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            streak, best_streak, biggest_win = int(row[0]), int(row[1]), int(row[2])

            wins = losses = pushes = 0
            for result in hand_results:
                if result == "win":
                    wins += 1
                    streak += 1
                    best_streak = max(best_streak, streak)
                elif result == "loss":
                    losses += 1
                    streak = 0
                else:
                    pushes += 1

            biggest_win = max(biggest_win, profit_cents)

            conn.execute(
                """
                UPDATE blackjack_stats
                SET hands_played = hands_played + ?,
                    hands_won = hands_won + ?,
                    hands_lost = hands_lost + ?,
                    hands_pushed = hands_pushed + ?,
                    blackjacks_hit = blackjacks_hit + ?,
                    busts = busts + ?,
                    busts_prevented = busts_prevented + ?,
                    current_win_streak = ?,
                    best_win_streak = ?,
                    total_wagered_cents = total_wagered_cents + ?,
                    total_profit_cents = total_profit_cents + ?,
                    biggest_win_cents = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (
                    len(hand_results), wins, losses, pushes,
                    blackjacks_hit, busts, busts_prevented,
                    streak, best_streak,
                    wagered_cents, profit_cents, biggest_win,
                    user_id,
                ),
            )
            conn.commit()

    async def get_stats(self, user_id: int) -> dict:
        return await asyncio.to_thread(self._get_stats_sync, user_id)

    def _get_stats_sync(self, user_id: int) -> dict:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                """
                SELECT hands_played, hands_won, hands_lost, hands_pushed, blackjacks_hit,
                       busts, busts_prevented, current_win_streak, best_win_streak,
                       total_wagered_cents, total_profit_cents, biggest_win_cents
                FROM blackjack_stats
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            streak_row = conn.execute(
                "SELECT current_streak FROM blackjack_daily_streak WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        columns = [
            "hands_played", "hands_won", "hands_lost", "hands_pushed", "blackjacks_hit",
            "busts", "busts_prevented", "current_win_streak", "best_win_streak",
            "total_wagered_cents", "total_profit_cents", "biggest_win_cents",
        ]
        stats = dict(zip(columns, row if row is not None else [0] * len(columns)))
        stats["daily_streak"] = int(streak_row[0]) if streak_row else 0
        return stats

    _LEADERBOARD_QUERIES = {
        "win_streak": """
            SELECT user_id, best_win_streak AS value FROM blackjack_stats
            WHERE best_win_streak > 0 ORDER BY best_win_streak DESC LIMIT ?
        """,
        "busts_prevented": """
            SELECT user_id, busts_prevented AS value FROM blackjack_stats
            WHERE busts_prevented > 0 ORDER BY busts_prevented DESC LIMIT ?
        """,
        "win_pct": """
            SELECT user_id, (100.0 * hands_won / NULLIF(hands_won + hands_lost, 0)) AS value
            FROM blackjack_stats WHERE (hands_won + hands_lost) >= 10
            ORDER BY value DESC LIMIT ?
        """,
        "roi_pct": """
            SELECT user_id, (100.0 * total_profit_cents / NULLIF(total_wagered_cents, 0)) AS value
            FROM blackjack_stats WHERE total_wagered_cents >= 10000
            ORDER BY value DESC LIMIT ?
        """,
        "hands_played": """
            SELECT user_id, hands_played AS value FROM blackjack_stats
            WHERE hands_played > 0 ORDER BY hands_played DESC LIMIT ?
        """,
        "biggest_win": """
            SELECT user_id, biggest_win_cents AS value FROM blackjack_stats
            WHERE biggest_win_cents > 0 ORDER BY biggest_win_cents DESC LIMIT ?
        """,
        "blackjacks_hit": """
            SELECT user_id, blackjacks_hit AS value FROM blackjack_stats
            WHERE blackjacks_hit > 0 ORDER BY blackjacks_hit DESC LIMIT ?
        """,
    }

    async def get_leaderboard(self, metric: str, limit: int = 5) -> list[dict]:
        return await asyncio.to_thread(self._get_leaderboard_sync, metric, limit)

    def _get_leaderboard_sync(self, metric: str, limit: int) -> list[dict]:
        query = self._LEADERBOARD_QUERIES[metric]
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(query, (limit,)).fetchall()
        return [{"user_id": int(r[0]), "value": r[1]} for r in rows]
