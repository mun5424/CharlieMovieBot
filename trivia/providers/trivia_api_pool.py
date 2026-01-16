# trivia/providers/trivia_api_pool.py - Question pool for The Trivia API
# Fetches 20 questions at a time and caches in SQLite for efficiency and deduplication

import json
import logging
import random
import sqlite3
import time
import aiohttp
from pathlib import Path
from typing import Dict, List, Optional, Any

from trivia.categories import UnifiedCategory

logger = logging.getLogger(__name__)

# Pool configuration
BATCH_SIZE = 20  # Questions per API request
LOW_POOL_THRESHOLD = 5  # Fetch more when unseen questions drop below this

# Map unified categories to The Trivia API categories
CATEGORY_MAP = {
    UnifiedCategory.ENTERTAINMENT: ["film_and_tv", "music"],
    UnifiedCategory.GAMING: [],  # Not supported
    UnifiedCategory.ANIME_MANGA: [],  # Not supported
    UnifiedCategory.SCIENCE_TECH: ["science"],
    UnifiedCategory.HISTORY_GEOGRAPHY: ["history", "geography"],
    UnifiedCategory.ARTS_LITERATURE: ["arts_and_literature"],
    UnifiedCategory.SPORTS: ["sport_and_leisure"],
    UnifiedCategory.NATURE: ["science"],
    UnifiedCategory.GENERAL: ["general_knowledge", "society_and_culture", "food_and_drink"],
}

# Reverse mapping
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


