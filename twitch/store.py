import aiosqlite
from typing import Optional, List, Dict, Any

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS twitch_guild_config (
  guild_id        INTEGER PRIMARY KEY,
  channel_id      INTEGER,           -- where to post notifications
  role_id         INTEGER            -- role to ping (optional)
);

CREATE TABLE IF NOT EXISTS twitch_streamers (
  guild_id        INTEGER NOT NULL,
  user_login      TEXT    NOT NULL,  -- lowercase twitch login
  display_name    TEXT,              -- optional, used for nice output
  added_by        INTEGER,           -- discord user id who added this
  PRIMARY KEY (guild_id, user_login)
);

CREATE TABLE IF NOT EXISTS twitch_stream_state (
  guild_id        INTEGER NOT NULL,
  user_login      TEXT    NOT NULL,
  is_live         INTEGER NOT NULL DEFAULT 0,
  last_stream_id  TEXT,
  last_started_at TEXT,
  last_notified_at TEXT,
  PRIMARY KEY (guild_id, user_login)
);

CREATE INDEX IF NOT EXISTS idx_twitch_streamers_guild ON twitch_streamers(guild_id);
"""

class TwitchStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if not self._db:
            raise RuntimeError("TwitchStore not connected")
        return self._db

    # ---- guild config ----
    async def set_guild_channel(self, guild_id: int, channel_id: Optional[int]) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO twitch_guild_config (guild_id, channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id",
            (guild_id, channel_id),
        )
        await db.commit()

    async def set_guild_role(self, guild_id: int, role_id: Optional[int]) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO twitch_guild_config (guild_id, role_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET role_id=excluded.role_id",
            (guild_id, role_id),
        )
        await db.commit()

    async def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        db = self._conn()
        cur = await db.execute(
            "SELECT guild_id, channel_id, role_id FROM twitch_guild_config WHERE guild_id=?",
            (guild_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else {"guild_id": guild_id, "channel_id": None, "role_id": None}

    # ---- streamers ----
    async def add_streamer(
        self,
        guild_id: int,
        user_login: str,
        display_name: Optional[str] = None,
        added_by: Optional[int] = None,
    ) -> None:
        user_login = user_login.strip().lower()
        db = self._conn()
        await db.execute(
            "INSERT OR REPLACE INTO twitch_streamers (guild_id, user_login, display_name, added_by) VALUES (?, ?, ?, ?)",
            (guild_id, user_login, display_name, added_by),
        )
        # Ensure state row exists
        await db.execute(
            "INSERT OR IGNORE INTO twitch_stream_state (guild_id, user_login, is_live) VALUES (?, ?, 0)",
            (guild_id, user_login),
        )
        await db.commit()

    async def count_user_streamers(self, guild_id: int, user_id: int) -> int:
        """Count how many streamers a specific user has added in this guild."""
        db = self._conn()
        cur = await db.execute(
            "SELECT COUNT(*) FROM twitch_streamers WHERE guild_id = ? AND added_by = ?",
            (guild_id, user_id),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def get_user_streamer(self, guild_id: int, user_id: int) -> Optional[str]:
        """Get the streamer a user has added (if any)."""
        db = self._conn()
        cur = await db.execute(
            "SELECT user_login FROM twitch_streamers WHERE guild_id = ? AND added_by = ?",
            (guild_id, user_id),
        )
        row = await cur.fetchone()
        return row["user_login"] if row else None

    async def remove_streamer(self, guild_id: int, user_login: str) -> None:
        user_login = user_login.strip().lower()
        db = self._conn()
        await db.execute("DELETE FROM twitch_streamers WHERE guild_id=? AND user_login=?", (guild_id, user_login))
        await db.execute("DELETE FROM twitch_stream_state WHERE guild_id=? AND user_login=?", (guild_id, user_login))
        await db.commit()

    async def list_streamers(self, guild_id: int) -> List[Dict[str, Any]]:
        db = self._conn()
        cur = await db.execute(
            "SELECT user_login, display_name FROM twitch_streamers WHERE guild_id=? ORDER BY user_login",
            (guild_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_all_guild_streamers(self) -> Dict[int, List[str]]:
        """Returns {guild_id: [user_login,...]} for polling."""
        db = self._conn()
        cur = await db.execute("SELECT guild_id, user_login FROM twitch_streamers ORDER BY guild_id")
        rows = await cur.fetchall()
        out: Dict[int, List[str]] = {}
        for r in rows:
            out.setdefault(int(r["guild_id"]), []).append(str(r["user_login"]))
        return out

    # ---- state ----
    async def get_state(self, guild_id: int, user_login: str) -> Dict[str, Any]:
        user_login = user_login.strip().lower()
        db = self._conn()
        cur = await db.execute(
            "SELECT guild_id, user_login, is_live, last_stream_id, last_started_at, last_notified_at "
            "FROM twitch_stream_state WHERE guild_id=? AND user_login=?",
            (guild_id, user_login),
        )
        row = await cur.fetchone()
        if not row:
            return {"guild_id": guild_id, "user_login": user_login, "is_live": 0,
                    "last_stream_id": None, "last_started_at": None, "last_notified_at": None}
        return dict(row)

    async def set_state(
        self,
        guild_id: int,
        user_login: str,
        is_live: bool,
        stream_id: Optional[str],
        started_at: Optional[str],
        notified_at: Optional[str],
    ) -> None:
        user_login = user_login.strip().lower()
        db = self._conn()
        await db.execute(
            "INSERT INTO twitch_stream_state (guild_id, user_login, is_live, last_stream_id, last_started_at, last_notified_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, user_login) DO UPDATE SET "
            "is_live=excluded.is_live, last_stream_id=excluded.last_stream_id, "
            "last_started_at=excluded.last_started_at, last_notified_at=excluded.last_notified_at",
            (guild_id, user_login, 1 if is_live else 0, stream_id, started_at, notified_at),
        )
        await db.commit()
