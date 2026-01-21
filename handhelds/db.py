"""
Fetch Google Sheet as CSV, normalize rows, compute hash, upsert into DB.
Also extracts embedded images from HTML export and backfills image_url.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import aiosqlite
import os
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from handhelds import db
from handhelds.ingest_images import extract_images_from_html

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=8)

_IMAGE_RE = re.compile(r'=IMAGE\(\s*"([^"]+)"', re.IGNORECASE)

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "handhelds.db")
# -------------------------
# URL builders
# -------------------------

def build_export_url(sheet_id: str, gid: str) -> str:
    """CSV export URL."""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}"


def build_html_url(sheet_id: str, gid: str) -> str:
    """HTML export URL (needed for embedded images)."""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:html&gid={gid}"


# -------------------------
# Helpers
# -------------------------

def norm_name(s: str) -> str:
    """Normalize names consistently across CSV + HTML extraction."""
    return " ".join(str(s or "").strip().lower().split())


def normalize_header(h: str) -> str:
    return " ".join((h or "").strip().split())


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_json(obj: Any) -> str:
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def extract_image_url(value: str | None) -> str | None:
    """Extract image URL from raw value or =IMAGE("...") formula."""
    if not value:
        return None
    v = value.strip()
    if v.startswith("http"):
        return v
    m = _IMAGE_RE.search(v)
    if m:
        return m.group(1).strip()
    return None


def pick_field(row: Dict[str, str], *candidates: str) -> Optional[str]:
    # Try exact header matches first, then case-insensitive
    for c in candidates:
        if c in row and row[c].strip():
            return row[c].strip()

    lowered = {k.lower(): k for k in row.keys()}
    for c in candidates:
        k = lowered.get(c.lower())
        if k and row[k].strip():
            return row[k].strip()

    return None


# -------------------------
# Fetchers
# -------------------------

async def fetch_text(url: str, *, expect_html: bool) -> str:
    headers = {
        "User-Agent": "CharlieMovieBot/1.0 (+handhelds ingest)",
        "Accept": "text/html,text/csv,text/plain;q=0.9,*/*;q=0.8",
    }

    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            text = await resp.text()
            ctype = (resp.headers.get("Content-Type") or "").lower()

            logger.info(
                "handhelds fetch: status=%s ctype=%s final_url=%s len=%s head=%r",
                resp.status, ctype, str(resp.url), len(text), text[:200]
            )
            resp.raise_for_status()

            if not expect_html:
                lowered = text.lower()
                if ("<html" in lowered) or ("<!doctype html" in lowered) or ("accounts.google.com" in lowered) or ("sign in" in lowered):
                    raise RuntimeError("Expected CSV but got HTML/login page. Check sharing or endpoint.")

            return text


async def fetch_csv_text(url: str) -> str:
    return await fetch_text(url, expect_html=False)


async def fetch_html_text(url: str) -> str:
    return await fetch_text(url, expect_html=True)


# -------------------------
# Parsing
# -------------------------

def parse_rows(csv_text: str) -> List[Dict[str, str]]:
    f = io.StringIO(csv_text)
    reader = csv.DictReader(f)
    rows: List[Dict[str, str]] = []

    for raw in reader:
        # raw can include None keys sometimes; handle safely
        row: Dict[str, str] = {}
        for k, v in raw.items():
            key = normalize_header(k or "")
            if not key:
                continue
            if isinstance(v, list):
                val = " ".join(x.strip() for x in v if x)
            else:
                val = (v or "").strip()
            row[key] = val

        if not any(v for v in row.values()):
            continue

        rows.append(row)

    return rows


def to_db_rows(sheet_rows: List[Dict[str, str]], image_map: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    image_map = image_map or {}

    for r in sheet_rows:
        name = pick_field(
            r,
            "Handheld (Hover for latest updates)",
            "Handheld",
            "Name",
            "Device",
        )
        if not name:
            continue

        slug = db.slugify(name)
        name_norm = norm_name(name)

        brand = pick_field(r, "Brand")
        os_ = pick_field(r, "OS")
        released = pick_field(r, "Released")
        form_factor = pick_field(r, "Form Factor")
        performance = pick_field(r, "Performance Rating", "Performance Rating (Hover for legend)")
        price_avg = pick_field(r, "Price (average)", "Price (average) (Best prices)", "Price")
        vendor_link = pick_field(r, "Vendor Link", "Vendor Link 1", "Vendor Link 2")

        raw_image = pick_field(r, "Image URL", "Image", "Thumbnail", "Photo")
        image_url = extract_image_url(raw_image)
        if not image_url:
            image_url = image_map.get(name_norm)

        data_json = json.dumps(r, ensure_ascii=False)

        out.append({
            "slug": slug,
            "name": name.strip(),
            "name_norm": name_norm,
            "brand": brand,
            "os": os_,
            "released": released,
            "form_factor": form_factor,
            "performance": performance,
            "price_avg": price_avg,
            "vendor_link": vendor_link,
            "image_url": image_url,
            "data_json": data_json,
        })

    return out


# -------------------------
# Main refresh
# -------------------------

async def refresh_from_sheet(sheet_id: str, gid: str) -> Tuple[bool, int]:
    """
    Returns (changed, row_count).
    changed=True if we upserted rows OR we updated any image_url values.
    row_count is the number of handheld rows parsed (not image updates).
    """
    await db.init_db()

    # --- Fetch CSV (data)
    csv_url = build_export_url(sheet_id, gid)
    csv_text = await fetch_csv_text(csv_url)
    csv_hash = sha256_text(csv_text)

    old_csv_hash = await db.get_meta("csv_hash")
    csv_changed = (old_csv_hash != csv_hash)

    # --- Fetch HTML (images) ALWAYS (best effort)
    html_url = build_html_url(sheet_id, gid)
    html_text = ""
    image_map: Dict[str, str] = {}
    image_hash: Optional[str] = None

    try:
        html_text = await fetch_html_text(html_url)
        raw_map = extract_images_from_html(html_text)

        # normalize keys to match CSV name_norm
        image_map = {norm_name(k): v for k, v in raw_map.items() if k and isinstance(v, str) and v.startswith("http")}

        image_hash = sha256_json(image_map)

        logger.info("Handhelds ingest: html_len=%d extracted_images=%d", len(html_text), len(image_map))
        if image_map:
            k = next(iter(image_map))
            logger.info("Handhelds ingest: image sample: %r -> %s", k, image_map[k][:80])
        else:
            logger.warning("Handhelds ingest: image_map EMPTY (HTML format mismatch?)")
            logger.info("Handhelds ingest: html_head=%r", html_text[:500])
    except Exception as e:
        logger.warning("Handhelds ingest: failed to extract images from HTML: %s", e)

    old_img_hash = await db.get_meta("image_hash")
    img_changed = (image_hash is not None and old_img_hash != image_hash)

    # --- If CSV changed: full upsert (with images merged in)
    if csv_changed:
        sheet_rows = parse_rows(csv_text)
        rows = to_db_rows(sheet_rows, image_map=image_map)

        if not rows:
            headers = list(sheet_rows[0].keys()) if sheet_rows else []
            raise RuntimeError(f"Parsed {len(sheet_rows)} sheet rows but mapped 0 DB rows. Headers sample: {headers[:10]}")

        changed_count, total = await db.upsert_many(rows)

        await db.set_meta("csv_hash", csv_hash)
        if image_hash is not None and image_map:
            await db.set_meta("image_hash", image_hash)

        await db.set_meta("last_refresh_ok_unix", str(db._now_unix()))
        await db.set_meta("last_row_count", str(total))
        await db.set_meta("source_url", csv_url)

        logger.info("Handhelds ingest: CSV changed; upserted=%d total=%d", changed_count, total)
        return (True, total)

    # --- CSV unchanged: maybe still update images
    # Backfill missing images at least once (even if image hash is stable)
    missing = 0
    try:
        missing = await db.count_missing_images()
    except Exception:
        # If you haven't implemented it yet, don't crash
        missing = 0

    should_backfill = missing > 0
    if (img_changed or should_backfill) and image_map:
        updated = await db.update_images_by_name_norm(image_map)

        if image_hash is not None and image_map:
            await db.set_meta("image_hash", image_hash)

        await db.set_meta("last_refresh_ok_unix", str(db._now_unix()))

        logger.info(
            "Handhelds ingest: CSV unchanged; images updated=%d (img_changed=%s backfill_missing=%d)",
            updated, img_changed, missing
        )
        return (updated > 0, 0)

    logger.info("Handhelds ingest: no changes detected (CSV unchanged; images unchanged; missing_images=%d).", missing)
    await db.set_meta("last_refresh_ok_unix", str(db._now_unix()))
    return (False, 0)

async def count_missing_images() -> int:
    """
    Returns how many handheld rows are missing an image_url.
    """
    await db.init_db()

    async with aiosqlite.connect(DB_FILE) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM handhelds WHERE image_url IS NULL OR image_url = ''"
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0