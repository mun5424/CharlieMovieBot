# trivia/providers/quizapi_pool.py - Question pool manager for QuizAPI
# Fetches 20 questions at a time and caches in SQLite for efficiency

import json
import logging
import sqlite3
import time
import aiohttp
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Pool configuration
BATCH_SIZE = 20  # Questions per API request
LOW_POOL_THRESHOLD = 5  # Fetch more when unseen questions drop below this
RATE_LIMIT_COOLDOWN = 3600  # 1 hour cooldown on rate limit

# QuizAPI tags for variety
QUIZAPI_TAGS = [
    "Linux", "DevOps", "Networking", "Programming", "Cloud",
    "Docker", "Kubernetes", "SQL", "CMS", "Code", "bash"
]


class QuizAPIPool:
    """
    Manages a pool of QuizAPI questions stored in SQLite.
    Fetches in batches of 20 to maximize the 50 requests/day limit.
    """

    BASE_URL = "https://quizapi.io/api/v1/questions"

    def __init__(self, api_key: str, db_path: str = "data/quizapi_pool.db"):
        self._api_key = api_key
        self._db_path = db_path
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limited_until: float = 0
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database for question pool"""
        # Ensure directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        # Questions table - stores fetched questions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_hash TEXT UNIQUE,
                question_text TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                incorrect_answers TEXT NOT NULL,
                category TEXT,
                difficulty TEXT,
                tags TEXT,
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

        # Index for faster unseen queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_seen_user ON seen(user_id)
        """)

        conn.commit()
        conn.close()
        logger.info(f"QuizAPI pool database initialized at {self._db_path}")

    async def initialize(self):
        """Create HTTP session"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=5, ttl_dns_cache=300)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': 'TriviaBot/2.0',
                    'X-Api-Key': self._api_key
                }
            )

    async def cleanup(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()

    @property
    def is_rate_limited(self) -> bool:
        """Check if currently rate limited"""
        return self._rate_limited_until > time.time()

    def _get_question_hash(self, question_text: str) -> str:
        """Generate a hash for deduplication"""
        import hashlib
        return hashlib.md5(question_text.encode()).hexdigest()

    def _get_unseen_count(self, user_id: str) -> int:
        """Count how many unseen questions are available for a user"""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM questions q
            WHERE q.id NOT IN (
                SELECT question_id FROM seen WHERE user_id = ?
            )
        """, (user_id,))

        count = cursor.fetchone()[0]
        conn.close()
        return count

    def _get_unseen_count_by_difficulty(self, user_id: str, difficulty: Optional[str]) -> int:
        """Count unseen questions for a user, optionally filtered by difficulty"""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        if difficulty:
            cursor.execute("""
                SELECT COUNT(*) FROM questions q
                WHERE q.difficulty = ?
                AND q.id NOT IN (
                    SELECT question_id FROM seen WHERE user_id = ?
                )
            """, (difficulty.lower(), user_id))
        else:
            cursor.execute("""
                SELECT COUNT(*) FROM questions q
                WHERE q.id NOT IN (
                    SELECT question_id FROM seen WHERE user_id = ?
                )
            """, (user_id,))

        count = cursor.fetchone()[0]
        conn.close()
        return count

    async def _fetch_batch(self, difficulty: Optional[str] = None) -> int:
        """
        Fetch a batch of 20 questions from QuizAPI.
        Returns number of new questions added.
        """
        if self.is_rate_limited:
            logger.warning("QuizAPI pool is rate limited, skipping fetch")
            return 0

        if not self._session:
            await self.initialize()

        params = {"limit": str(BATCH_SIZE)}
        if difficulty:
            params["difficulty"] = difficulty.capitalize()

        try:
            async with self._session.get(self.BASE_URL, params=params) as resp:
                if resp.status == 401:
                    logger.error("QuizAPI authentication failed")
                    return 0

                if resp.status == 429:
                    self._rate_limited_until = time.time() + RATE_LIMIT_COOLDOWN
                    logger.warning(f"QuizAPI rate limited - cooldown for {RATE_LIMIT_COOLDOWN}s")
                    return 0

                if resp.status != 200:
                    logger.warning(f"QuizAPI returned status {resp.status}")
                    return 0

                data = await resp.json()

                if not data or not isinstance(data, list):
                    return 0

                return self._store_questions(data)

        except aiohttp.ClientError as e:
            logger.error(f"QuizAPI fetch failed: {e}")
            return 0
        except Exception as e:
            logger.error(f"QuizAPI unexpected error: {e}")
            return 0

    def _store_questions(self, questions: List[Dict]) -> int:
        """Store fetched questions in database, returns count of new questions"""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        new_count = 0

        for q in questions:
            question_text = q.get("question", "")
            if not question_text:
                continue

            # Parse answers
            answers = q.get("answers", {})
            correct_answers = q.get("correct_answers", {})

            correct_answer = None
            incorrect_answers = []

            for key in ["answer_a", "answer_b", "answer_c", "answer_d", "answer_e", "answer_f"]:
                answer_text = answers.get(key)
                if answer_text is None:
                    continue

                correct_key = f"{key}_correct"
                is_correct = correct_answers.get(correct_key, "false") == "true"

                if is_correct:
                    correct_answer = answer_text
                else:
                    incorrect_answers.append(answer_text)

            # Skip if not enough answers
            if not correct_answer or len(incorrect_answers) < 2:
                continue

            question_hash = self._get_question_hash(question_text)
            difficulty = q.get("difficulty", "Medium").lower()
            if difficulty not in ["easy", "medium", "hard"]:
                difficulty = "medium"

            category = q.get("category", "Programming")
            tags = json.dumps(q.get("tags", []))

            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO questions
                    (question_hash, question_text, correct_answer, incorrect_answers,
                     category, difficulty, tags, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    question_hash,
                    question_text,
                    correct_answer,
                    json.dumps(incorrect_answers[:3]),
                    category,
                    difficulty,
                    tags,
                    time.time()
                ))

                if cursor.rowcount > 0:
                    new_count += 1

            except sqlite3.Error as e:
                logger.error(f"Failed to store question: {e}")

        conn.commit()
        conn.close()

        logger.info(f"Stored {new_count} new questions from QuizAPI batch")
        return new_count

    async def get_question(
        self,
        user_id: str,
        difficulty: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get an unseen question for a user.
        Fetches new batch if pool is running low.
        """
        # Check if we need to fetch more
        unseen_count = self._get_unseen_count_by_difficulty(user_id, difficulty)

        if unseen_count < LOW_POOL_THRESHOLD:
            logger.info(f"Pool low ({unseen_count} unseen), fetching batch...")
            await self._fetch_batch(difficulty)

        # Get an unseen question
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        if difficulty:
            cursor.execute("""
                SELECT id, question_text, correct_answer, incorrect_answers,
                       category, difficulty, tags
                FROM questions q
                WHERE q.difficulty = ?
                AND q.id NOT IN (
                    SELECT question_id FROM seen WHERE user_id = ?
                )
                ORDER BY RANDOM()
                LIMIT 1
            """, (difficulty.lower(), user_id))
        else:
            cursor.execute("""
                SELECT id, question_text, correct_answer, incorrect_answers,
                       category, difficulty, tags
                FROM questions q
                WHERE q.id NOT IN (
                    SELECT question_id FROM seen WHERE user_id = ?
                )
                ORDER BY RANDOM()
                LIMIT 1
            """, (user_id,))

        row = cursor.fetchone()

        if not row:
            conn.close()
            # Try fetching if we haven't yet
            if not self.is_rate_limited:
                await self._fetch_batch(difficulty)
                return await self.get_question(user_id, difficulty)
            return None

        question_id, question_text, correct_answer, incorrect_answers_json, category, diff, tags_json = row

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
            "difficulty": diff,
            "tags": json.loads(tags_json)
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

        # Questions by difficulty
        cursor.execute("""
            SELECT difficulty, COUNT(*) FROM questions GROUP BY difficulty
        """)
        by_difficulty = dict(cursor.fetchall())

        conn.close()

        return {
            "total_questions": total_questions,
            "users_served": users_served,
            "total_questions_served": total_served,
            "by_difficulty": by_difficulty,
            "rate_limited": self.is_rate_limited,
            "rate_limit_remaining": max(0, int(self._rate_limited_until - time.time())) if self.is_rate_limited else 0
        }

    def reset_user_seen(self, user_id: str):
        """Reset seen questions for a user (allows them to see questions again)"""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM seen WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"Reset seen questions for user {user_id}")
