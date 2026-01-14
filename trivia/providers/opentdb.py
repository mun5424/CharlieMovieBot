# trivia/providers/opentdb.py - OpenTDB trivia provider

import html
import random
import logging
import aiohttp
from typing import Dict, List, Optional, Any

from trivia.providers.base import TriviaProvider, StandardQuestion
from trivia.categories import (
    UnifiedCategory,
    OPENTDB_CATEGORY_MAP,
    get_opentdb_ids_for_unified,
)
from trivia.question_cache import QuestionCache
from trivia.models import Difficulty

logger = logging.getLogger(__name__)


class OpenTDBProvider(TriviaProvider):
    """Provider for Open Trivia Database (opentdb.com)"""

    def __init__(self, data_manager=None):
        self._available = True
        self._session: Optional[aiohttp.ClientSession] = None
        self._question_cache = QuestionCache()
        self._data_manager = data_manager

    @property
    def name(self) -> str:
        return "Open Trivia DB"

    @property
    def provider_id(self) -> str:
        return "opentdb"

    @property
    def is_available(self) -> bool:
        return self._available and self._session is not None

    def set_data_manager(self, data_manager):
        """Set the data manager for question tracking"""
        self._data_manager = data_manager

    def get_supported_categories(self) -> List[UnifiedCategory]:
        """OpenTDB supports all unified categories except SF6-specific ones"""
        return [
            UnifiedCategory.ENTERTAINMENT,
            UnifiedCategory.GAMING,
            UnifiedCategory.ANIME_MANGA,
            UnifiedCategory.SCIENCE_TECH,
            UnifiedCategory.HISTORY_GEOGRAPHY,
            UnifiedCategory.ARTS_LITERATURE,
            UnifiedCategory.SPORTS,
            UnifiedCategory.NATURE,
            UnifiedCategory.GENERAL,
        ]

    async def initialize(self) -> None:
        """Create HTTP session for API calls"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=5,
                limit_per_host=2,
                ttl_dns_cache=300,
                use_dns_cache=True
            )
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={'User-Agent': 'TriviaBot/2.0'}
            )
            logger.info("OpenTDB provider session created")

    async def cleanup(self) -> None:
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("OpenTDB provider session closed")

    async def get_question(
        self,
        unified_category: Optional[UnifiedCategory] = None,
        difficulty: Optional[str] = None,
        guild_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[StandardQuestion]:
        """Fetch a question from OpenTDB"""
        if not self._session:
            await self.initialize()

        # Convert unified category to OpenTDB category ID
        category_id = None
        if unified_category:
            opentdb_ids = get_opentdb_ids_for_unified(unified_category)
            if opentdb_ids:
                category_id = random.choice(opentdb_ids)

        # Convert difficulty string to enum
        diff_enum = None
        if difficulty:
            try:
                diff_enum = Difficulty(difficulty.lower())
            except ValueError:
                diff_enum = None

        # Fetch from cache
        question_data = await self._question_cache.get_question(
            self._session,
            category_id,
            diff_enum,
            guild_id,
            user_id,
            self._data_manager
        )

        if not question_data:
            logger.warning("Failed to get question from OpenTDB cache")
            return None

        # Convert to StandardQuestion
        return self._convert_to_standard(question_data, unified_category)

    def _convert_to_standard(
        self,
        question_data: Dict,
        requested_category: Optional[UnifiedCategory]
    ) -> StandardQuestion:
        """Convert OpenTDB format to StandardQuestion"""
        # Decode HTML entities
        question = html.unescape(question_data["question"])
        correct = html.unescape(question_data["correct_answer"])
        incorrect = [html.unescape(i) for i in question_data["incorrect_answers"]]
        category = html.unescape(question_data["category"])
        difficulty = question_data["difficulty"]

        # Determine unified category
        if requested_category:
            unified = requested_category
        else:
            # Try to find the unified category from the response
            unified = UnifiedCategory.GENERAL
            for mapping in OPENTDB_CATEGORY_MAP.values():
                if mapping.provider_name.lower() in category.lower():
                    unified = mapping.unified
                    break

        return StandardQuestion(
            question=question,
            correct_answer=correct,
            incorrect_answers=incorrect,
            category=category,
            unified_category=unified,
            difficulty=difficulty,
            provider=self.provider_id,
        )

    def get_cache_stats(self) -> Dict:
        """Get question cache statistics"""
        return self._question_cache.get_cache_stats()

    def get_statistics(self) -> Dict[str, Any]:
        """Get provider statistics including cache stats"""
        stats = super().get_statistics()
        stats["cache"] = self.get_cache_stats()
        return stats
