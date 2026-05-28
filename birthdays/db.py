"""SQLite persistence for birthday sign-ups and announcement deduplication."""

from __future__ import annotations

import asyncio
import calendar
import datetime
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class BirthdayRecord:
    user_id: int
    month: int
    day: int


class BirthdayStore:
    """Stores one birthday per Discord user and tracks celebration posts."""

    def __init__(self, db_path: str | Path = "bot.db") -> None:
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        db = self._connect()
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS birthdays (
                    user_id INTEGER PRIMARY KEY,
                    birth_month INTEGER NOT NULL CHECK (birth_month BETWEEN 1 AND 12),
                    birth_day INTEGER NOT NULL CHECK (
                        (birth_month IN (1, 3, 5, 7, 8, 10, 12) AND birth_day BETWEEN 1 AND 31)
                        OR (birth_month IN (4, 6, 9, 11) AND birth_day BETWEEN 1 AND 30)
                        OR (birth_month = 2 AND birth_day BETWEEN 1 AND 29)
                    ),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS birthday_announcements (
                    announced_on TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (announced_on, channel_id, user_id)
                )
                """
            )
            db.commit()
        finally:
            db.close()

    async def get_birthday(self, user_id: int) -> BirthdayRecord | None:
        return await asyncio.to_thread(self._get_birthday_sync, user_id)

    def _get_birthday_sync(self, user_id: int) -> BirthdayRecord | None:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT user_id, birth_month, birth_day FROM birthdays WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            db.close()

        if row is None:
            return None
        return BirthdayRecord(user_id=int(row[0]), month=int(row[1]), day=int(row[2]))

    async def get_unannounced_birthdays(
        self,
        month: int,
        day: int,
        announcement_date: datetime.date,
        channel_id: int,
    ) -> list[BirthdayRecord]:
        return await asyncio.to_thread(
            self._get_unannounced_birthdays_sync,
            month,
            day,
            announcement_date,
            channel_id,
        )

    def _get_unannounced_birthdays_sync(
        self,
        month: int,
        day: int,
        announcement_date: datetime.date,
        channel_id: int,
    ) -> list[BirthdayRecord]:
        db = self._connect()
        try:
            rows = db.execute(
                """
                SELECT b.user_id, b.birth_month, b.birth_day
                FROM birthdays AS b
                WHERE b.birth_month = ?
                  AND b.birth_day = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM birthday_announcements AS a
                      WHERE a.announced_on = ?
                        AND a.channel_id = ?
                        AND a.user_id = b.user_id
                  )
                ORDER BY b.user_id
                """,
                (month, day, announcement_date.isoformat(), channel_id),
            ).fetchall()
        finally:
            db.close()

        return [
            BirthdayRecord(user_id=int(row[0]), month=int(row[1]), day=int(row[2]))
            for row in rows
        ]

    async def mark_announced(
        self,
        announcement_date: datetime.date,
        channel_id: int,
        user_ids: Iterable[int],
    ) -> None:
        rows = [
            (announcement_date.isoformat(), channel_id, int(user_id))
            for user_id in user_ids
        ]
        if rows:
            await asyncio.to_thread(self._mark_announced_sync, rows)

    def _mark_announced_sync(self, rows: list[tuple[str, int, int]]) -> None:
        db = self._connect()
        try:
            db.executemany(
                """
                INSERT OR IGNORE INTO birthday_announcements
                    (announced_on, channel_id, user_id)
                VALUES (?, ?, ?)
                """,
                rows,
            )
            db.commit()
        finally:
            db.close()

    async def upsert_birthday(self, user_id: int, month: int, day: int) -> bool:
        """
        Save a birthday and return True when an existing birthday was replaced.

        A leap year is used for validation so February 29 is accepted.
        """
        last_day = calendar.monthrange(2000, month)[1]
        if not 1 <= day <= last_day:
            raise ValueError("Invalid birthday date.")

        return await asyncio.to_thread(self._upsert_birthday_sync, user_id, month, day)

    def _upsert_birthday_sync(self, user_id: int, month: int, day: int) -> bool:
        db = self._connect()
        try:
            already_exists = db.execute(
                "SELECT 1 FROM birthdays WHERE user_id = ?",
                (user_id,),
            ).fetchone() is not None
            db.execute(
                """
                INSERT INTO birthdays (user_id, birth_month, birth_day)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    birth_month = excluded.birth_month,
                    birth_day = excluded.birth_day,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, month, day),
            )
            db.commit()
            return already_exists
        finally:
            db.close()
