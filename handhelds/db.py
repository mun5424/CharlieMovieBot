"""
Handhelds database operations (async, aiosqlite)
Stores a canonical name/slug + the full row JSON.
"""

from __future__ import annotations

import aiosqlite
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "handhelds.db")


def _now_unix() -> int:
    # keep it simple; sqlite also has strftime, but we use python for consistency
    import time
    return int(time.time())


async def init_db() -> None:
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS handhelds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            slug            TEXT NOT NULL UNIQUE,
            name            TEXT NOT NULL,
            name_norm       TEXT NOT NULL,
            brand           TEXT,
            os              TEXT,
            released        TEXT,
            form_factor     TEXT,
            performance     TEXT,
            price_avg       TEXT,
            vendor_link     TEXT,
            image_url       TEXT,
            data_json       TEXT NOT NULL,

            updated_at      INTEGER NOT NULL
        );
        """)
        # Add image_url column if it doesn't exist (for existing DBs)
        try:
            await conn.execute("ALTER TABLE handhelds ADD COLUMN image_url TEXT;")
        except Exception:
            pass  # Column already exists
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_handhelds_name_norm ON handhelds(name_norm);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_handhelds_brand ON handhelds(brand);")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS handhelds_meta (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL
        );
        """)
        await conn.commit()


async def get_meta(key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_FILE) as conn:
        cur = await conn.execute("SELECT value FROM handhelds_meta WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


async def set_meta(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute(
            "INSERT INTO handhelds_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await conn.commit()


async def upsert_many(rows: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    Upsert rows into handhelds.
    Returns (inserted_or_updated_count, total_count).
    """
    if not rows:
        return (0, 0)

    now = _now_unix()

    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")

        changed = 0
        for r in rows:
            await conn.execute("""
            INSERT INTO handhelds (
                slug, name, name_norm, brand, os, released, form_factor,
                performance, price_avg, vendor_link, image_url, data_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name        = excluded.name,
                name_norm   = excluded.name_norm,
                brand       = excluded.brand,
                os          = excluded.os,
                released    = excluded.released,
                form_factor = excluded.form_factor,
                performance = excluded.performance,
                price_avg   = excluded.price_avg,
                vendor_link = excluded.vendor_link,
                image_url   = excluded.image_url,
                data_json   = excluded.data_json,
                updated_at  = excluded.updated_at
            """, (
                r["slug"], r["name"], r["name_norm"],
                r.get("brand"), r.get("os"), r.get("released"), r.get("form_factor"),
                r.get("performance"), r.get("price_avg"), r.get("vendor_link"),
                r.get("image_url"), r["data_json"], now
            ))
            changed += 1

        await conn.commit()
        return (changed, len(rows))


async def get_by_slug_or_exact_name(query: str) -> Optional[Dict[str, Any]]:
    q_norm = query.strip().lower()
    slug = slugify(query)

    async with aiosqlite.connect(DB_FILE) as conn:
        conn.row_factory = aiosqlite.Row

        # 1) direct slug match
        cur = await conn.execute("SELECT * FROM handhelds WHERE slug = ? LIMIT 1", (slug,))
        row = await cur.fetchone()
        if row:
            return dict(row)

        # 2) exact normalized name
        cur = await conn.execute("SELECT * FROM handhelds WHERE name_norm = ? LIMIT 1", (q_norm,))
        row = await cur.fetchone()
        if row:
            return dict(row)

    return None


async def search_names(partial: str, limit: int = 25) -> List[Dict[str, Any]]:
    q = partial.strip().lower()
    if not q:
        return []

    like = f"%{q}%"
    async with aiosqlite.connect(DB_FILE) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT name, slug, brand, performance
            FROM handhelds
            WHERE name_norm LIKE ?
            ORDER BY
              CASE WHEN name_norm LIKE ? THEN 0 ELSE 1 END,
              LENGTH(name_norm) ASC
            LIMIT ?
        """, (like, f"{q}%", limit))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


def slugify(name: str) -> str:
    import re
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")

async def update_images_by_name_norm(image_map: dict[str, str]) -> int:
    """
    Update image_url for rows matching name_norm.
    Only updates when the new URL is non-empty and differs from what's stored.
    Returns number of rows updated.
    """
    if not image_map:
        return 0

    await init_db()

    # Clean + normalize input (avoid updating with junk)
    items: list[tuple[str, str]] = []
    for name_norm, url in image_map.items():
        if not name_norm:
            continue
        nn = str(name_norm).strip().lower()
        u = str(url).strip()
        if not nn or not u or not u.startswith("http"):
            continue
        items.append((u, nn, u))  # params for UPDATE

    if not items:
        return 0

    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")

        # One UPDATE per item; executemany is fine for a few thousand rows
        cur = await conn.executemany(
            """
            UPDATE handhelds
               SET image_url = ?
             WHERE name_norm = ?
               AND (image_url IS NULL OR image_url != ?)
            """,
            items
        )
        await conn.commit()

        # aiosqlite cursor.rowcount with executemany can be unreliable depending on sqlite version,
        # so we compute updated count explicitly via SELECT changes().
        cur2 = await conn.execute("SELECT changes();")
        row = await cur2.fetchone()
        return int(row[0]) if row else 0
