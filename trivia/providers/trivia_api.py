# trivia/providers/trivia_api.py - The Trivia API provider (https://the-trivia-api.com)
# Uses pooled question fetching for efficiency and per-user deduplication

import logging
from typing import Dict, List, Optional, Any

from trivia.providers.base import TriviaProvider, StandardQuestion
from trivia.providers.trivia_api_pool import TriviaAPIPool
from trivia.categories import UnifiedCategory

logger = logging.getLogger(__name__)


class TriviaAPIProvider(TriviaProvider):
    """
    Provider for The Trivia API (https://the-trivia-api.com).

    Uses a question pool that fetches 20 questions at a time and tracks
    seen questions per user to prevent duplicates.
    """

    def __init__(self, db_path: str = "data/trivia_api_pool.db"):
        self._pool = TriviaAPIPool(db_path=db_path)
        self._available = True

    @property
    def name(self) -> str:
        return "The Trivia API"

    @property
    def provider_id(self) -> str:
        return "trivia_api"

    @property
    def is_available(self) -> bool:
        return self._available

    def get_supported_categories(self) -> List[UnifiedCategory]:
        """Categories supported by The Trivia API"""
        return [
            UnifiedCategory.ENTERTAINMENT,
            UnifiedCategory.SCIENCE_TECH,
            UnifiedCategory.HISTORY_GEOGRAPHY,
            UnifiedCategory.ARTS_LITERATURE,
            UnifiedCategory.SPORTS,
            UnifiedCategory.NATURE,
            UnifiedCategory.GENERAL,
        ]

    async def initialize(self) -> None:
        """Initialize the question pool"""
        await self._pool.initialize()
        logger.info("The Trivia API provider pool initialized")

    async def cleanup(self) -> None:
        """Cleanup the question pool"""
        await self._pool.cleanup()
        logger.info("The Trivia API provider pool cleaned up")

    async def get_question(
        self,
        unified_category: Optional[UnifiedCategory] = None,
        difficulty: Optional[str] = None,
        guild_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[StandardQuestion]:
        """Fetch a question from the pool"""
        if not user_id:
            logger.warning("No user_id provided to Trivia API provider")
            return None

        # Get question from pool
        question_data = await self._pool.get_question(
            user_id=user_id,
            unified_category=unified_category,
            difficulty=difficulty
        )

        if not question_data:
            logger.warning("Trivia API pool returned no question")
            self._available = False
            return None

        self._available = True
        return self._convert_to_standard(question_data)

    def _convert_to_standard(self, question_data: Dict) -> StandardQuestion:
        """Convert pool question format to StandardQuestion"""
        # Get unified category enum from string
        try:
            unified = UnifiedCategory(question_data["unified_category"])
        except (ValueError, KeyError):
            unified = UnifiedCategory.GENERAL

        return StandardQuestion(
            question=question_data["question"],
            correct_answer=question_data["correct_answer"],
            incorrect_answers=question_data["incorrect_answers"],
            category=question_data["category"],
            unified_category=unified,
            difficulty=question_data["difficulty"],
            provider=self.provider_id,
        )

    def get_statistics(self) -> Dict[str, Any]:
        """Get provider statistics including pool stats"""
        stats = super().get_statistics()
        stats["uses_pool"] = True
        stats["pool"] = self._pool.get_pool_stats()
        return stats

    def reset_user_seen(self, user_id: str):
        """Reset seen questions for a user"""
        self._pool.reset_user_seen(user_id)
