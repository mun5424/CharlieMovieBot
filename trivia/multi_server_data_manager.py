"""
Multi-server data management for trivia bot
"""

from dataclasses import asdict, dataclass
from enum import Enum
import json
import os
import time
import random
from typing import Any, Dict, List
import logging
from trivia.question_tracker import QuestionTracker
from trivia.models import Difficulty, UserStats, SeasonSnapshot

logger = logging.getLogger(__name__)

class Difficulty(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"

@dataclass
class UserStats:
    username: str
    total_score: int = 0
    questions_answered: int = 0
    correct_answers: int = 0
    current_streak: int = 0
    best_streak: int = 0
    avg_response_time: float = 0.0
    difficulty_stats: Dict[str, Dict[str, int]] = None
    seen_question_hashes: set = None  # Set of question_hash+user_id combinations
    
    def __post_init__(self):
        if self.difficulty_stats is None:
            self.difficulty_stats = {
                "easy": {"correct": 0, "total": 0},
                "medium": {"correct": 0, "total": 0},
                "hard": {"correct": 0, "total": 0}
            }
        if self.seen_question_hashes is None:
            self.seen_question_hashes = set()

class MultiServerDataManager:
    """Manages data for multiple Discord servers efficiently"""
    
    def __init__(self):
        self.server_data: Dict[str, Dict[str, UserStats]] = {}
        
        # Initialize question tracker
        self.question_tracker = QuestionTracker()
        
        # Load configuration
        try:
            import config
            self.data_directory = config.TRIVIA_CONFIG.get("data_directory", "data") + "/servers"
            self.hall_of_fame_directory = config.TRIVIA_CONFIG.get("data_directory", "data") + "/hall_of_fame"
            self.save_interval = config.TRIVIA_CONFIG["performance"].get("save_interval", 30)
            self.batch_save_size = config.TRIVIA_CONFIG["performance"].get("batch_save_size", 10)
        except (ImportError, AttributeError, KeyError):
            self.data_directory = "data/servers"
            self.hall_of_fame_directory = "data/hall_of_fame"
            self.save_interval = 30
            self.batch_save_size = 10
        
        self.last_save_time = time.time()
        self.pending_saves = set()  # Track which servers need saving
        
        self.ensure_data_directory()
        self.ensure_hall_of_fame_directory()
        self.load_all_server_data()
    
    def ensure_data_directory(self):
        """Ensure data directory exists"""
        os.makedirs(self.data_directory, exist_ok=True)
    
    def ensure_hall_of_fame_directory(self):
        """Ensure hall of fame directory exists"""
        os.makedirs(self.hall_of_fame_directory, exist_ok=True)
    
    def get_server_file_path(self, guild_id: str) -> str:
        """Get file path for a specific server"""
        return os.path.join(self.data_directory, f"server_{guild_id}.json")
    
    def get_hall_of_fame_file_path(self, guild_id: str) -> str:
        """Get hall of fame file path for a specific server"""
        return os.path.join(self.hall_of_fame_directory, f"hof_{guild_id}.json")
    
    def load_all_server_data(self):
        """Load data for all servers (lazy loading)"""
        try:
            # Just scan for existing server files
            if os.path.exists(self.data_directory):
                server_files = [f for f in os.listdir(self.data_directory) if f.startswith("server_") and f.endswith(".json")]
                logger.info(f"Found {len(server_files)} server data files")
            else:
                logger.info("No existing server data found, starting fresh")
        except Exception as e:
            logger.error(f"Error scanning server data directory: {e}")
    
    def load_server_data(self, guild_id: str) -> Dict[str, UserStats]:
        """Load data for a specific server (lazy loading)"""
        if guild_id in self.server_data:
            return self.server_data[guild_id]
        
        # Load from file
        server_file = self.get_server_file_path(guild_id)
        server_stats = {}
        
        if os.path.exists(server_file):
            try:
                with open(server_file, "r") as f:
                    data = json.load(f)
                    for user_id, stats_dict in data.items():
                        # Handle migration from old seen_questions format
                        if "seen_questions" in stats_dict:
                            # Old format - convert to new format
                            old_seen = stats_dict.pop("seen_questions")
                            stats_dict["seen_question_hashes"] = set()
                            
                        # Handle seen_question_hashes - convert lists back to sets
                        if "seen_question_hashes" in stats_dict:
                            if isinstance(stats_dict["seen_question_hashes"], list):
                                stats_dict["seen_question_hashes"] = set(stats_dict["seen_question_hashes"])
                        
                        server_stats[user_id] = UserStats(**stats_dict)
                logger.info(f"Loaded stats for {len(server_stats)} users in server {guild_id}")
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Error loading server {guild_id} stats: {e}")
                server_stats = {}
        else:
            logger.info(f"No existing data for server {guild_id}, starting fresh")
        
        # Cache in memory
        self.server_data[guild_id] = server_stats
        return server_stats
    
    def save_server_data(self, guild_id: str, immediate: bool = False):
        """Save data for a specific server"""
        if guild_id not in self.server_data:
            return
        
        if immediate:
            self._save_server_immediate(guild_id)
        else:
            # Mark for batched save
            self.pending_saves.add(guild_id)
            self._check_batched_save()
    
    def _save_server_immediate(self, guild_id: str):
        """Immediately save server data - simple version"""
        try:
            server_file = self.get_server_file_path(guild_id)
            server_stats = self.server_data[guild_id]
            
            serializable_stats = {}
            for user_id, stats in server_stats.items():
                stats_dict = asdict(stats)
                # Convert sets to lists for JSON serialization
                if "seen_question_hashes" in stats_dict:
                    if isinstance(stats_dict["seen_question_hashes"], set):
                        stats_dict["seen_question_hashes"] = list(stats_dict["seen_question_hashes"])
                serializable_stats[user_id] = stats_dict
            
            # Direct write (less safe but simpler)
            with open(server_file, "w") as f:
                json.dump(serializable_stats, f, indent=2)
            
            logger.debug(f"Saved stats for server {guild_id}")
            
        except Exception as e:
            logger.error(f"Error saving server {guild_id} stats: {e}")
    

    def _check_batched_save(self):
        """Check if it's time for batched save"""
        current_time = time.time()
        
        if (current_time - self.last_save_time >= self.save_interval and 
            self.pending_saves):
            self._execute_batched_save()
    
    def _execute_batched_save(self):
        """Execute batched save for all pending servers"""
        for guild_id in self.pending_saves.copy():
            self._save_server_immediate(guild_id)
        
        self.pending_saves.clear()
        self.last_save_time = time.time()
    
    def force_save_all(self):
        """Force save all server data immediately"""
        for guild_id in self.server_data:
            self._save_server_immediate(guild_id)
        self.pending_saves.clear()
    
    def get_user_stats(self, guild_id: str, user_id: str, username: str) -> UserStats:
        """Get or create user stats for a specific server"""
        server_stats = self.load_server_data(guild_id)
        
        if user_id not in server_stats:
            server_stats[user_id] = UserStats(username=username)
            logger.debug(f"Created new user stats for {username} in server {guild_id}")
        else:
            # Update username if changed
            if server_stats[user_id].username != username:
                server_stats[user_id].username = username
                logger.debug(f"Updated username for {user_id} in server {guild_id}: {username}")
        
        return server_stats[user_id]
    
    def update_user_stats(self, guild_id: str, user_id: str, difficulty: Difficulty, 
                         is_correct: bool, response_time: float, score_change: int):
        """Update user statistics for a specific server"""
        server_stats = self.load_server_data(guild_id)
        
        if user_id not in server_stats:
            logger.warning(f"User {user_id} not found in server {guild_id} during update")
            return
        
        stats = server_stats[user_id]
        
        # Update basic stats
        stats.questions_answered += 1
        stats.total_score += score_change
        
        # Prevent negative scores - reset to 0 if below zero
        if stats.total_score < 0:
            logger.debug(f"User {user_id} score went below zero ({stats.total_score}), resetting to 0")
            stats.total_score = 0
        
        # Update difficulty stats
        diff_str = difficulty.value
        stats.difficulty_stats[diff_str]["total"] += 1
        
        if is_correct:
            stats.correct_answers += 1
            stats.current_streak += 1
            stats.best_streak = max(stats.best_streak, stats.current_streak)
            stats.difficulty_stats[diff_str]["correct"] += 1
        else:
            stats.current_streak = 0
        
        # Update average response time
        total_time = stats.avg_response_time * (stats.questions_answered - 1) + response_time
        stats.avg_response_time = total_time / stats.questions_answered
        
        # Schedule save
        self.save_server_data(guild_id)
        
        logger.debug(f"Updated stats for {user_id} in server {guild_id}: score={stats.total_score}, streak={stats.current_streak}")
    
    def get_server_leaderboard(self, guild_id: str, limit: int = 10) -> list:
        """Get leaderboard for a specific server"""
        server_stats = self.load_server_data(guild_id)
        
        if not server_stats:
            return []
        
        # Sort by total score
        sorted_users = sorted(
            server_stats.items(), 
            key=lambda x: x[1].total_score, 
            reverse=True
        )[:limit]
        
        return sorted_users
    
    def create_season_snapshot(self, guild_id: str, season_name: str, server_name: str) -> SeasonSnapshot:
        """Create a snapshot of the current season for hall of fame"""
        server_stats = self.load_server_data(guild_id)
        
        # Get full leaderboard (not limited to 10)
        leaderboard_data = []
        if server_stats:
            sorted_users = sorted(
                server_stats.items(), 
                key=lambda x: x[1].total_score, 
                reverse=True
            )
            
            for i, (user_id, stats) in enumerate(sorted_users, 1):
                accuracy = (stats.correct_answers / stats.questions_answered * 100) if stats.questions_answered > 0 else 0
                leaderboard_data.append({
                    "rank": i,
                    "user_id": user_id,
                    "username": stats.username,
                    "total_score": stats.total_score,
                    "questions_answered": stats.questions_answered,
                    "correct_answers": stats.correct_answers,
                    "accuracy": round(accuracy, 1),
                    "best_streak": stats.best_streak,
                    "avg_response_time": round(stats.avg_response_time, 1)
                })
        
        total_questions_asked = sum(stats.questions_answered for stats in server_stats.values())
        
        snapshot = SeasonSnapshot(
            season_name=season_name,
            end_date=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            server_name=server_name,
            leaderboard=leaderboard_data,
            total_players=len(server_stats),
            total_questions_asked=total_questions_asked
        )
        
        return snapshot
    
    def save_season_snapshot(self, guild_id: str, snapshot: SeasonSnapshot):
        """Save a season snapshot to hall of fame"""
        hof_file = self.get_hall_of_fame_file_path(guild_id)
        
        # Load existing hall of fame
        hall_of_fame = []
        if os.path.exists(hof_file):
            try:
                with open(hof_file, "r") as f:
                    hall_of_fame = json.load(f)
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Error loading hall of fame for {guild_id}: {e}")
                hall_of_fame = []
        
        # Add new snapshot
        hall_of_fame.append(snapshot.to_dict())
        
        # Save updated hall of fame
        try:
            temp_file = f"{hof_file}.tmp"
            with open(temp_file, "w") as f:
                json.dump(hall_of_fame, f, indent=2)
            
            os.rename(temp_file, hof_file)
            logger.info(f"Saved season snapshot '{snapshot.season_name}' for server {guild_id}")
            
        except Exception as e:
            logger.error(f"Error saving hall of fame for {guild_id}: {e}")
            raise
    
    def get_hall_of_fame(self, guild_id: str) -> List[SeasonSnapshot]:
        """Get all season snapshots for a server"""
        hof_file = self.get_hall_of_fame_file_path(guild_id)
        
        if not os.path.exists(hof_file):
            return []
        
        try:
            with open(hof_file, "r") as f:
                data = json.load(f)
                return [SeasonSnapshot(**snapshot_data) for snapshot_data in data]
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Error loading hall of fame for {guild_id}: {e}")
            return []
    
    def reset_server_scores(self, guild_id: str):
        """Reset all scores for a server - complete fresh start"""
        server_stats = self.load_server_data(guild_id)
        
        # Clear all user data completely
        server_stats.clear()
        
        # Force immediate save
        self.save_server_data(guild_id, immediate=True)
        logger.info(f"Completely reset server {guild_id} - removed all users from leaderboard")

    
    def mark_question_seen(self, guild_id: str, user_id: str, question_data: Dict):
        """Mark a question as seen by a user"""
        self.question_tracker.mark_question_seen(guild_id, user_id, question_data)
    
    def has_user_seen_question(self, guild_id: str, user_id: str, question_data: Dict) -> bool:
        """Check if a user has seen a question"""
        question_hash = self.question_tracker.create_question_hash(question_data)
        return self.question_tracker.has_user_seen_question(guild_id, user_id, question_hash)
    
    def get_user_question_count(self, guild_id: str, user_id: str) -> int:
        """Get total unique questions seen by user"""
        return self.question_tracker.get_user_question_count(guild_id, user_id)
    
    def get_memory_usage_info(self) -> Dict[str, Any]:
        """Get memory usage information for monitoring"""
        tracker_info = self.question_tracker.get_memory_info()
        
        return {
            "servers_loaded": len(self.server_data),
            "total_users": sum(len(server_stats) for server_stats in self.server_data.values()),
            "pending_saves": len(self.pending_saves),
            "last_save_time": self.last_save_time,
            "question_tracking": tracker_info
        }
    
    def cleanup_memory(self):
        """Clean up memory by removing unused server data"""
        # Clean up old question metadata
        self.question_tracker.cleanup_old_questions()
        
        # Remove servers that haven't been accessed recently
        # This is a simple implementation - could be enhanced with LRU cache
        current_time = time.time()
        
        # For Pi optimization, only keep data for servers with recent activity
        # This is a placeholder - you could implement more sophisticated cleanup
        logger.info("Memory cleanup performed")