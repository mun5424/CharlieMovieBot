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
from handhelds.ingest_images import extract_images_from_html
from handhelds.retrocatalog import resolve_retrocatalog

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=8)


def build_export_url(sheet_id: str, gid: str) -> str:
    """CSV export URL."""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}"


def build_html_url(sheet_id: str, gid: str) -> str:
    """HTML export URL (needed for embedded images)."""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:html&gid={gid}"



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


async def fetch_text(url: str, expect_html: bool = False) -> str:
    """Fetch text from URL. Set expect_html=True for HTML exports."""
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

            lowered = text.lower()
            # Check for login page (only matters for CSV, HTML export is expected to have html tags)
            if not expect_html:
                if "<html" in lowered or "<!doctype html" in lowered or "accounts.google.com" in lowered or "sign in" in lowered:
                    raise RuntimeError("Expected CSV but got HTML (login/permission page). Check sharing or endpoint.")

            return text


async def fetch_csv_text(url: str) -> str:
    """Fetch CSV text from URL."""
    return await fetch_text(url, expect_html=False)


async def fetch_html_text(url: str) -> str:
    """Fetch HTML text from URL."""
    return await fetch_text(url, expect_html=True)



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


def to_db_rows(sheet_rows: List[Dict[str, str]], image_map: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Convert sheet rows to DB rows.

    Args:
        sheet_rows: Parsed CSV rows
        image_map: Optional dict mapping lowercase name -> image URL (from HTML extraction)
    """
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

        # Try CSV image field first, then fall back to HTML-extracted image
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


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def resolve_missing_images_from_retrocatalog() -> int:
    """
    Fetch images from retrocatalog.com for handhelds missing images.
    Returns the number of images successfully resolved.
    """
    missing = await db.get_handhelds_missing_images()
    if not missing:
        logger.info("RetroCatalog: no handhelds missing images")
        return 0

    logger.info("RetroCatalog: attempting to resolve images for %d handhelds", len(missing))
    resolved = 0

    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        for item in missing:
            name = item["name"]
            slug = item["slug"]

            try:
                hit = await resolve_retrocatalog(name, session)
                if hit and hit.image_url:
                    updated = await db.update_image_by_slug(slug, hit.image_url)
                    if updated:
                        resolved += 1
                        logger.info("RetroCatalog: resolved image for %s -> %s", name, hit.image_url)
                    else:
                        logger.debug("RetroCatalog: found image for %s but DB update skipped", name)
                else:
                    logger.debug("RetroCatalog: no image found for %s", name)
            except Exception as e:
                logger.warning("RetroCatalog: error resolving %s: %s", name, e)

    logger.info("RetroCatalog: resolved %d/%d missing images", resolved, len(missing))
    return resolved


def sha256_json(obj: Any) -> str:
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def refresh_from_sheet(sheet_id: str, gid: str) -> Tuple[bool, int]:
    await db.init_db()

    csv_url = build_export_url(sheet_id, gid)
    csv_text = await fetch_csv_text(csv_url)
    csv_hash = sha256_text(csv_text)

    # Always try to fetch images (because images can change even if CSV doesn't)
    image_map: Dict[str, str] = {}
    image_hash: str | None = None
    try:
        html_url = build_html_url(sheet_id, gid)
        html_text = await fetch_html_text(html_url)
        image_map = extract_images_from_html(html_text)
        if len(image_map) == 0:
            # log a tiny diagnostic: how many hrefs exist in the whole doc?
            href_count = html_text.lower().count("href=")
            a_count = html_text.lower().count("<a ")
            logger.info("Handhelds ingest: html diagnostics: <a count=%d href count=%d", a_count, href_count)
            logger.info("Handhelds ingest: html snippet around first href: %r", html_text[:2000])
        image_hash = sha256_json(image_map)
        logger.info("Handhelds ingest: extracted %d images from HTML", len(image_map))
    except Exception as e:
        logger.warning("Handhelds ingest: failed to extract images from HTML: %s", e)

    old_csv_hash = await db.get_meta("csv_hash")
    old_img_hash = await db.get_meta("image_hash")

    csv_changed = (old_csv_hash != csv_hash)
    img_changed = (image_hash is not None and old_img_hash != image_hash)

    # If CSV changed, do full ingest/upsert
    if csv_changed:
        sheet_rows = parse_rows(csv_text)
        rows = to_db_rows(sheet_rows, image_map=image_map)

        if not rows:
            headers = list(sheet_rows[0].keys()) if sheet_rows else []
            raise RuntimeError(f"Parsed {len(sheet_rows)} sheet rows but mapped 0 DB rows. Headers sample: {headers[:8]}")

        changed_count, total = await db.upsert_many(rows)

        await db.set_meta("csv_hash", csv_hash)
        if image_hash is not None:
            await db.set_meta("image_hash", image_hash)

        await db.set_meta("last_refresh_ok_unix", str(db._now_unix()))
        await db.set_meta("last_row_count", str(total))
        await db.set_meta("source_url", csv_url)

        logger.info("Handhelds ingest: upserted %s rows (parsed=%s).", changed_count, total)

        # Try to fill in missing images from retrocatalog.com
        retro_resolved = await resolve_missing_images_from_retrocatalog()
        if retro_resolved:
            logger.info("Handhelds ingest: resolved %d images from RetroCatalog", retro_resolved)

        return (True, total)

    # If CSV NOT changed but images changed, update images only
    if img_changed and image_map:
        updated = await db.update_images_by_name_norm(image_map)
        await db.set_meta("image_hash", image_hash)
        await db.set_meta("last_refresh_ok_unix", str(db._now_unix()))
        logger.info("Handhelds ingest: updated %d image URLs (CSV unchanged).", updated)

        # Try to fill in missing images from retrocatalog.com
        retro_resolved = await resolve_missing_images_from_retrocatalog()
        if retro_resolved:
            logger.info("Handhelds ingest: resolved %d images from RetroCatalog", retro_resolved)

        return (True, 0)

    logger.info("Handhelds ingest: html length=%d", len(html_text))
    logger.info("Handhelds ingest: image_map size=%d", len(image_map))
    if image_map:
        k = next(iter(image_map))
        logger.info("Handhelds ingest: sample image: %r -> %s", k, image_map[k][:80])
    else:
        logger.warning("Handhelds ingest: image_map EMPTY (HTML structure mismatch)")

    logger.info("Handhelds ingest: no changes detected (CSV and images).")
    await db.set_meta("last_refresh_ok_unix", str(db._now_unix()))

    # Still try to fill in missing images from retrocatalog.com
    retro_resolved = await resolve_missing_images_from_retrocatalog()
    if retro_resolved:
        logger.info("Handhelds ingest: resolved %d images from RetroCatalog", retro_resolved)
        return (True, 0)  # Mark as changed since we updated images

    return (False, 0)


