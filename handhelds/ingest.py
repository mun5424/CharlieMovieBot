"""
Fetch Google Sheet as CSV, normalize rows, compute hash, upsert into DB.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

_IMAGE_RE = re.compile(r'=IMAGE\(\s*"([^"]+)"', re.IGNORECASE)


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

from handhelds import db

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=8)


def build_export_url(sheet_id: str, gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}"



def normalize_header(h: str) -> str:
    return " ".join((h or "").strip().split())


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


async def fetch_csv_text(url: str) -> str:
    headers = {
        "User-Agent": "CharlieMovieBot/1.0 (+handhelds ingest)",
        "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8",
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

            lowered = text.lower()
            if "<html" in lowered or "<!doctype html" in lowered or "accounts.google.com" in lowered or "sign in" in lowered:
                raise RuntimeError("Expected CSV but got HTML (login/permission page). Check sharing or endpoint.")

            return text



def parse_rows(csv_text: str) -> List[Dict[str, str]]:
    # Handles quoted fields, commas, etc.
    f = io.StringIO(csv_text)
    reader = csv.DictReader(f)
    rows: List[Dict[str, str]] = []

    for raw in reader:
        # Normalize headers (collapse whitespace)
        row = {normalize_header(k): (v or "").strip() for k, v in raw.items()}

        # Skip fully empty rows
        if not any(v for v in row.values()):
            continue

        rows.append(row)

    return rows


def to_db_rows(sheet_rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    for r in sheet_rows:
        name = pick_field(r, "Handheld", "Name", "Device")
        if not name:
            # If the sheet ever changes headers, you can log one example row
            # but we just skip here.
            continue

        slug = db.slugify(name)
        name_norm = name.strip().lower()

        # Pull a few nice-to-have fields if present
        brand = pick_field(r, "Brand")
        os_ = pick_field(r, "OS")
        released = pick_field(r, "Released")
        form_factor = pick_field(r, "Form Factor")
        performance = pick_field(r, "Performance Rating")
        price_avg = pick_field(r, "Price (average)", "Price")
        vendor_link = pick_field(r, "Vendor Link", "Vendor Link 1", "Vendor Link 2")
        raw_image = pick_field(r, "Image URL", "Image", "Thumbnail", "Photo")
        image_url = extract_image_url(raw_image)

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


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def refresh_from_sheet(sheet_id: str, gid: str) -> Tuple[bool, int]:
    """
    Returns (changed, row_count).
    Uses hash to avoid rewriting DB when unchanged.
    """
    await db.init_db()

    url = build_export_url(sheet_id, gid)
    csv_text = await fetch_csv_text(url)
    new_hash = sha256_text(csv_text)

    old_hash = await db.get_meta("csv_hash")
    if old_hash == new_hash:
        logger.info("Handhelds ingest: no change detected (hash match).")
        await db.set_meta("last_refresh_ok_unix", str(db._now_unix()))
        return (False, 0)

    sheet_rows = parse_rows(csv_text)
    if sheet_rows:
        logger.info("handhelds headers=%s", list(sheet_rows[0].keys()))
    else:
        logger.warning("handhelds parse_rows returned 0 rows")
        rows = to_db_rows(sheet_rows)
    changed_count, total = await db.upsert_many(rows)

    await db.set_meta("csv_hash", new_hash)
    await db.set_meta("last_refresh_ok_unix", str(db._now_unix()))
    await db.set_meta("last_row_count", str(total))
    await db.set_meta("source_url", url)

    logger.info("Handhelds ingest: upserted %s rows (parsed=%s).", changed_count, total)
    return (True, total)
