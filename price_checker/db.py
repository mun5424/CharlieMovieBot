"""
Price Checker database operations
"""

import aiosqlite
import asyncio
import json
import logging
import os
import time
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Database file path
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "price_checker.db")

# Connection
_db: Optional[aiosqlite.Connection] = None
_lock = asyncio.Lock()

# Valid seller tiers
SELLER_TIERS = ('first_party', 'fulfilled', 'marketplace_good', 'marketplace_unknown')
CONDITIONS = ('new', 'refurb', 'used')


async def get_db() -> aiosqlite.Connection:
    """Get or create database connection"""
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_FILE)
        _db.row_factory = aiosqlite.Row
        await _init_schema(_db)
        logger.info(f"Connected to price checker database: {DB_FILE}")
    return _db


async def close_db():
    """Close database connection"""
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("Closed price checker database connection")


async def _init_schema(db: aiosqlite.Connection):
    """Initialize database schema"""
    await db.executescript("""
        PRAGMA foreign_keys = ON;

        -- Products you track
        CREATE TABLE IF NOT EXISTS products (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            category          TEXT NOT NULL,
            brand             TEXT,
            model             TEXT,
            name              TEXT NOT NULL,

            -- Canonical identifiers
            upc               TEXT,
            mpn               TEXT,
            asin              TEXT,
            bestbuy_sku       TEXT,
            walmart_item_id   TEXT,
            ebay_epid         TEXT,

            -- Extra attributes as JSON
            attrs_json        TEXT,

            created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
        CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_products_upc ON products(upc) WHERE upc IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_products_asin ON products(asin) WHERE asin IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_products_bestbuy ON products(bestbuy_sku) WHERE bestbuy_sku IS NOT NULL;

        -- Trigger for updated_at
        CREATE TRIGGER IF NOT EXISTS products_updated_at
        AFTER UPDATE ON products
        BEGIN
            UPDATE products SET updated_at = strftime('%s','now') WHERE id = NEW.id;
        END;

        -- Offers observed from sources
        CREATE TABLE IF NOT EXISTS offers (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            source            TEXT NOT NULL,
            source_item_id    TEXT,
            url               TEXT,

            condition         TEXT NOT NULL CHECK(condition IN ('new','refurb','used')),
            seller_tier       TEXT NOT NULL CHECK(seller_tier IN ('first_party','fulfilled','marketplace_good','marketplace_unknown')),
            seller_name       TEXT,
            return_ok         INTEGER NOT NULL DEFAULT 1,
            flags             TEXT,

            price             REAL NOT NULL,
            shipping          REAL NOT NULL DEFAULT 0,
            currency          TEXT NOT NULL DEFAULT 'USD',

            observed_at       INTEGER NOT NULL,
            created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_offers_product_time ON offers(product_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_offers_product_cond_time ON offers(product_id, condition, observed_at);
        CREATE INDEX IF NOT EXISTS idx_offers_source_item ON offers(source, source_item_id);

        -- Daily snapshots (one row per product+condition+day)
        CREATE TABLE IF NOT EXISTS daily_snapshots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            condition         TEXT NOT NULL CHECK(condition IN ('new','refurb','used')),
            day_utc           TEXT NOT NULL,
            best_price        REAL NOT NULL,
            best_source       TEXT,
            best_offer_id     INTEGER REFERENCES offers(id) ON DELETE SET NULL,

            sample_count      INTEGER NOT NULL DEFAULT 0,
            created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),

            UNIQUE(product_id, condition, day_utc)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_product_cond_day ON daily_snapshots(product_id, condition, day_utc);

        -- Precomputed baselines (median/MAD)
        CREATE TABLE IF NOT EXISTS baselines (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            condition         TEXT NOT NULL CHECK(condition IN ('new','refurb','used')),
            window_days       INTEGER NOT NULL,
            as_of_day_utc     TEXT NOT NULL,
            median_price      REAL NOT NULL,
            mad_price         REAL NOT NULL,
            n_days            INTEGER NOT NULL,

            created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),

            UNIQUE(product_id, condition, window_days, as_of_day_utc)
        );

        CREATE INDEX IF NOT EXISTS idx_baselines_lookup
            ON baselines(product_id, condition, window_days, as_of_day_utc);

        -- Guild watchlists / alert configuration
        CREATE TABLE IF NOT EXISTS guild_watchlists (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id          TEXT NOT NULL,
            channel_id        TEXT NOT NULL,
            role_id_to_ping   TEXT,

            category          TEXT NOT NULL DEFAULT '',
            condition         TEXT NOT NULL DEFAULT '',
            min_score         INTEGER NOT NULL DEFAULT 80,
            max_items_per_day INTEGER NOT NULL DEFAULT 10,

            created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),

            UNIQUE(guild_id, channel_id, category, condition)
        );

        -- Alerts already sent (deduplication)
        CREATE TABLE IF NOT EXISTS alerts_sent (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id          TEXT NOT NULL,
            channel_id        TEXT NOT NULL,
            product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            offer_id          INTEGER REFERENCES offers(id) ON DELETE SET NULL,
            day_utc           TEXT NOT NULL,
            deal_class        TEXT NOT NULL,
            score             INTEGER NOT NULL,

            created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),

            UNIQUE(guild_id, channel_id, product_id, day_utc, deal_class)
        );
    """)
    await db.commit()


