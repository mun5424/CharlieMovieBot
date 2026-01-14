# trivia/providers/base.py - Abstract base class for trivia providers

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import logging

from trivia.categories import UnifiedCategory

logger = logging.getLogger(__name__)


@dataclass
class StandardQuestion:
    """Standard question format that all providers must produce"""
    question: str
    correct_answer: str
    incorrect_answers: List[str]
    category: str  # Display category name
    unified_category: UnifiedCategory
    difficulty: str  # easy, medium, hard
    provider: str  # Which provider this came from

    # Optional metadata
    explanation: Optional[str] = None
    extra_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary format for compatibility"""
        return {
            "question": self.question,
            "correct_answer": self.correct_answer,
            "incorrect_answers": self.incorrect_answers,
            "category": self.category,
            "unified_category": self.unified_category.value,
            "difficulty": self.difficulty,
            "provider": self.provider,
            "explanation": self.explanation,
            **self.extra_data
        }


class TriviaProvider(ABC):
    """Abstract base class for trivia question providers"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name"""
        pass

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider"""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is ready to serve questions"""
        pass

    @abstractmethod
    def get_supported_categories(self) -> List[UnifiedCategory]:
        """Get list of unified categories this provider supports"""
        pass

    @abstractmethod
    async def get_question(
        self,
        unified_category: Optional[UnifiedCategory] = None,
        difficulty: Optional[str] = None,
        guild_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[StandardQuestion]:
        """
        Fetch a question from this provider.

        Args:
            unified_category: The unified category to fetch from
            difficulty: easy, medium, or hard
            guild_id: Server ID for question tracking
            user_id: User ID for duplicate checking

        Returns:
            StandardQuestion or None if unavailable
        """
        pass

    async def initialize(self) -> None:
        """Optional initialization (e.g., create HTTP session)"""
        pass

    async def cleanup(self) -> None:
        """Optional cleanup (e.g., close HTTP session)"""
        pass

    def get_statistics(self) -> Dict[str, Any]:
        """Get provider-specific statistics"""
        return {
            "name": self.name,
            "available": self.is_available,
            "categories": [cat.value for cat in self.get_supported_categories()]
        }
