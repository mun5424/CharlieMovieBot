"""
Handhelds database operations (async, aiosqlite)
Stores a canonical name/slug + the full row JSON.
"""

from __future__ import annotations

import aiosqlite
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "handhelds.db")


def _now_unix() -> int:
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


def slugify(name: str) -> str:
    import re
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")


async def upsert_many(rows: List[Dict[str, Any]]) -> Tuple[int, int]:
    if not rows:
        return (0, 0)

    now = _now_unix()

    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")

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

        await conn.commit()
        return (len(rows), len(rows))


async def count_missing_images() -> int:
    await init_db()
    async with aiosqlite.connect(DB_FILE) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM handhelds WHERE image_url IS NULL OR image_url = ''")
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def update_images_by_name_norm(image_map: dict[str, str]) -> int:
    if not image_map:
        return 0

    await init_db()

    items: list[tuple[str, str, str]] = []
    for name_norm, url in image_map.items():
        nn = str(name_norm or "").strip().lower()
        u = str(url or "").strip()
        if not nn or not u.startswith("http"):
            continue
        items.append((u, nn, u))

    if not items:
        return 0

    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")

        await conn.executemany(
            """
            UPDATE handhelds
               SET image_url = ?
             WHERE name_norm = ?
               AND (image_url IS NULL OR image_url = '' OR image_url != ?)
            """,
            items
        )
        await conn.commit()

        cur = await conn.execute("SELECT changes();")
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def get_by_slug_or_exact_name(query: str) -> Optional[Dict[str, Any]]:
    q_norm = query.strip().lower()
    slug = slugify(query)

    async with aiosqlite.connect(DB_FILE) as conn:
        conn.row_factory = aiosqlite.Row

        cur = await conn.execute("SELECT * FROM handhelds WHERE slug = ? LIMIT 1", (slug,))
        row = await cur.fetchone()
        if row:
            return dict(row)

        cur = await conn.execute("SELECT * FROM handhelds WHERE name_norm = ? LIMIT 1", (q_norm,))
        row = await cur.fetchone()
        if row:
            return dict(row)

    return None


async def get_handhelds_missing_images() -> List[Dict[str, Any]]:
    """Get all handhelds without an image_url."""
    async with aiosqlite.connect(DB_FILE) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT slug, name
            FROM handhelds
            WHERE image_url IS NULL OR image_url = ''
        """)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_image_by_slug(slug: str, image_url: str) -> bool:
    """Update image_url for a handheld by slug. Returns True if updated."""
    if not slug or not image_url or not image_url.startswith("http"):
        return False

    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute(
            """
            UPDATE handhelds
               SET image_url = ?
             WHERE slug = ?
               AND (image_url IS NULL OR image_url = '')
            """,
            (image_url, slug)
        )
        await conn.commit()
        cur = await conn.execute("SELECT changes();")
        row = await cur.fetchone()
        return bool(row and row[0] > 0)


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