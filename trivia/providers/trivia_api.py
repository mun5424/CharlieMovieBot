# trivia/providers/trivia_api.py - The Trivia API provider (https://the-trivia-api.com)

import random
import logging
import aiohttp
from typing import Dict, List, Optional, Any

from trivia.providers.base import TriviaProvider, StandardQuestion
from trivia.categories import UnifiedCategory

logger = logging.getLogger(__name__)

# Map unified categories to The Trivia API categories
CATEGORY_MAP = {
    UnifiedCategory.ENTERTAINMENT: ["film_and_tv", "music"],
    UnifiedCategory.GAMING: [],  # Not supported
    UnifiedCategory.ANIME_MANGA: [],  # Not supported
    UnifiedCategory.SCIENCE_TECH: ["science"],
    UnifiedCategory.HISTORY_GEOGRAPHY: ["history", "geography"],
    UnifiedCategory.ARTS_LITERATURE: ["arts_and_literature"],
    UnifiedCategory.SPORTS: ["sport_and_leisure"],
    UnifiedCategory.NATURE: ["science"],  # Closest match
    UnifiedCategory.GENERAL: ["general_knowledge", "society_and_culture", "food_and_drink"],
}

# Reverse mapping for response parsing
API_CATEGORY_TO_UNIFIED = {
    "arts_and_literature": UnifiedCategory.ARTS_LITERATURE,
    "film_and_tv": UnifiedCategory.ENTERTAINMENT,
    "food_and_drink": UnifiedCategory.GENERAL,
    "general_knowledge": UnifiedCategory.GENERAL,
    "geography": UnifiedCategory.HISTORY_GEOGRAPHY,
    "history": UnifiedCategory.HISTORY_GEOGRAPHY,
    "music": UnifiedCategory.ENTERTAINMENT,
    "science": UnifiedCategory.SCIENCE_TECH,
    "society_and_culture": UnifiedCategory.GENERAL,
    "sport_and_leisure": UnifiedCategory.SPORTS,
}


class TriviaAPIProvider(TriviaProvider):
    """Provider for The Trivia API (https://the-trivia-api.com)"""

    BASE_URL = "https://the-trivia-api.com/v2/questions"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._available = True

    @property
    def name(self) -> str:
        return "The Trivia API"

    @property
    def provider_id(self) -> str:
        return "trivia_api"

    @property
    def is_available(self) -> bool:
        return self._available and self._session is not None

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
        """Create HTTP session"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=5, ttl_dns_cache=300)
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={'User-Agent': 'TriviaBot/2.0'}
            )
            logger.info("The Trivia API provider session created")

    async def cleanup(self) -> None:
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("The Trivia API provider session closed")

    async def get_question(
        self,
        unified_category: Optional[UnifiedCategory] = None,
        difficulty: Optional[str] = None,
        guild_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[StandardQuestion]:
        """Fetch a question from The Trivia API"""
        if not self._session:
            await self.initialize()

        # Build query parameters
        params = {"limit": "1"}

        # Map unified category to API categories
        if unified_category:
            api_categories = CATEGORY_MAP.get(unified_category, [])
            if api_categories:
                params["categories"] = random.choice(api_categories)

        # Add difficulty
        if difficulty and difficulty.lower() in ["easy", "medium", "hard"]:
            params["difficulties"] = difficulty.lower()

        try:
            async with self._session.get(self.BASE_URL, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"The Trivia API returned status {resp.status}")
                    self._available = False
                    return None

                data = await resp.json()

                if not data or not isinstance(data, list) or len(data) == 0:
                    logger.warning("The Trivia API returned empty response")
                    return None

                self._available = True
                return self._convert_to_standard(data[0], unified_category)

        except aiohttp.ClientError as e:
            logger.error(f"The Trivia API request failed: {e}")
            self._available = False
            return None
        except Exception as e:
            logger.error(f"The Trivia API unexpected error: {e}")
            return None

    def _convert_to_standard(
        self,
        question_data: Dict,
        requested_category: Optional[UnifiedCategory]
    ) -> StandardQuestion:
        """Convert The Trivia API format to StandardQuestion"""
        # Extract question text (can be nested or direct)
        question_obj = question_data.get("question", {})
        if isinstance(question_obj, dict):
            question_text = question_obj.get("text", "")
        else:
            question_text = str(question_obj)

        correct_answer = question_data.get("correctAnswer", "")
        incorrect_answers = question_data.get("incorrectAnswers", [])
        category = question_data.get("category", "General Knowledge")
        difficulty = question_data.get("difficulty", "medium").lower()

        # Determine unified category
        if requested_category:
            unified = requested_category
        else:
            # Try to map from API category
            category_key = category.lower().replace(" ", "_").replace("&", "and")
            unified = API_CATEGORY_TO_UNIFIED.get(category_key, UnifiedCategory.GENERAL)

        return StandardQuestion(
            question=question_text,
            correct_answer=correct_answer,
            incorrect_answers=incorrect_answers[:3],  # Ensure max 3
            category=category.replace("_", " ").title(),
            unified_category=unified,
            difficulty=difficulty,
            provider=self.provider_id,
        )

    def get_statistics(self) -> Dict[str, Any]:
        """Get provider statistics"""
        stats = super().get_statistics()
        stats["api_url"] = self.BASE_URL
        return stats