# ============== Product Operations ==============

async def add_product(
    category: str,
    name: str,
    brand: str = None,
    model: str = None,
    upc: str = None,
    mpn: str = None,
    asin: str = None,
    bestbuy_sku: str = None,
    walmart_item_id: str = None,
    ebay_epid: str = None,
    attrs: Dict = None
) -> int:
    """Add a product. Returns the product ID."""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            """INSERT INTO products
               (category, name, brand, model, upc, mpn, asin, bestbuy_sku, walmart_item_id, ebay_epid, attrs_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (category, name, brand, model, upc, mpn, asin, bestbuy_sku, walmart_item_id, ebay_epid,
             json.dumps(attrs) if attrs else None)
        )
        await db.commit()
        return cursor.lastrowid


async def get_product(product_id: int) -> Optional[Dict]:
    """Get a product by ID"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def get_product_by_identifier(
    upc: str = None,
    asin: str = None,
    bestbuy_sku: str = None
) -> Optional[Dict]:
    """Find a product by any unique identifier"""
    db = await get_db()
    async with _lock:
        if upc:
            cursor = await db.execute("SELECT * FROM products WHERE upc = ?", (upc,))
        elif asin:
            cursor = await db.execute("SELECT * FROM products WHERE asin = ?", (asin,))
        elif bestbuy_sku:
            cursor = await db.execute("SELECT * FROM products WHERE bestbuy_sku = ?", (bestbuy_sku,))
        else:
            return None

        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def search_products(query: str, category: str = None, limit: int = 25) -> List[Dict]:
    """Search products by name"""
    db = await get_db()
    async with _lock:
        if category:
            cursor = await db.execute(
                "SELECT * FROM products WHERE name LIKE ? AND category = ? LIMIT ?",
                (f"%{query}%", category, limit)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM products WHERE name LIKE ? LIMIT ?",
                (f"%{query}%", limit)
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ============== Offer Operations ==============

async def add_offer(
    product_id: int,
    source: str,
    price: float,
    condition: str,
    seller_tier: str,
    source_item_id: str = None,
    url: str = None,
    seller_name: str = None,
    return_ok: bool = True,
    flags: str = None,
    shipping: float = 0,
    currency: str = 'USD',
    observed_at: int = None
) -> int:
    """Add an offer observation. Returns the offer ID."""
    if condition not in CONDITIONS:
        raise ValueError(f"Invalid condition: {condition}")
    if seller_tier not in SELLER_TIERS:
        raise ValueError(f"Invalid seller_tier: {seller_tier}")

    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            """INSERT INTO offers
               (product_id, source, source_item_id, url, condition, seller_tier, seller_name,
                return_ok, flags, price, shipping, currency, observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (product_id, source, source_item_id, url, condition, seller_tier, seller_name,
             1 if return_ok else 0, flags, price, shipping, currency,
             observed_at or int(time.time()))
        )
        await db.commit()
        return cursor.lastrowid


async def get_offers_for_product(
    product_id: int,
    condition: str = None,
    since: int = None,
    limit: int = 100
) -> List[Dict]:
    """Get offers for a product"""
    db = await get_db()
    async with _lock:
        query = "SELECT * FROM offers WHERE product_id = ?"
        params = [product_id]

        if condition:
            query += " AND condition = ?"
            params.append(condition)
        if since:
            query += " AND observed_at >= ?"
            params.append(since)

        query += " ORDER BY observed_at DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ============== Daily Snapshot Operations ==============

async def update_daily_snapshot(
    product_id: int,
    condition: str,
    day_utc: str,
    best_price: float,
    best_source: str,
    best_offer_id: int = None,
    sample_count: int = 1
) -> None:
    """Update or insert a daily snapshot"""
    db = await get_db()
    async with _lock:
        await db.execute(
            """INSERT INTO daily_snapshots
               (product_id, condition, day_utc, best_price, best_source, best_offer_id, sample_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id, condition, day_utc)
               DO UPDATE SET
                   best_price = CASE WHEN excluded.best_price < best_price THEN excluded.best_price ELSE best_price END,
                   best_source = CASE WHEN excluded.best_price < best_price THEN excluded.best_source ELSE best_source END,
                   best_offer_id = CASE WHEN excluded.best_price < best_price THEN excluded.best_offer_id ELSE best_offer_id END,
                   sample_count = sample_count + 1""",
            (product_id, condition, day_utc, best_price, best_source, best_offer_id, sample_count)
        )
        await db.commit()


# ============== Baseline Operations ==============

async def compute_baseline(
    product_id: int,
    condition: str,
    as_of_day: str,
    window_days: int = 60
) -> Optional[Dict]:
    """Compute and store median/MAD baseline for a product+condition"""
    db = await get_db()
    async with _lock:
        # Compute median and MAD
        cursor = await db.execute("""
            WITH window AS (
                SELECT best_price
                FROM daily_snapshots
                WHERE product_id = ?
                  AND condition = ?
                  AND day_utc <= ?
                  AND day_utc >= date(?, '-' || (? - 1) || ' day')
            ),
            ordered AS (
                SELECT
                    best_price,
                    ROW_NUMBER() OVER (ORDER BY best_price) AS rn,
                    COUNT(*) OVER () AS cnt
                FROM window
            ),
            med AS (
                SELECT
                    AVG(best_price) AS median_price,
                    MAX(cnt) AS n_days
                FROM ordered
                WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
            ),
            devs AS (
                SELECT ABS(w.best_price - m.median_price) AS dev
                FROM window w
                CROSS JOIN med m
            ),
            dev_ordered AS (
                SELECT
                    dev,
                    ROW_NUMBER() OVER (ORDER BY dev) AS rn,
                    COUNT(*) OVER () AS cnt
                FROM devs
            ),
            mad AS (
                SELECT AVG(dev) AS mad_price
                FROM dev_ordered
                WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
            )
            SELECT
                med.median_price,
                COALESCE(mad.mad_price, 0) AS mad_price,
                med.n_days
            FROM med, mad
        """, (product_id, condition, as_of_day, as_of_day, window_days))

        row = await cursor.fetchone()
        if not row or row['median_price'] is None:
            return None

        # Store the baseline
        await db.execute(
            """INSERT INTO baselines
               (product_id, condition, window_days, as_of_day_utc, median_price, mad_price, n_days)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id, condition, window_days, as_of_day_utc)
               DO UPDATE SET
                   median_price = excluded.median_price,
                   mad_price = excluded.mad_price,
                   n_days = excluded.n_days""",
            (product_id, condition, window_days, as_of_day,
             row['median_price'], row['mad_price'], row['n_days'])
        )
        await db.commit()

        return {
            'median_price': row['median_price'],
            'mad_price': row['mad_price'],
            'n_days': row['n_days']
        }


async def get_baseline(
    product_id: int,
    condition: str,
    as_of_day: str,
    window_days: int = 60
) -> Optional[Dict]:
    """Get cached baseline for a product+condition"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            """SELECT median_price, mad_price, n_days
               FROM baselines
               WHERE product_id = ? AND condition = ?
                 AND window_days = ? AND as_of_day_utc = ?""",
            (product_id, condition, window_days, as_of_day)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


# ============== Deal Detection ==============

async def get_deals_for_guild(
    guild_id: str,
    channel_id: str,
    day_utc: str,
    window_days: int = 60,
    cap_discount: float = 0.30
) -> List[Dict]:
    """Get deals for a guild's watchlist configuration"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute("""
            WITH wl AS (
                SELECT * FROM guild_watchlists
                WHERE guild_id = ? AND channel_id = ?
                LIMIT 1
            ),
            candidates AS (
                SELECT
                    p.id AS product_id,
                    p.category,
                    p.name,
                    p.brand,
                    s.condition,
                    s.best_price AS price,
                    s.best_source,
                    s.best_offer_id,
                    b.median_price,
                    b.mad_price,
                    b.n_days,
                    o.seller_tier,
                    o.return_ok,
                    o.flags,
                    o.url
                FROM daily_snapshots s
                JOIN products p ON p.id = s.product_id
                JOIN baselines b ON b.product_id = s.product_id
                    AND b.condition = s.condition
                    AND b.window_days = ?
                    AND b.as_of_day_utc = ?
                LEFT JOIN offers o ON o.id = s.best_offer_id
                WHERE s.day_utc = ?
                  AND b.n_days >= 10
                  AND ((SELECT category FROM wl) = '' OR p.category = (SELECT category FROM wl))
                  AND ((SELECT condition FROM wl) = '' OR s.condition = (SELECT condition FROM wl))
            ),
            scored AS (
                SELECT *,
                    (median_price - price) / median_price AS discount,
                    (median_price - price) / (1.4826 * MAX(mad_price, 1.0)) AS z_score,
                    CASE
                        WHEN return_ok = 0 OR flags LIKE '%parts%' OR flags LIKE '%repair%' THEN 0.0
                        WHEN seller_tier = 'first_party' THEN 1.00
                        WHEN seller_tier = 'fulfilled' THEN 0.95
                        WHEN seller_tier = 'marketplace_good' THEN 0.85
                        ELSE 0.70
                    END AS trust
                FROM candidates
            ),
            final AS (
                SELECT *,
                    CAST(ROUND(100.0
                        * MIN(MAX(discount / ?, 0), 1.0)
                        * trust
                        * CASE WHEN z_score > 4.0 AND trust < 0.90 THEN 0.6 ELSE 1.0 END
                    ) AS INTEGER) AS score
                FROM scored
                WHERE trust > 0
            )
            SELECT f.* FROM final f
            LEFT JOIN alerts_sent a ON a.product_id = f.product_id
                AND a.day_utc = ?
                AND a.guild_id = ?
                AND a.channel_id = ?
            WHERE a.id IS NULL
              AND f.score >= (SELECT min_score FROM wl)
            ORDER BY f.score DESC
            LIMIT (SELECT max_items_per_day FROM wl)
        """, (guild_id, channel_id, window_days, day_utc, day_utc,
              cap_discount, day_utc, guild_id, channel_id))

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ============== Alert Operations ==============

async def mark_alert_sent(
    guild_id: str,
    channel_id: str,
    product_id: int,
    day_utc: str,
    deal_class: str,
    score: int,
    offer_id: int = None
) -> None:
    """Mark an alert as sent to prevent duplicates"""
    db = await get_db()
    async with _lock:
        await db.execute(
            """INSERT OR IGNORE INTO alerts_sent
               (guild_id, channel_id, product_id, offer_id, day_utc, deal_class, score)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, channel_id, product_id, offer_id, day_utc, deal_class, score)
        )
        await db.commit()


# ============== Watchlist Operations ==============

async def add_watchlist(
    guild_id: str,
    channel_id: str,
    category: str = '',
    condition: str = '',
    min_score: int = 80,
    max_items_per_day: int = 10,
    role_id_to_ping: str = None
) -> None:
    """Add or update a guild watchlist configuration"""
    db = await get_db()
    async with _lock:
        await db.execute(
            """INSERT INTO guild_watchlists
               (guild_id, channel_id, category, condition, min_score, max_items_per_day, role_id_to_ping)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(guild_id, channel_id, category, condition)
               DO UPDATE SET
                   min_score = excluded.min_score,
                   max_items_per_day = excluded.max_items_per_day,
                   role_id_to_ping = excluded.role_id_to_ping""",
            (guild_id, channel_id, category, condition, min_score, max_items_per_day, role_id_to_ping)
        )
        await db.commit()


async def get_watchlist(guild_id: str, channel_id: str) -> Optional[Dict]:
    """Get watchlist config for a guild+channel"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute(
            "SELECT * FROM guild_watchlists WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def get_all_watchlists() -> List[Dict]:
    """Get all watchlist configurations"""
    db = await get_db()
    async with _lock:
        cursor = await db.execute("SELECT * FROM guild_watchlists")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
