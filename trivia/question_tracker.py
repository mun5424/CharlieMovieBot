"""
Question tracking system for preventing duplicate trivia questions
"""

from typing import Dict, Any
import hashlib
import logging
import time

logger = logging.getLogger(__name__)

class QuestionTracker:
    """Efficient question tracking system for preventing duplicates"""
    
    def __init__(self):
        # Global question pool - maps question_hash to question metadata
        self.question_pool: Dict[str, Dict] = {}
        
        # User-question tracking using Bloom filter concept
        # Maps guild_id -> user_id -> set of compact question hashes
        self.user_seen_questions: Dict[str, Dict[str, set]] = {}

        # For very active users, we'll use a more memory-efficient approach
        self.MAX_TRACKED_QUESTIONS = 500  # Reduced from 1000 for Pi memory
    
    def create_question_hash(self, question_data: Dict) -> str:
        """Create a compact, unique hash for a question"""
        # Use question text + correct answer to create unique identifier
        question_text = question_data.get("question", "")
        correct_answer = question_data.get("correct_answer", "")
        
        # Create a compact hash (8 chars should be sufficient for uniqueness)
        content = f"{question_text}|{correct_answer}".encode('utf-8')
        return hashlib.md5(content).hexdigest()[:8]
    
    def create_user_question_hash(self, user_id: str, question_hash: str) -> str:
        """Create a user-specific question hash for tracking"""
        # Combine user_id + question_hash for a unique identifier
        content = f"{user_id}|{question_hash}".encode('utf-8')
        return hashlib.md5(content).hexdigest()[:12]
    
    def has_user_seen_question(self, guild_id: str, user_id: str, question_hash: str) -> bool:
        """Check if a user has seen a specific question"""
        if guild_id not in self.user_seen_questions:
            return False
        
        if user_id not in self.user_seen_questions[guild_id]:
            return False
        
        user_question_hash = self.create_user_question_hash(user_id, question_hash)
        return user_question_hash in self.user_seen_questions[guild_id][user_id]
    
    def mark_question_seen(self, guild_id: str, user_id: str, question_data: Dict):
        """Mark a question as seen by a user"""
        question_hash = self.create_question_hash(question_data)
        user_question_hash = self.create_user_question_hash(user_id, question_hash)
        
        # Initialize nested dictionaries if needed
        if guild_id not in self.user_seen_questions:
            self.user_seen_questions[guild_id] = {}
        
        if user_id not in self.user_seen_questions[guild_id]:
            self.user_seen_questions[guild_id][user_id] = set()
        
        user_seen_set = self.user_seen_questions[guild_id][user_id]
        
        # Memory management for very active users
        if len(user_seen_set) >= self.MAX_TRACKED_QUESTIONS:
            # Remove oldest 20% of questions (simple FIFO approach)
            # Convert to list, remove first 20%, convert back to set
            seen_list = list(user_seen_set)
            keep_count = int(self.MAX_TRACKED_QUESTIONS * 0.8)
            self.user_seen_questions[guild_id][user_id] = set(seen_list[-keep_count:])
            logger.info(f"Pruned question history for user {user_id} in guild {guild_id}")
        
        # Add the new question
        self.user_seen_questions[guild_id][user_id].add(user_question_hash)
        
        # Also store question metadata for potential future use
        self.question_pool[question_hash] = {
            "question": question_data.get("question", ""),
            "category": question_data.get("category", ""),
            "difficulty": question_data.get("difficulty", ""),
            "first_seen": time.time()
        }
    
    def get_user_question_count(self, guild_id: str, user_id: str) -> int:
        """Get total number of unique questions seen by user"""
        if (guild_id not in self.user_seen_questions or 
            user_id not in self.user_seen_questions[guild_id]):
            return 0
        
        return len(self.user_seen_questions[guild_id][user_id])
    
    def cleanup_old_questions(self):
        """Clean up old question metadata (called periodically)"""
        current_time = time.time()
        cutoff_time = current_time - (7 * 24 * 60 * 60)  # 7 days (reduced from 30 for Pi)
        
        old_questions = [
            qhash for qhash, data in self.question_pool.items()
            if data.get("first_seen", 0) < cutoff_time
        ]
        
        for qhash in old_questions:
            del self.question_pool[qhash]
        
        if old_questions:
            logger.info(f"Cleaned up {len(old_questions)} old question metadata entries")
    
    def get_memory_info(self) -> Dict[str, Any]:
        """Get memory usage information"""
        total_user_questions = sum(
            len(users.get(user_id, set()))
            for users in self.user_seen_questions.values()
            for user_id in users
        )
        
        return {
            "question_pool_size": len(self.question_pool),
            "total_tracked_servers": len(self.user_seen_questions),
            "total_user_question_records": total_user_questions,
            "average_questions_per_user": (
                total_user_questions / max(1, sum(len(users) for users in self.user_seen_questions.values()))
            )
        }
