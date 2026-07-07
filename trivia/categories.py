# trivia/categories.py - Unified category system for multi-provider trivia

from enum import Enum
from typing import Dict, List, Optional
from dataclasses import dataclass


class UnifiedCategory(Enum):
    """Broad unified categories that map across all providers"""
    ENTERTAINMENT = "Entertainment"
    GAMING = "Gaming"
    ANIME_MANGA = "Anime & Manga"
    SCIENCE_TECH = "Science & Tech"
    HISTORY_GEOGRAPHY = "History & Geography"
    ARTS_LITERATURE = "Arts & Literature"
    SPORTS = "Sports"
    NATURE = "Nature"
    GENERAL = "General Knowledge"


@dataclass
class CategoryMapping:
    """Maps a provider-specific category to unified category"""
    provider_id: any  # Provider-specific ID (int for OpenTDB, str for others)
    provider_name: str  # Display name in provider
    unified: UnifiedCategory  # Which unified category this belongs to


# OpenTDB category IDs mapped to unified categories
OPENTDB_CATEGORY_MAP: Dict[int, CategoryMapping] = {
    9: CategoryMapping(9, "General Knowledge", UnifiedCategory.GENERAL),
    10: CategoryMapping(10, "Books", UnifiedCategory.ARTS_LITERATURE),
    11: CategoryMapping(11, "Film", UnifiedCategory.ENTERTAINMENT),
    12: CategoryMapping(12, "Music", UnifiedCategory.ENTERTAINMENT),
    14: CategoryMapping(14, "Television", UnifiedCategory.ENTERTAINMENT),
    15: CategoryMapping(15, "Video Games", UnifiedCategory.GAMING),
    17: CategoryMapping(17, "Science", UnifiedCategory.SCIENCE_TECH),
    18: CategoryMapping(18, "Computers", UnifiedCategory.SCIENCE_TECH),
    19: CategoryMapping(19, "Math", UnifiedCategory.SCIENCE_TECH),
    20: CategoryMapping(20, "Mythology", UnifiedCategory.ARTS_LITERATURE),
    21: CategoryMapping(21, "Sports", UnifiedCategory.SPORTS),
    22: CategoryMapping(22, "Geography", UnifiedCategory.HISTORY_GEOGRAPHY),
    23: CategoryMapping(23, "History", UnifiedCategory.HISTORY_GEOGRAPHY),
    24: CategoryMapping(24, "Politics", UnifiedCategory.HISTORY_GEOGRAPHY),
    25: CategoryMapping(25, "Art", UnifiedCategory.ARTS_LITERATURE),
    26: CategoryMapping(26, "Celebrities", UnifiedCategory.ENTERTAINMENT),
    27: CategoryMapping(27, "Animals", UnifiedCategory.NATURE),
    28: CategoryMapping(28, "Vehicles", UnifiedCategory.SPORTS),
    29: CategoryMapping(29, "Comics", UnifiedCategory.ENTERTAINMENT),
    30: CategoryMapping(30, "Gadgets", UnifiedCategory.SCIENCE_TECH),
    31: CategoryMapping(31, "Anime & Manga", UnifiedCategory.ANIME_MANGA),
    32: CategoryMapping(32, "Cartoons", UnifiedCategory.ENTERTAINMENT),
}


def get_unified_categories() -> List[str]:
    """Get list of all unified category names for autocomplete"""
    return [cat.value for cat in UnifiedCategory]


def get_opentdb_ids_for_unified(unified: UnifiedCategory) -> List[int]:
    """Get all OpenTDB category IDs that map to a unified category"""
    return [
        mapping.provider_id
        for mapping in OPENTDB_CATEGORY_MAP.values()
        if mapping.unified == unified
    ]


def get_unified_from_opentdb(category_id: int) -> Optional[UnifiedCategory]:
    """Get the unified category for an OpenTDB category ID"""
    mapping = OPENTDB_CATEGORY_MAP.get(category_id)
    return mapping.unified if mapping else None


def get_category_display_info() -> Dict[str, Dict]:
    """Get display info for all unified categories with their sub-categories"""
    info = {}
    for unified in UnifiedCategory:
        sub_categories = [
            mapping.provider_name
            for mapping in OPENTDB_CATEGORY_MAP.values()
            if mapping.unified == unified
        ]

        info[unified.value] = {
            "emoji": CATEGORY_EMOJIS.get(unified, "❓"),
            "sub_categories": sub_categories
        }
    return info


# Emojis for each unified category
CATEGORY_EMOJIS = {
    UnifiedCategory.ENTERTAINMENT: "🎬",
    UnifiedCategory.GAMING: "🎮",
    UnifiedCategory.ANIME_MANGA: "🎌",
    UnifiedCategory.SCIENCE_TECH: "🔬",
    UnifiedCategory.HISTORY_GEOGRAPHY: "🌍",
    UnifiedCategory.ARTS_LITERATURE: "📚",
    UnifiedCategory.SPORTS: "⚽",
    UnifiedCategory.NATURE: "🦁",
    UnifiedCategory.GENERAL: "🧠",
}


def get_category_emoji(unified: UnifiedCategory) -> str:
    """Get emoji for a unified category"""
    return CATEGORY_EMOJIS.get(unified, "❓")
