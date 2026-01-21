"""
Handheld image URL overrides from local JSON file.
Provides fallback images when the spreadsheet doesn't have URLs.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, Dict

logger = logging.getLogger(__name__)

_IMAGES_PATH = os.path.join(os.path.dirname(__file__), "handheld_images.json")
_cache: Optional[Dict[str, str]] = None
_mtime: Optional[float] = None


def get_image_url(slug: str) -> Optional[str]:
    """
    Get image URL for a handheld by slug from the override JSON.
    Auto-reloads if the file changes.
    """
    global _cache, _mtime

    try:
        st = os.stat(_IMAGES_PATH)
        if _cache is None or _mtime != st.st_mtime:
            with open(_IMAGES_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
            _mtime = st.st_mtime
    except FileNotFoundError:
        _cache = {}
        _mtime = None
    except Exception as e:
        logger.warning("Failed to load handheld_images.json: %s", e)
        return None

    url = (_cache or {}).get(slug)
    if isinstance(url, str) and url.startswith("http"):
        return url
    return None
