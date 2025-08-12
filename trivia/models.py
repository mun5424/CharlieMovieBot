from enum import Enum
from dataclasses import dataclass
from typing import Dict, List

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

@dataclass
class SeasonSnapshot:
    season_name: str
    end_date: str
    server_name: str
    leaderboard: List[Dict]
    total_players: int
    total_questions_asked: int
    
    def to_dict(self):
        from dataclasses import asdict
        return asdict(self)


class Difficulty(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"