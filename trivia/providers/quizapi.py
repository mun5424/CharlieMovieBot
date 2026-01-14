# trivia/providers/quizapi.py - QuizAPI provider (https://quizapi.io)
# Focused on tech/programming questions - requires API key
# Uses pooled question fetching (20 at a time) for efficiency

import logging
import time
from typing import Dict, List, Optional, Any

from trivia.providers.base import TriviaProvider, StandardQuestion
from trivia.providers.quizapi_pool import QuizAPIPool
from trivia.categories import UnifiedCategory

logger = logging.getLogger(__name__)


class QuizAPIProvider(TriviaProvider):
    """
    Provider for QuizAPI (https://quizapi.io) - Tech/Programming focused.

    Uses a question pool that fetches 20 questions at a time to maximize
    the 50 requests/day free tier limit (up to 1000 questions/day).
    """

    def __init__(self, api_key: Optional[str] = None, db_path: str = "data/quizapi_pool.db"):
        self._api_key = api_key
        self._pool: Optional[QuizAPIPool] = None
        self._available = api_key is not None

        if api_key:
            self._pool = QuizAPIPool(api_key=api_key, db_path=db_path)
            logger.info("QuizAPI provider initialized with question pool")
        else:
            logger.warning("QuizAPI provider disabled - no API key provided")

    @property
    def name(self) -> str:
        return "QuizAPI"

    @property
    def provider_id(self) -> str:
        return "quizapi"

    @property
    def is_available(self) -> bool:
        if not self._available or not self._pool:
            return False
        # Still available even when rate limited - we serve from pool
        return True

    def get_supported_categories(self) -> List[UnifiedCategory]:
        """QuizAPI only supports Science & Tech (programming/DevOps focus)"""
        return [UnifiedCategory.SCIENCE_TECH]

    async def initialize(self) -> None:
        """Initialize the question pool"""
        if self._pool:
            await self._pool.initialize()
            logger.info("QuizAPI provider pool initialized")

    async def cleanup(self) -> None:
        """Cleanup the question pool"""
        if self._pool:
            await self._pool.cleanup()
            logger.info("QuizAPI provider pool cleaned up")

    async def get_question(
        self,
        unified_category: Optional[UnifiedCategory] = None,
        difficulty: Optional[str] = None,
        guild_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[StandardQuestion]:
        """Fetch a question from the QuizAPI pool"""
        if not self._pool or not user_id:
            return None

        # Get question from pool
        question_data = await self._pool.get_question(
            user_id=user_id,
            difficulty=difficulty
        )

        if not question_data:
            logger.warning("QuizAPI pool returned no question")
            return None

        return self._convert_to_standard(question_data)

    def _convert_to_standard(self, question_data: Dict) -> StandardQuestion:
        """Convert pool question format to StandardQuestion"""
        return StandardQuestion(
            question=question_data["question"],
            correct_answer=question_data["correct_answer"],
            incorrect_answers=question_data["incorrect_answers"],
            category=f"Tech: {question_data['category']}",
            unified_category=UnifiedCategory.SCIENCE_TECH,
            difficulty=question_data["difficulty"],
            provider=self.provider_id,
            extra_data={
                "tags": question_data.get("tags", []),
            }
        )

    def get_statistics(self) -> Dict[str, Any]:
        """Get provider statistics including pool stats"""
        stats = super().get_statistics()
        stats["api_key_configured"] = self._api_key is not None
        stats["focus"] = "Tech/Programming"
        stats["uses_pool"] = True

        if self._pool:
            pool_stats = self._pool.get_pool_stats()
            stats["pool"] = pool_stats

        return stats

    def reset_user_seen(self, user_id: str):
        """Reset seen questions for a user"""
        if self._pool:
            self._pool.reset_user_seen(user_id)
