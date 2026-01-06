import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import aiosqlite
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

DATASET_ID = "qgc5-ecnb"
ENDPOINT = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"

# Default database path (in food folder)
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "food.db")

# Tune
LIMIT = 50000

# Setup requests session with retry logic
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# Suppress SSL warnings (macOS Python cert issues)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None

async def ensure_schema(db: aiosqlite.Connection) -> None:
    await db.executescript("""
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS food_items (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      vendor        TEXT NOT NULL,
      name          TEXT NOT NULL,
      image_url     TEXT,
      logo_url      TEXT,
      menustat_item_id TEXT,
      created_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
      updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
      UNIQUE(vendor, name)
    );

    CREATE TRIGGER IF NOT EXISTS food_items_updated_at
    AFTER UPDATE ON food_items
    BEGIN
      UPDATE food_items SET updated_at = strftime('%s','now') WHERE id = NEW.id;
    END;

    CREATE TABLE IF NOT EXISTS food_nutrition (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      food_item_id    INTEGER NOT NULL REFERENCES food_items(id) ON DELETE CASCADE,
      source          TEXT NOT NULL DEFAULT 'menustat',
      year            INTEGER,
      calories        REAL,
      total_fat_g     REAL,
      sat_fat_g       REAL,
      cholesterol_mg  REAL,
      carbs_g         REAL,
      protein_g       REAL,
      sodium_mg       REAL,
      serving_size    TEXT,
      raw_json        TEXT,
      created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
      UNIQUE(food_item_id, source, year)
    );

    CREATE INDEX IF NOT EXISTS idx_food_items_vendor ON food_items(vendor);
    CREATE INDEX IF NOT EXISTS idx_food_nutrition_item ON food_nutrition(food_item_id);
    """)
    await db.commit()

def fetch_all_rows() -> List[Dict[str, Any]]:
    logger.info(f"Fetching data from MenuStat API: {ENDPOINT}")
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        params = {"$limit": LIMIT, "$offset": offset}
        try:
            # verify=False is used due to macOS Python SSL cert issues
            # Safe for this public API; data is not sensitive
            r = session.get(ENDPOINT, params=params, timeout=60, verify=False)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"API request failed at offset {offset}: {e}")
            raise
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        logger.info(f"Fetched {len(rows)} rows so far...")
        offset += len(batch)
        time.sleep(0.2)
    logger.info(f"Fetch complete. Total rows: {len(rows)}")
    return rows

async def upsert_food_item(db: aiosqlite.Connection, vendor: str, name: str, menustat_item_id: Optional[str]) -> int:
    # Insert or ignore, then fetch id
    await db.execute("""
      INSERT OR IGNORE INTO food_items (vendor, name, menustat_item_id)
      VALUES (?, ?, ?)
    """, (vendor, name, menustat_item_id))

    # If already exists but menustat_item_id was missing, update it
    await db.execute("""
      UPDATE food_items
      SET menustat_item_id = COALESCE(menustat_item_id, ?)
      WHERE vendor = ? AND name = ?
    """, (menustat_item_id, vendor, name))

    cur = await db.execute("SELECT id FROM food_items WHERE vendor = ? AND name = ?", (vendor, name))
    row = await cur.fetchone()
    return int(row[0])

async def upsert_nutrition(db: aiosqlite.Connection, food_item_id: int, year: Optional[int], row: Dict[str, Any]) -> None:
    # NOTE: field names in MenuStat can vary slightly; adjust if needed after first run.
    await db.execute("""
      INSERT INTO food_nutrition
        (food_item_id, source, year, calories, total_fat_g, sat_fat_g, cholesterol_mg, carbs_g, protein_g, sodium_mg, serving_size, raw_json)
      VALUES
        (?, 'menustat', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(food_item_id, source, year) DO UPDATE SET
        calories        = excluded.calories,
        total_fat_g     = excluded.total_fat_g,
        sat_fat_g       = excluded.sat_fat_g,
        cholesterol_mg  = excluded.cholesterol_mg,
        carbs_g         = excluded.carbs_g,
        protein_g       = excluded.protein_g,
        sodium_mg       = excluded.sodium_mg,
        serving_size    = excluded.serving_size,
        raw_json        = excluded.raw_json
    """, (
        food_item_id,
        year,
        to_float(row.get("calories")),
        to_float(row.get("total_fat") or row.get("total_fat_g")),
        to_float(row.get("saturated_fat") or row.get("sat_fat")),
        to_float(row.get("cholesterol")),
        to_float(row.get("carbohydrates") or row.get("carbs")),
        to_float(row.get("protein")),
        to_float(row.get("sodium")),
        row.get("serving_size"),
        json.dumps(row, ensure_ascii=False),
    ))

async def main(db_path: str, all_years: bool = False) -> None:
    try:
        rows = fetch_all_rows()
    except requests.RequestException:
        logger.error("Failed to fetch data from API. Exiting.")
        sys.exit(1)

    if not rows:
        logger.warning("No data returned from API. Exiting.")
        return

    # Filter to latest year only (unless --all-years is specified)
    if not all_years:
        years = [int(r["year"]) for r in rows if r.get("year")]
        latest_year = max(years) if years else None
        if latest_year:
            original_count = len(rows)
            rows = [r for r in rows if r.get("year") and int(r["year"]) == latest_year]
            logger.info(f"Filtering to year {latest_year}: {len(rows)} rows (from {original_count})")
    else:
        logger.info(f"Keeping all years: {len(rows)} rows")

    logger.info(f"Connecting to database: {db_path}")
    try:
        async with aiosqlite.connect(db_path) as db:
            await ensure_schema(db)
            logger.info("Database schema verified")

            inserted = 0
            skipped = 0

            for r in rows:
                vendor = (r.get("restaurant") or "").strip()
                name = (r.get("item_name") or "").strip()
                if not vendor or not name:
                    skipped += 1
                    continue

                year = int(r["year"]) if r.get("year") else None
                menustat_item_id = r.get("menu_item_id")

                food_item_id = await upsert_food_item(db, vendor, name, menustat_item_id)
                await upsert_nutrition(db, food_item_id, year, r)

                inserted += 1
                if inserted % 2000 == 0:
                    await db.commit()
                    logger.info(f"Progress: {inserted} rows upserted...")

            await db.commit()
            logger.info(f"Complete! Upserted {inserted} rows, skipped {skipped} invalid rows")
            logger.info(f"Database saved to: {db_path}")

    except aiosqlite.Error as e:
        logger.error(f"Database error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load MenuStat restaurant nutrition data into SQLite"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "--all-years",
        action="store_true",
        help="Keep all years of data (default: latest year only)"
    )
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("MenuStat Food Data Loader")
    logger.info("=" * 50)

    asyncio.run(main(args.db, args.all_years))
