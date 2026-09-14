from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .rules import DEFAULT_MODE

logger = logging.getLogger(__name__)

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
    user_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'single_house',
    deck_json TEXT NOT NULL,
    discard_json TEXT NOT NULL,
    hands_played INTEGER NOT NULL DEFAULT 0,
    last_shuffle_reason TEXT NOT NULL DEFAULT 'new shoe',
    -- Cards left when this shoe's cut card comes out. Persisted rather than
    -- re-rolled on load: the depth is chosen once per shuffle, and re-rolling it
    -- every restart would silently move the cut of a shoe already in play.
    cut_card_at INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, mode)
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

CREATE TABLE IF NOT EXISTS blackjack_prefs (
    user_id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'single_house',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blackjack_stats (
    user_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'single_house',
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
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, mode)
);
"""

def _table_ddl(table: str) -> list[str]:
    """The CREATE statements for one table, taken from SCHEMA itself so a rebuilt
    table is byte-for-byte what a fresh install would have produced."""
    out = []
    for chunk in SCHEMA.split(";"):
        text = chunk.strip()
        if not text:
            continue
        if text.startswith("CREATE TABLE") and f"EXISTS {table} (" in text:
            out.append(text)
        elif text.startswith("CREATE INDEX") and f" ON {table}(" in text:
            out.append(text)
    if not out:
        raise RuntimeError(f"no DDL for {table} in SCHEMA")
    return out


class BlackjackDB:
    def __init__(self, path: str | Path):
        self.path = str(path)

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with sqlite3.connect(self.path) as conn:
            self._migrate_sync(conn)
            conn.executescript(SCHEMA)
            conn.commit()

    # Tables that gained a `mode` column and a composite primary key when the
    # casino modes landed. SQLite can't alter a primary key in place, so each has
    # to be rebuilt; every pre-existing row belongs to the game this bot shipped
    # with, so it is adopted into DEFAULT_MODE and nobody loses a shoe or a stat.
    _MODE_KEYED_TABLES = ("blackjack_shoes", "blackjack_stats")

    # Columns added after a table already existed. ALTER ADD COLUMN is additive and
    # cheap, so these are handled separately from the primary-key rebuild above.
    _ADDED_COLUMNS = {
        "blackjack_shoes": [("cut_card_at", "INTEGER NOT NULL DEFAULT 0")],
    }

    def _migrate_sync(self, conn: sqlite3.Connection) -> None:
        self._rebuild_mode_keyed_tables(conn)
        self._add_missing_columns(conn)

    def _add_missing_columns(self, conn: sqlite3.Connection) -> None:
        for table, columns in self._ADDED_COLUMNS.items():
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue      # fresh database - SCHEMA builds it with the column
            for name, decl in columns:
                if name in existing:
                    continue
                logger.info("Blackjack: adding %s.%s", table, name)
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                conn.commit()

    def _rebuild_mode_keyed_tables(self, conn: sqlite3.Connection) -> None:
        for table in self._MODE_KEYED_TABLES:
            existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if not existing:
                continue          # fresh database - SCHEMA builds it correctly
            if "mode" in existing:
                continue          # already migrated

            logger.info("Blackjack: migrating %s to per-mode rows", table)
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(f"ALTER TABLE {table} RENAME TO {table}_pre_modes")
                for statement in _table_ddl(table):
                    conn.execute(statement)
                carried = ", ".join(existing)
                conn.execute(
                    f"INSERT INTO {table} (mode, {carried}) "
                    f"SELECT ?, {carried} FROM {table}_pre_modes",
                    (DEFAULT_MODE,),
                )
                moved = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                conn.execute(f"DROP TABLE {table}_pre_modes")
                conn.commit()
                logger.info("Blackjack: %s migrated, %s row(s) adopted into %s", table, moved, DEFAULT_MODE)
            except Exception:
                conn.rollback()
                logger.exception("Blackjack: migration of %s failed; left untouched", table)
                raise

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
        plateau_cents: int,
        extended_step_cents: int,
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
            plateau_cents,
            extended_step_cents,
            reason,
        )

    def _claim_daily_bonus_sync(
        self,
        user_id: int,
        claim_date: str,
        base_cents: int,
        streak_step_cents: int,
        plateau_cents: int,
        extended_step_cents: int,
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
            plateau_streak = (plateau_cents - base_cents) // streak_step_cents + 1
            if new_streak <= plateau_streak:
                amount_cents = base_cents + (new_streak - 1) * streak_step_cents
            else:
                amount_cents = plateau_cents + (new_streak - plateau_streak) * extended_step_cents

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

    async def get_all_active_games(self) -> list[tuple[int, dict]]:
        """Every row in blackjack_active_games, for startup reconciliation -
        get_active_game only looks up a single known user."""
        return await asyncio.to_thread(self._get_all_active_games_sync)

    def _get_all_active_games_sync(self) -> list[tuple[int, dict]]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT user_id, state_json FROM blackjack_active_games").fetchall()

        games: list[tuple[int, dict]] = []
        for user_id, state_json in rows:
            try:
                games.append((int(user_id), json.loads(state_json)))
            except Exception:
                # One row with corrupted JSON must not take down every other
                # user's row with it - callers (startup reconciliation) rely
                # on getting everyone else's hands back even if this one is
                # unreadable. It's left in place for scripts/flush_blackjack_hands.py
                # or the per-user get_active_game() corrupt-state recovery path
                # to deal with later.
                logger.error("Blackjack: skipping unreadable active-game row for user_id=%s", user_id, exc_info=True)
        return games

    async def delete_active_game(self, user_id: int) -> None:
        await asyncio.to_thread(self._delete_active_game_sync, user_id)

    def _delete_active_game_sync(self, user_id: int) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM blackjack_active_games WHERE user_id = ?", (user_id,))
            conn.commit()

    async def get_shoe_state(self, user_id: int, mode: str = DEFAULT_MODE) -> dict | None:
        return await asyncio.to_thread(self._get_shoe_state_sync, user_id, mode)

    def _get_shoe_state_sync(self, user_id: int, mode: str) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                """
                SELECT deck_json, discard_json, hands_played, last_shuffle_reason, cut_card_at
                FROM blackjack_shoes
                WHERE user_id = ? AND mode = ?
                """,
                (user_id, mode),
            ).fetchone()

        if row is None:
            return None

        return {
            "deck": json.loads(row[0]),
            "discard": json.loads(row[1]),
            "hands_played": int(row[2]),
            "last_shuffle_reason": str(row[3]),
            "cut_card_at": int(row[4] or 0),
        }

    async def save_shoe_state(
        self,
        user_id: int,
        deck_codes: list[str],
        discard_codes: list[str],
        hands_played: int,
        last_shuffle_reason: str,
        mode: str = DEFAULT_MODE,
        cut_card_at: int = 0,
    ) -> None:
        await asyncio.to_thread(
            self._save_shoe_state_sync,
            user_id,
            deck_codes,
            discard_codes,
            hands_played,
            last_shuffle_reason,
            mode,
            cut_card_at,
        )

    def _save_shoe_state_sync(
        self,
        user_id: int,
        deck_codes: list[str],
        discard_codes: list[str],
        hands_played: int,
        last_shuffle_reason: str,
        mode: str,
        cut_card_at: int = 0,
    ) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO blackjack_shoes(
                    user_id, mode, deck_json, discard_json, hands_played,
                    last_shuffle_reason, cut_card_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, mode) DO UPDATE SET
                    deck_json = excluded.deck_json,
                    discard_json = excluded.discard_json,
                    hands_played = excluded.hands_played,
                    last_shuffle_reason = excluded.last_shuffle_reason,
                    cut_card_at = excluded.cut_card_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    mode,
                    json.dumps(deck_codes),
                    json.dumps(discard_codes),
                    hands_played,
                    last_shuffle_reason,
                    cut_card_at,
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
        mode: str = DEFAULT_MODE,
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
            mode,
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
        mode: str,
    ) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO blackjack_stats(user_id, mode) VALUES (?, ?)", (user_id, mode))

            row = conn.execute(
                "SELECT current_win_streak, best_win_streak, biggest_win_cents "
                "FROM blackjack_stats WHERE user_id = ? AND mode = ?",
                (user_id, mode),
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
                WHERE user_id = ? AND mode = ?
                """,
                (
                    len(hand_results), wins, losses, pushes,
                    blackjacks_hit, busts, busts_prevented,
                    streak, best_streak,
                    wagered_cents, profit_cents, biggest_win,
                    user_id, mode,
                ),
            )
            conn.commit()

    async def get_stats(self, user_id: int, mode: str | None = None) -> dict:
        """Stats for one mode, or summed across every mode when mode is None."""
        return await asyncio.to_thread(self._get_stats_sync, user_id, mode)

    def _get_stats_sync(self, user_id: int, mode: str | None = None) -> dict:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                """
                SELECT SUM(hands_played), SUM(hands_won), SUM(hands_lost), SUM(hands_pushed),
                       SUM(blackjacks_hit), SUM(busts), SUM(busts_prevented),
                       -- best_win_streak and biggest_win are records, so MAX is right.
                       -- current_win_streak is a *live* counter per mode: MAX would
                       -- report a frozen streak from a mode the player left. Take the
                       -- one belonging to the most recently played row instead.
                       0, MAX(best_win_streak),
                       SUM(total_wagered_cents), SUM(total_profit_cents), MAX(biggest_win_cents)
                FROM blackjack_stats
                WHERE user_id = ? AND (? IS NULL OR mode = ?)
                """,
                (user_id, mode, mode),
            ).fetchone()
            live_streak_row = conn.execute(
                "SELECT current_win_streak FROM blackjack_stats "
                "WHERE user_id = ? AND (? IS NULL OR mode = ?) "
                "ORDER BY updated_at DESC LIMIT 1",
                (user_id, mode, mode),
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
        # SUM()/MAX() over zero matching rows yields a row of NULLs rather than no
        # row at all, so coalesce here or a player with no hands in this mode gets
        # None where every caller expects an int.
        values = [(v if v is not None else 0) for v in (row or [0] * len(columns))]
        stats = dict(zip(columns, values))
        stats["current_win_streak"] = int(live_streak_row[0]) if live_streak_row else 0
        stats["daily_streak"] = int(streak_row[0]) if streak_row else 0
        return stats

    # Stats are per (user, mode), so every board aggregates across a player's rows
    # and takes an optional mode filter. `? IS NULL` means "all modes" - counting
    # metrics SUM cleanly, records take MAX. Note that win_pct/roi_pct are only
    # strictly comparable *within* a mode: a 6:5 game and a 3:2 game do not have
    # the same expected value, which is why the filter exists.
    _LEADERBOARD_QUERIES = {
        "win_streak": """
            SELECT user_id, MAX(best_win_streak) AS value FROM blackjack_stats
            WHERE (? IS NULL OR mode = ?) GROUP BY user_id
            HAVING value > 0 ORDER BY value DESC LIMIT ?
        """,
        "busts_prevented": """
            SELECT user_id, SUM(busts_prevented) AS value FROM blackjack_stats
            WHERE (? IS NULL OR mode = ?) GROUP BY user_id
            HAVING value > 0 ORDER BY value DESC LIMIT ?
        """,
        "win_pct": """
            SELECT user_id,
                   (100.0 * SUM(hands_won) / NULLIF(SUM(hands_won) + SUM(hands_lost), 0)) AS value
            FROM blackjack_stats WHERE (? IS NULL OR mode = ?) GROUP BY user_id
            HAVING (SUM(hands_won) + SUM(hands_lost)) >= 10 ORDER BY value DESC LIMIT ?
        """,
        "roi_pct": """
            SELECT user_id,
                   (100.0 * SUM(total_profit_cents) / NULLIF(SUM(total_wagered_cents), 0)) AS value
            FROM blackjack_stats WHERE (? IS NULL OR mode = ?) GROUP BY user_id
            HAVING SUM(total_wagered_cents) >= 10000 ORDER BY value DESC LIMIT ?
        """,
        "hands_played": """
            SELECT user_id, SUM(hands_played) AS value FROM blackjack_stats
            WHERE (? IS NULL OR mode = ?) GROUP BY user_id
            HAVING value > 0 ORDER BY value DESC LIMIT ?
        """,
        "biggest_win": """
            SELECT user_id, MAX(biggest_win_cents) AS value FROM blackjack_stats
            WHERE (? IS NULL OR mode = ?) GROUP BY user_id
            HAVING value > 0 ORDER BY value DESC LIMIT ?
        """,
        "blackjacks_hit": """
            SELECT user_id, SUM(blackjacks_hit) AS value FROM blackjack_stats
            WHERE (? IS NULL OR mode = ?) GROUP BY user_id
            HAVING value > 0 ORDER BY value DESC LIMIT ?
        """,
    }

    async def get_leaderboard(self, metric: str, limit: int = 5, mode: str | None = None) -> list[dict]:
        return await asyncio.to_thread(self._get_leaderboard_sync, metric, limit, mode)

    def _get_leaderboard_sync(self, metric: str, limit: int, mode: str | None = None) -> list[dict]:
        query = self._LEADERBOARD_QUERIES[metric]
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(query, (mode, mode, limit)).fetchall()
        return [{"user_id": int(r[0]), "value": r[1]} for r in rows]

    async def get_mode(self, user_id: int) -> str:
        return await asyncio.to_thread(self._get_mode_sync, user_id)

    def _get_mode_sync(self, user_id: int) -> str:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT mode FROM blackjack_prefs WHERE user_id = ?", (user_id,)
            ).fetchone()
        return str(row[0]) if row else DEFAULT_MODE

    async def set_mode(self, user_id: int, mode: str) -> None:
        await asyncio.to_thread(self._set_mode_sync, user_id, mode)

    def _set_mode_sync(self, user_id: int, mode: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO blackjack_prefs(user_id, mode, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "mode = excluded.mode, updated_at = CURRENT_TIMESTAMP",
                (user_id, mode),
            )
            conn.commit()