class TriviaAPIPool:
    """
    Manages a pool of The Trivia API questions stored in SQLite.
    Fetches in batches to reduce API calls and tracks seen questions per user.
    """

    BASE_URL = "https://the-trivia-api.com/v2/questions"

    def __init__(self, db_path: str = "data/trivia_api_pool.db"):
        self._db_path = db_path
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_fetch_time: Dict[str, float] = {}  # category -> last fetch timestamp
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database for question pool"""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        # Questions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_hash TEXT UNIQUE,
                question_text TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                incorrect_answers TEXT NOT NULL,
                category TEXT,
                unified_category TEXT,
                difficulty TEXT,
                fetched_at REAL NOT NULL
            )
        """)

        # Seen table - tracks which users have seen which questions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                user_id TEXT NOT NULL,
                question_id INTEGER NOT NULL,
                seen_at REAL NOT NULL,
                PRIMARY KEY (user_id, question_id),
                FOREIGN KEY (question_id) REFERENCES questions(id)
            )
        """)

        # Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_questions_category ON questions(unified_category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_seen_user ON seen(user_id)")

        conn.commit()
        conn.close()
        logger.info(f"Trivia API pool database initialized at {self._db_path}")

    async def initialize(self):
        """Create HTTP session"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=5, ttl_dns_cache=300)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={'User-Agent': 'TriviaBot/2.0'}
            )

    async def cleanup(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_question_hash(self, question_text: str) -> str:
        """Generate a hash for deduplication"""
        import hashlib
        return hashlib.md5(question_text.encode()).hexdigest()

    def _get_unseen_count(self, user_id: str, unified_category: Optional[str] = None, difficulty: Optional[str] = None) -> int:
        """Count unseen questions for a user, optionally filtered"""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        query = """
            SELECT COUNT(*) FROM questions q
            WHERE q.id NOT IN (
                SELECT question_id FROM seen WHERE user_id = ?
            )
        """
        params = [user_id]

        if unified_category:
            query += " AND q.unified_category = ?"
            params.append(unified_category)

        if difficulty:
            query += " AND q.difficulty = ?"
            params.append(difficulty.lower())

        cursor.execute(query, params)
        count = cursor.fetchone()[0]
        conn.close()
        return count

    async def _fetch_batch(self, unified_category: Optional[UnifiedCategory] = None, difficulty: Optional[str] = None) -> int:
        """
        Fetch a batch of questions from The Trivia API.
        Returns number of new questions added.
        """
        if not self._session:
            await self.initialize()

        # Rate limit: don't fetch same category more than once per 30 seconds
        cache_key = f"{unified_category}_{difficulty}"
        last_fetch = self._last_fetch_time.get(cache_key, 0)
        if time.time() - last_fetch < 30:
            logger.debug(f"Skipping fetch for {cache_key} - too recent")
            return 0

        params = {"limit": str(BATCH_SIZE)}

        # Map unified category to API categories
        if unified_category:
            api_categories = CATEGORY_MAP.get(unified_category, [])
            if api_categories:
                params["categories"] = random.choice(api_categories)

        if difficulty and difficulty.lower() in ["easy", "medium", "hard"]:
            params["difficulties"] = difficulty.lower()

        try:
            async with self._session.get(self.BASE_URL, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"The Trivia API returned status {resp.status}")
                    return 0

                data = await resp.json()

                if not data or not isinstance(data, list):
                    return 0

                self._last_fetch_time[cache_key] = time.time()
                return self._store_questions(data, unified_category)

        except aiohttp.ClientError as e:
            logger.error(f"The Trivia API fetch failed: {e}")
            return 0
        except Exception as e:
            logger.error(f"The Trivia API unexpected error: {e}")
            return 0

    def _store_questions(self, questions: List[Dict], requested_category: Optional[UnifiedCategory] = None) -> int:
        """Store fetched questions in database, returns count of new questions"""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        new_count = 0

        for q in questions:
            # Extract question text (can be nested or direct)
            question_obj = q.get("question", {})
            if isinstance(question_obj, dict):
                question_text = question_obj.get("text", "")
            else:
                question_text = str(question_obj)

            if not question_text:
                continue

            correct_answer = q.get("correctAnswer", "")
            incorrect_answers = q.get("incorrectAnswers", [])
            category = q.get("category", "General Knowledge")
            difficulty = q.get("difficulty", "medium").lower()

            # Determine unified category
            if requested_category:
                unified = requested_category.value
            else:
                category_key = category.lower().replace(" ", "_").replace("&", "and")
                unified_enum = API_CATEGORY_TO_UNIFIED.get(category_key, UnifiedCategory.GENERAL)
                unified = unified_enum.value

            question_hash = self._get_question_hash(question_text)

            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO questions
                    (question_hash, question_text, correct_answer, incorrect_answers,
                     category, unified_category, difficulty, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    question_hash,
                    question_text,
                    correct_answer,
                    json.dumps(incorrect_answers[:3]),
                    category.replace("_", " ").title(),
                    unified,
                    difficulty,
                    time.time()
                ))

                if cursor.rowcount > 0:
                    new_count += 1

            except sqlite3.Error as e:
                logger.error(f"Failed to store question: {e}")

        conn.commit()
        conn.close()

        logger.info(f"Stored {new_count} new questions from The Trivia API batch")
        return new_count

    async def get_question(
        self,
        user_id: str,
        unified_category: Optional[UnifiedCategory] = None,
        difficulty: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get an unseen question for a user.
        Fetches new batch if pool is running low.
        """
        category_str = unified_category.value if unified_category else None

        # Check if we need to fetch more
        unseen_count = self._get_unseen_count(user_id, category_str, difficulty)

        if unseen_count < LOW_POOL_THRESHOLD:
            logger.info(f"Pool low ({unseen_count} unseen for {category_str}), fetching batch...")
            await self._fetch_batch(unified_category, difficulty)

        # Get an unseen question
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        query = """
            SELECT id, question_text, correct_answer, incorrect_answers,
                   category, unified_category, difficulty
            FROM questions q
            WHERE q.id NOT IN (
                SELECT question_id FROM seen WHERE user_id = ?
            )
        """
        params = [user_id]

        if category_str:
            query += " AND q.unified_category = ?"
            params.append(category_str)

        if difficulty:
            query += " AND q.difficulty = ?"
            params.append(difficulty.lower())

        query += " ORDER BY RANDOM() LIMIT 1"

        cursor.execute(query, params)
        row = cursor.fetchone()

        if not row:
            conn.close()
            # Try fetching if we haven't recently
            fetched = await self._fetch_batch(unified_category, difficulty)
            if fetched > 0:
                return await self.get_question(user_id, unified_category, difficulty)
            return None

        question_id, question_text, correct_answer, incorrect_answers_json, category, unified_cat, diff = row

        # Mark as seen
        cursor.execute("""
            INSERT OR REPLACE INTO seen (user_id, question_id, seen_at)
            VALUES (?, ?, ?)
        """, (user_id, question_id, time.time()))

        conn.commit()
        conn.close()

        return {
            "question": question_text,
            "correct_answer": correct_answer,
            "incorrect_answers": json.loads(incorrect_answers_json),
            "category": category,
            "unified_category": unified_cat,
            "difficulty": diff
        }

    def get_pool_stats(self) -> Dict[str, Any]:
        """Get statistics about the question pool"""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM questions")
        total_questions = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM seen")
        users_served = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM seen")
        total_served = cursor.fetchone()[0]

        # Questions by category
        cursor.execute("""
            SELECT unified_category, COUNT(*) FROM questions GROUP BY unified_category
        """)
        by_category = dict(cursor.fetchall())

        conn.close()

        return {
            "total_questions": total_questions,
            "users_served": users_served,
            "total_questions_served": total_served,
            "by_category": by_category,
        }

    def reset_user_seen(self, user_id: str):
        """Reset seen questions for a user"""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM seen WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"Reset seen questions for user {user_id}")
