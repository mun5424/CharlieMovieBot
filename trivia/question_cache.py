import time
import random
from typing import Dict, Optional, List
from collections import defaultdict
import asyncio
import logging
import aiohttp
from trivia.models import Difficulty

logger = logging.getLogger(__name__)

class QuestionCache:
    """Smart question cache that fetches in bulk and serves instantly"""
    
    def __init__(self, max_total_questions=150):
        self.max_total_questions = max_total_questions
        self.last_request_time = 0
        self.min_interval = 5.1  # 5.1 seconds to be safe
        
        # Cache structure: cache[category_id][difficulty] = [questions...]
        # None keys represent "any" category/difficulty
        self.cache = defaultdict(lambda: defaultdict(list))
        self.cache_lock = asyncio.Lock()
        
        # Track cache stats
        self.cache_hits = 0
        self.cache_misses = 0
        self.last_refill = {}  # Track when each cache bucket was last filled
    
    def _evict_old_cache_if_needed(self):
        """Remove old cache buckets if we're over the limit"""
        total_questions = sum(
            len(questions) 
            for cat_cache in self.cache.values() 
            for questions in cat_cache.values()
        )
        
        if total_questions > self.max_total_questions:
            # Simple strategy: clear oldest cache buckets
            oldest_buckets = sorted(
                self.last_refill.items(), 
                key=lambda x: x[1]  # Sort by timestamp
            )
            
            # Remove oldest until under limit
            for (category_id, diff_key), _ in oldest_buckets:
                if total_questions <= self.max_total_questions:
                    break
                    
                questions = self.cache[category_id][diff_key]
                total_questions -= len(questions)
                self.cache[category_id][diff_key].clear()
                logger.debug(f"Evicted cache bucket: category={category_id}, difficulty={diff_key}")

    async def ensure_rate_limit(self):
        """Ensure we don't violate the 5-second rate limit"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.min_interval:
            wait_time = self.min_interval - time_since_last
            logger.debug(f"Rate limit protection: waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time)
        
        self.last_request_time = time.time()
    
    def _get_cache_key(self, category_id: Optional[int], difficulty: Optional[Difficulty]) -> tuple:
        """Get cache key for category/difficulty combination"""
        diff_key = difficulty.value if difficulty else None
        return (category_id, diff_key)
    
    def _needs_refill(self, category_id: Optional[int], difficulty: Optional[Difficulty]) -> bool:
        """Check if a cache bucket needs refilling"""
        cache_key = self._get_cache_key(category_id, difficulty)
        questions = self.cache[category_id][difficulty.value if difficulty else None]
        
        # Need refill if empty or hasn't been refilled in 30 minutes
        if len(questions) == 0:
            return True
        
        last_refill_time = self.last_refill.get(cache_key, 0)
        return time.time() - last_refill_time > 1800  # 30 minutes
    
    async def fetch_bulk_questions(self, session: aiohttp.ClientSession,
                                 category_id: Optional[int] = None, 
                                 difficulty: Optional[Difficulty] = None,
                                 amount: int = 20) -> List[Dict]:
        """Fetch multiple questions from API in one request"""
        base_url = f"https://opentdb.com/api.php?amount={amount}&type=multiple"
        
        if category_id:
            base_url += f"&category={category_id}"
        if difficulty:
            base_url += f"&difficulty={difficulty.value}"
        
        await self.ensure_rate_limit()
        
        try:
            async with session.get(base_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("response_code") == 0 and data.get("results"):
                        questions = data["results"]
                        logger.info(f"Fetched {len(questions)} questions (category={category_id}, difficulty={difficulty.value if difficulty else 'any'})")
                        return questions
                    else:
                        error_code = data.get("response_code", "unknown")
                        logger.warning(f"API returned error code: {error_code}")
                        return []
                elif resp.status == 429:
                    logger.warning("Rate limited by API - increasing wait time")
                    self.min_interval = min(10.0, self.min_interval + 1.0)
                    return []
                else:
                    logger.error(f"HTTP error: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching bulk questions: {e}")
            return []
    
    async def _try_fetch_more_questions(self, session: aiohttp.ClientSession,
                                      category_id: Optional[int],
                                      difficulty: Optional[Difficulty]) -> bool:
        """Try to fetch more questions for this category/difficulty combo"""
        logger.info(f"Attempting to fetch more questions for category={category_id}, difficulty={difficulty.value if difficulty else 'any'}")
        
        new_questions = await self.fetch_bulk_questions(session, category_id, difficulty, 20)
        
        if new_questions:
            diff_key = difficulty.value if difficulty else None
            
            # Add to existing cache (don't replace, append)
            self.cache[category_id][diff_key].extend(new_questions)
            self.last_refill[self._get_cache_key(category_id, difficulty)] = time.time()
            
            logger.info(f"Successfully fetched {len(new_questions)} more questions")
            return True
        else:
            logger.warning(f"Failed to fetch more questions for category={category_id}, difficulty={difficulty.value if difficulty else 'any'}")
            return False
    
    async def get_question(self, session: aiohttp.ClientSession,
                          category_id: Optional[int] = None,
                          difficulty: Optional[Difficulty] = None,
                          guild_id: Optional[str] = None,
                          user_id: Optional[str] = None,
                          data_manager = None) -> Optional[Dict]:
        """Get a question from cache, refilling if needed"""
        
        async with self.cache_lock:
            self._evict_old_cache_if_needed()

            diff_key = difficulty.value if difficulty else None
            questions = self.cache[category_id][diff_key]
            
            # Check if we need to refill the cache
            if self._needs_refill(category_id, difficulty):
                logger.info(f"Cache needs refill for category={category_id}, difficulty={diff_key}")
                
                # Fetch new questions
                new_questions = await self.fetch_bulk_questions(session, category_id, difficulty, 20)
                
                if new_questions:
                    # Replace cache with new questions
                    self.cache[category_id][diff_key] = new_questions.copy()
                    self.last_refill[self._get_cache_key(category_id, difficulty)] = time.time()
                    questions = new_questions.copy()  # to address race condition 
                    logger.info(f"Refilled cache with {len(new_questions)} questions")
                elif len(questions) == 0:
                    # No questions in cache and failed to fetch - try generic
                    if category_id is not None or difficulty is not None:
                        logger.info("Falling back to generic questions")
                        return await self.get_question(session, None, None, guild_id, user_id, data_manager)
                    else:
                        logger.error("No questions available and failed to fetch generic questions")
                        return None
            
            if not questions:
                self.cache_misses += 1
                return None
            
            self.cache_hits += 1
            
            # Strategy 1: If we have user tracking, try to find unseen question
            if guild_id and user_id and data_manager:
                unseen_questions = []
                
                for question in questions:
                    if not data_manager.has_user_seen_question(guild_id, user_id, question):
                        unseen_questions.append(question)
                
                if unseen_questions:
                    # Found unseen questions - pick random one and remove from cache
                    chosen_question = random.choice(unseen_questions)
                    questions.remove(chosen_question)  # Remove the chosen question from cache
                    logger.debug(f"Served unseen question to user {user_id}")
                    return chosen_question
                else:
                    logger.info(f"User {user_id} has seen all {len(questions)} cached questions - trying to fetch more")
                    
                    # ✅ NEW: Try to fetch more questions after 5-second interval
                    fetch_success = await self._try_fetch_more_questions(session, category_id, difficulty)
                    
                    if fetch_success:
                        # Refresh our questions list after fetching more
                        questions = self.cache[category_id][diff_key]
                        
                        # Try again to find unseen questions
                        new_unseen_questions = []
                        for question in questions:
                            if not data_manager.has_user_seen_question(guild_id, user_id, question):
                                new_unseen_questions.append(question)
                        
                        if new_unseen_questions:
                            chosen_question = random.choice(new_unseen_questions)
                            questions.remove(chosen_question)
                            logger.info(f"Served new unseen question to user {user_id} after refetch")
                            return chosen_question
                        else:
                            logger.warning(f"Even after refetch, user {user_id} has seen all questions. Using fallback.")
                    else:
                        logger.info("Could not fetch more questions, falling back to generic or serving seen question")
                        
                        # Try generic fallback if we're being specific
                        if category_id is not None or difficulty is not None:
                            logger.info("Trying generic questions as fallback")
                            return await self.get_question(session, None, None, guild_id, user_id, data_manager)
            
            # Strategy 2: Just pick a random question from the cache (fallback)
            if questions:
                random_index = random.randint(0, len(questions) - 1)
                chosen_question = questions.pop(random_index) 
                logger.debug("Served random question from cache (fallback)")
                return chosen_question
            
            return None
    
    def get_cache_stats(self) -> Dict:
        """Get cache statistics for debugging"""
        total_questions = 0
        cache_buckets = 0
        
        for category_id in self.cache:
            for difficulty in self.cache[category_id]:
                questions = self.cache[category_id][difficulty]
                if questions:
                    total_questions += len(questions)
                    cache_buckets += 1
        
        return {
            'total_questions': total_questions,
            'cache_buckets': cache_buckets,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'hit_rate': self.cache_hits / (self.cache_hits + self.cache_misses) if (self.cache_hits + self.cache_misses) > 0 else 0
        }