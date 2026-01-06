# food/db.py - Database queries for food nutrition data
import logging
import os
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

# Database path
DB_FILE = os.path.join(os.path.dirname(__file__), "food.db")

_db: Optional[aiosqlite.Connection] = None


async def get_food_db() -> aiosqlite.Connection:
    """Get or create database connection"""
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_FILE)
        _db.row_factory = aiosqlite.Row
    return _db


async def close_food_db() -> None:
    """Close database connection"""
    global _db
    if _db:
        await _db.close()
        _db = None


async def search_food(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Search for food items by name, returns list of matches"""
    db = await get_food_db()

    # Search by item name, include vendor for context
    cursor = await db.execute("""
        SELECT
            fi.id,
            fi.vendor,
            fi.name,
            fi.image_url,
            fi.logo_url,
            fn.calories,
            fn.total_fat_g,
            fn.sat_fat_g,
            fn.cholesterol_mg,
            fn.carbs_g,
            fn.protein_g,
            fn.sodium_mg,
            fn.serving_size,
            json_extract(fn.raw_json, '$.food_category') as food_category
        FROM food_items fi
        LEFT JOIN food_nutrition fn ON fi.id = fn.food_item_id
        WHERE fi.name LIKE ? OR fi.vendor LIKE ?
        ORDER BY
            CASE WHEN fi.name LIKE ? THEN 0 ELSE 1 END,
            fi.name
        LIMIT ?
    """, (f"%{query}%", f"%{query}%", f"{query}%", limit))

    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_food_by_id(food_id: int) -> Optional[Dict[str, Any]]:
    """Get a specific food item by ID"""
    db = await get_food_db()

    cursor = await db.execute("""
        SELECT
            fi.id,
            fi.vendor,
            fi.name,
            fi.image_url,
            fi.logo_url,
            fn.calories,
            fn.total_fat_g,
            fn.sat_fat_g,
            fn.cholesterol_mg,
            fn.carbs_g,
            fn.protein_g,
            fn.sodium_mg,
            fn.serving_size,
            fn.year,
            json_extract(fn.raw_json, '$.food_category') as food_category
        FROM food_items fi
        LEFT JOIN food_nutrition fn ON fi.id = fn.food_item_id
        WHERE fi.id = ?
    """, (food_id,))

    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_random_food() -> Optional[Dict[str, Any]]:
    """Get a random food item with nutrition info"""
    db = await get_food_db()

    cursor = await db.execute("""
        SELECT
            fi.id,
            fi.vendor,
            fi.name,
            fi.image_url,
            fi.logo_url,
            fn.calories,
            fn.total_fat_g,
            fn.sat_fat_g,
            fn.cholesterol_mg,
            fn.carbs_g,
            fn.protein_g,
            fn.sodium_mg,
            fn.serving_size,
            fn.year,
            json_extract(fn.raw_json, '$.food_category') as food_category
        FROM food_items fi
        LEFT JOIN food_nutrition fn ON fi.id = fn.food_item_id
        WHERE fn.calories IS NOT NULL
        ORDER BY RANDOM()
        LIMIT 1
    """)

    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_vendors() -> List[str]:
    """Get list of all vendors/restaurants"""
    db = await get_food_db()
    cursor = await db.execute("SELECT DISTINCT vendor FROM food_items ORDER BY vendor")
    rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def get_food_count() -> int:
    """Get total count of food items"""
    db = await get_food_db()
    cursor = await db.execute("SELECT COUNT(*) FROM food_items")
    row = await cursor.fetchone()
    return row[0] if row else 0
