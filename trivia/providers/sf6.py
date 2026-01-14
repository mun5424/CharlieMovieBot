# trivia/providers/sf6.py - Street Fighter 6 trivia provider

import logging
from typing import Dict, List, Optional, Any

from trivia.providers.base import TriviaProvider, StandardQuestion
from trivia.categories import UnifiedCategory
from trivia.sf6_trivia import SF6TriviaManager

logger = logging.getLogger(__name__)


class SF6Provider(TriviaProvider):
    """Provider for Street Fighter 6 frame data trivia"""

    def __init__(self, db_path: str = "data/sf6/sf6_trivia.db"):
        self._sf6_manager = SF6TriviaManager(db_path)

    @property
    def name(self) -> str:
        return "Street Fighter 6"

    @property
    def provider_id(self) -> str:
        return "sf6"

    @property
    def is_available(self) -> bool:
        return self._sf6_manager.available

    def get_supported_categories(self) -> List[UnifiedCategory]:
        """SF6 only supports Gaming category"""
        return [UnifiedCategory.GAMING]

    async def initialize(self) -> None:
        """No async initialization needed for SF6"""
        pass

    async def cleanup(self) -> None:
        """No cleanup needed for SF6"""
        pass

    async def get_question(
        self,
        unified_category: Optional[UnifiedCategory] = None,
        difficulty: Optional[str] = None,
        guild_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[StandardQuestion]:
        """Fetch an SF6 trivia question"""
        if not self.is_available:
            logger.warning("SF6 provider not available")
            return None

        # Generate question using the SF6 manager
        sf6_question = self._sf6_manager.generate_question(difficulty)

        if not sf6_question:
            logger.warning("Failed to generate SF6 question")
            return None

        # Convert to StandardQuestion
        return StandardQuestion(
            question=sf6_question.question,
            correct_answer=sf6_question.correct_answer,
            incorrect_answers=sf6_question.incorrect_answers,
            category="Street Fighter 6",
            unified_category=UnifiedCategory.GAMING,
            difficulty=sf6_question.difficulty,
            provider=self.provider_id,
            explanation=sf6_question.explanation,
            extra_data={
                "character": sf6_question.character,
                "move_name": sf6_question.move_name,
                "question_type": sf6_question.question_type,
            }
        )

    def get_characters(self) -> List[str]:
        """Get list of available SF6 characters"""
        return self._sf6_manager.get_characters()

    def get_statistics(self) -> Dict[str, Any]:
        """Get SF6 provider statistics"""
        stats = super().get_statistics()
        if self.is_available:
            stats.update(self._sf6_manager.get_statistics())
        return stats
