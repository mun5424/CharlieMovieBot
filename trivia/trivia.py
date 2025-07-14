# trivia/trivia.py - Multi-server trivia system

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
import html
import json
import random
import os
import asyncio
import time
import logging
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)

# Configuration
QUESTION_TIMEOUT = 30

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
    
    def __post_init__(self):
        if self.difficulty_stats is None:
            self.difficulty_stats = {
                "easy": {"correct": 0, "total": 0},
                "medium": {"correct": 0, "total": 0},
                "hard": {"correct": 0, "total": 0}
            }

# Import config for scoring configuration
try:
    import config
    SCORING_CONFIG = config.TRIVIA_CONFIG["scoring"]
    QUESTION_TIMEOUT = config.TRIVIA_CONFIG.get("question_timeout", 30)
except (ImportError, KeyError) as e:
    logger.warning(f"Could not load config, using fallback scoring: {e}")
    # Fallback scoring configuration
    SCORING_CONFIG = {
        "base_points": {"easy": 5, "medium": 10, "hard": 20},
        "speed_bonuses": [
            {"max_time": 2, "bonus": 10},
            {"max_time": 5, "bonus": 5},
            {"max_time": 10, "bonus": 2}
        ],
        "streak_multipliers": [
            {"min_streak": 1, "multiplier": 1.0},
            {"min_streak": 2, "multiplier": 1.25},
            {"min_streak": 3, "multiplier": 1.5},
            {"min_streak": 4, "multiplier": 2.0}
        ],
        "penalties": {
            "wrong_answer": -5,
            "wrong_fast": -10,
            "timeout": -2
        }
    }

TRIVIA_CATEGORIES = {
    "General Knowledge": 9,
    "Books": 10,
    "Film": 11,
    "Music": 12,
    "Television": 14,
    "Video Games": 15,
    "Science": 17,
    "Computers": 18,
    "Math": 19,
    "Mythology": 20,
    "Sports": 21,
    "Geography": 22,
    "History": 23,
    "Politics": 24,
    "Art": 25,
    "Celebrities": 26,
    "Animals": 27,
    "Vehicles": 28,
    "Comics": 29,
    "Gadgets": 30,
    "Anime & Manga": 31,
    "Cartoons": 32
}

class ScoreCalculator:
    @staticmethod
    def calculate_speed_bonus(response_time: float) -> int:
        """Calculate speed bonus based on response time"""
        for bonus_config in SCORING_CONFIG["speed_bonuses"]:
            if response_time < bonus_config["max_time"]:
                return bonus_config["bonus"]
        return 0
    
    @staticmethod
    def calculate_streak_multiplier(streak: int) -> float:
        """Calculate streak multiplier based on current streak"""
        multiplier = 1.0
        for streak_config in SCORING_CONFIG["streak_multipliers"]:
            if streak >= streak_config["min_streak"]:
                multiplier = streak_config["multiplier"]
        return multiplier
    
    @staticmethod
    def calculate_final_score(difficulty: Difficulty, response_time: float, 
                            is_correct: bool, streak: int) -> Tuple[int, Dict[str, Any]]:
        """Calculate final score with breakdown"""
        base_points = SCORING_CONFIG["base_points"][difficulty.value]
        speed_bonus = ScoreCalculator.calculate_speed_bonus(response_time)
        streak_multiplier = ScoreCalculator.calculate_streak_multiplier(streak)
        
        breakdown = {
            "base_points": base_points,
            "speed_bonus": speed_bonus,
            "streak_multiplier": streak_multiplier,
            "penalty": 0
        }
        
        if is_correct:
            final_score = int((base_points + speed_bonus) * streak_multiplier)
        else:
            # Apply penalties
            if response_time < 2:
                penalty = SCORING_CONFIG["penalties"]["wrong_fast"]
            else:
                penalty = SCORING_CONFIG["penalties"]["wrong_answer"]
            
            breakdown["penalty"] = penalty
            final_score = penalty
        
        return final_score, breakdown


class MultiServerDataManager:
    """Manages data for multiple Discord servers efficiently"""
    
    def __init__(self):
        self.server_data: Dict[str, Dict[str, UserStats]] = {}
        
        # Load configuration
        try:
            self.data_directory = config.TRIVIA_CONFIG.get("data_directory", "data") + "/servers"
            self.save_interval = config.TRIVIA_CONFIG["performance"].get("save_interval", 30)
            self.batch_save_size = config.TRIVIA_CONFIG["performance"].get("batch_save_size", 10)
        except (AttributeError, KeyError):
            self.data_directory = "data/servers"
            self.save_interval = 30
            self.batch_save_size = 10
        
        self.last_save_time = time.time()
        self.pending_saves = set()  # Track which servers need saving
        
        self.ensure_data_directory()
        self.load_all_server_data()
    
    def ensure_data_directory(self):
        """Ensure data directory exists"""
        os.makedirs(self.data_directory, exist_ok=True)
    
    def get_server_file_path(self, guild_id: str) -> str:
        """Get file path for a specific server"""
        return os.path.join(self.data_directory, f"server_{guild_id}.json")
    
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
        """Immediately save server data"""
        try:
            server_file = self.get_server_file_path(guild_id)
            server_stats = self.server_data[guild_id]
            
            serializable_stats = {
                user_id: asdict(stats) for user_id, stats in server_stats.items()
            }
            
            # Atomic write (write to temp file, then rename)
            temp_file = f"{server_file}.tmp"
            with open(temp_file, "w") as f:
                json.dump(serializable_stats, f, indent=2)
            
            os.rename(temp_file, server_file)
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
    
    def get_memory_usage_info(self) -> Dict[str, Any]:
        """Get memory usage information for monitoring"""
        return {
            "servers_loaded": len(self.server_data),
            "total_users": sum(len(server_stats) for server_stats in self.server_data.values()),
            "pending_saves": len(self.pending_saves),
            "last_save_time": self.last_save_time
        }
    
    def cleanup_memory(self):
        """Clean up memory by removing unused server data"""
        # Remove servers that haven't been accessed recently
        # This is a simple implementation - could be enhanced with LRU cache
        current_time = time.time()
        
        # For Pi optimization, only keep data for servers with recent activity
        # This is a placeholder - you could implement more sophisticated cleanup
        logger.info("Memory cleanup performed")


class TriviaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_manager = MultiServerDataManager()
        
        # Track active questions per server
        self.active_questions: Dict[str, Tuple] = {}  # guild_id -> question_data
        self.timeout_tasks: Dict[str, asyncio.Task] = {}  # guild_id -> timeout_task
        
        self.session = None
        logger.info("Multi-server TriviaCog initialized")
    
    async def cog_load(self):
        """Called when the cog is loaded"""
        # Create HTTP session for API calls
        connector = aiohttp.TCPConnector(
            limit=5,  # Connection pool limit
            limit_per_host=2,
            ttl_dns_cache=300,
            use_dns_cache=True
        )
        
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': 'TriviaBot/2.0'}
        )
        logger.info("TriviaCog session created")
    
    async def cog_unload(self):
        """Called when the cog is unloaded"""
        # Cancel all timeout tasks
        for guild_id, task in self.timeout_tasks.items():
            if not task.done():
                task.cancel()
        
        if self.session and not self.session.closed:
            await self.session.close()
        
        # Force save all pending data
        self.data_manager.force_save_all()
        logger.info("Multi-server TriviaCog cleaned up")
    
    async def cleanup_memory(self):
        """Clean up memory (called by performance monitor)"""
        self.data_manager.cleanup_memory()
        logger.info("Trivia memory cleanup completed")
    
    def get_guild_id(self, interaction: discord.Interaction) -> str:
        """Get guild ID as string"""
        return str(interaction.guild.id) if interaction.guild else "DM"
    
    async def fetch_trivia_question(self, category_id: Optional[int] = None, 
                                  difficulty: Optional[Difficulty] = None) -> Optional[Dict]:
        """Fetch a trivia question from Open Trivia DB"""
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        
        if category_id:
            url += f"&category={category_id}"
        if difficulty:
            url += f"&difficulty={difficulty.value}"
        
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("response_code") == 0 and data.get("results"):
                        return data["results"][0]
                    else:
                        logger.warning(f"API returned error code: {data.get('response_code')}")
                else:
                    logger.error(f"HTTP error: {resp.status}")
        except Exception as e:
            logger.error(f"Error fetching trivia question: {e}")
        
        return None
    
    async def autocomplete_category(self, interaction: discord.Interaction, current: str):
        """Autocomplete for trivia categories"""
        return [
            app_commands.Choice(name=cat, value=cat)
            for cat in TRIVIA_CATEGORIES
            if current.lower() in cat.lower()
        ][:20]
    
    async def autocomplete_difficulty(self, interaction: discord.Interaction, current: str):
        """Autocomplete for difficulty levels"""
        difficulties = ["easy", "medium", "hard"]
        return [
            app_commands.Choice(name=diff.title(), value=diff)
            for diff in difficulties
            if current.lower() in diff.lower()
        ]
    
    @app_commands.command(name="trivia", description="Start a trivia question")
    @app_commands.describe(
        category="Choose a trivia category (optional)",
        difficulty="Choose your difficulty (optional)"
    )
    @app_commands.autocomplete(category=autocomplete_category)
    @app_commands.autocomplete(difficulty=autocomplete_difficulty)
    async def trivia(self, interaction: discord.Interaction, 
                    category: Optional[str] = None, 
                    difficulty: Optional[str] = None):
        """Main trivia command"""
        await interaction.response.defer()
        
        guild_id = self.get_guild_id(interaction)
        
        # Check if there's already an active question in this server
        if guild_id in self.active_questions:
            await self._handle_active_question_conflict(interaction, guild_id)
            return
        
        # Parse difficulty
        diff_enum = None
        if difficulty:
            try:
                diff_enum = Difficulty(difficulty.lower())
            except ValueError:
                await interaction.followup.send("❌ Invalid difficulty. Use: easy, medium, or hard")
                return
        
        # Get category ID
        category_id = None
        if category:
            category_id = TRIVIA_CATEGORIES.get(category.title())
            if not category_id:
                await interaction.followup.send("❌ Invalid category.")
                return
        
        # Fetch question
        question_data = await self.fetch_trivia_question(category_id, diff_enum)
        if not question_data:
            await interaction.followup.send("❌ Could not fetch a trivia question. Please try again.")
            return
        
        await self._create_trivia_question(interaction, question_data, guild_id)
    
    async def _handle_active_question_conflict(self, interaction: discord.Interaction, guild_id: str):
        """Handle when there's already an active question in this server"""
        active_user_id, _, _, _, start_time, _, _ = self.active_questions[guild_id]
        elapsed_time = time.time() - start_time
        remaining_time = max(0, QUESTION_TIMEOUT - elapsed_time)
        
        active_user = self.bot.get_user(active_user_id)
        active_username = active_user.name if active_user else "Unknown User"
        
        embed = discord.Embed(
            title="🚫 Trivia In Progress",
            description=f"**{active_username}** is currently answering a trivia question in this server.\n\n"
                       f"⏱️ Time remaining: **{remaining_time:.0f}** seconds\n\n"
                       f"Please wait for them to finish or for the question to timeout.",
            color=discord.Color.orange()
        )
        
        await interaction.followup.send(embed=embed)
    
    async def _create_trivia_question(self, interaction: discord.Interaction, 
                                    question_data: Dict, guild_id: str):
        """Create and display a trivia question"""
        # Parse question data
        question = html.unescape(question_data["question"])
        correct = html.unescape(question_data["correct_answer"])
        incorrect = [html.unescape(i) for i in question_data["incorrect_answers"]]
        category = html.unescape(question_data["category"])
        difficulty = Difficulty(question_data["difficulty"])

        # Create options
        options = incorrect + [correct]
        random.shuffle(options)
        correct_letter = chr(65 + options.index(correct))
        
        # Create embed
        embed = discord.Embed(
            title=f"🧠 {category} Trivia",
            color=self._get_difficulty_color(difficulty)
        )
        
        option_text = "\n".join([f"**{chr(65+i)}. {opt}**" for i, opt in enumerate(options)])
        embed.description = f"**{question}**\n\n{option_text}"
        
        # Add difficulty indicator
        diff_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
        embed.add_field(
            name="Difficulty",
            value=f"{diff_emoji[difficulty.value]} {difficulty.value.title()}",
            inline=True
        )
        
        # Add scoring info
        base_points = SCORING_CONFIG["base_points"][difficulty.value]
        embed.add_field(
            name="Base Points",
            value=f"{base_points} points",
            inline=True
        )
        
        # Get user's current streak in this server
        user_stats = self.data_manager.get_user_stats(guild_id, str(interaction.user.id), interaction.user.name)
        if user_stats.current_streak > 0:
            embed.add_field(
                name="Current Streak",
                value=f"🔥 {user_stats.current_streak}",
                inline=True
            )
        
        guild_name = interaction.guild.name if interaction.guild else "DM"
        embed.set_footer(text=f"Only {interaction.user.name} can answer! Type A, B, C, or D. ({QUESTION_TIMEOUT}s timeout)")
        
        msg = await interaction.followup.send(embed=embed)
        
        # Set active question for this server
        start_time = time.time()
        self.active_questions[guild_id] = (
            interaction.user.id, correct_letter, correct, msg.id, 
            start_time, interaction.channel.id, difficulty
        )
        
        # Start timeout task for this server
        self.timeout_tasks[guild_id] = asyncio.create_task(self._timeout_question(guild_id))
        logger.info(f"Started trivia question for {interaction.user.name} in server {guild_name}")
    
    def _get_difficulty_color(self, difficulty: Difficulty) -> discord.Color:
        """Get color based on difficulty"""
        colors = {
            Difficulty.EASY: discord.Color.green(),
            Difficulty.MEDIUM: discord.Color.yellow(),
            Difficulty.HARD: discord.Color.red()
        }
        return colors.get(difficulty, discord.Color.blurple())
    
    async def _timeout_question(self, guild_id: str):
        """Handle question timeout for a specific server"""
        await asyncio.sleep(QUESTION_TIMEOUT)
        
        if guild_id in self.active_questions:
            user_id, correct_letter, correct_answer, msg_id, _, channel_id, difficulty = self.active_questions[guild_id]
            
            # Apply timeout penalty
            user_stats = self.data_manager.get_user_stats(guild_id, str(user_id), "Unknown")
            penalty = SCORING_CONFIG["penalties"]["timeout"]
            self.data_manager.update_user_stats(guild_id, str(user_id), difficulty, False, QUESTION_TIMEOUT, penalty)
            
            # Clear active question for this server
            del self.active_questions[guild_id]
            if guild_id in self.timeout_tasks:
                del self.timeout_tasks[guild_id]
            
            # Update message
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    message = await channel.fetch_message(msg_id)
                    
                    timeout_embed = discord.Embed(
                        title="⏰ Time's Up!",
                        description=f"<@{user_id}> took too long to answer!\n\n"
                                   f"The correct answer was **{correct_letter}) {correct_answer}**\n\n"
                                   f"**Penalty:** {penalty} points\n"
                                   f"**New Score:** {user_stats.total_score} points",
                        color=discord.Color.red()
                    )
                    
                    await message.edit(embed=timeout_embed)
                    
                except discord.NotFound:
                    logger.warning("Timeout message not found")
                except Exception as e:
                    logger.error(f"Error during timeout: {e}")
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle trivia answers"""
        if message.author.bot or not message.guild:
            return
        
        guild_id = str(message.guild.id)
        
        if guild_id not in self.active_questions:
            return
        
        user_id, correct_letter, correct_answer, msg_id, start_time, channel_id, difficulty = self.active_questions[guild_id]
        
        # Only the person who started the question can answer
        if message.author.id != user_id or message.channel.id != channel_id:
            return
        
        content = message.content.strip().upper()
        if content not in ["A", "B", "C", "D"]:
            return
        
        # Cancel timeout task
        if guild_id in self.timeout_tasks and not self.timeout_tasks[guild_id].done():
            self.timeout_tasks[guild_id].cancel()
        
        # Clear active question for this server
        del self.active_questions[guild_id]
        if guild_id in self.timeout_tasks:
            del self.timeout_tasks[guild_id]
        
        # Calculate response time and score
        response_time = time.time() - start_time
        is_correct = content == correct_letter
        
        # Get user stats for this server
        user_stats = self.data_manager.get_user_stats(guild_id, str(user_id), message.author.name)
        
        # Calculate score
        score_change, breakdown = ScoreCalculator.calculate_final_score(
            difficulty, response_time, is_correct, user_stats.current_streak
        )
        
        # Update user stats for this server
        self.data_manager.update_user_stats(guild_id, str(user_id), difficulty, is_correct, response_time, score_change)
        
        # Create response embed
        await self._send_answer_response(message, is_correct, correct_letter, correct_answer, 
                                       response_time, score_change, breakdown, user_stats)
    
    async def _send_answer_response(self, message: discord.Message, is_correct: bool, 
                                  correct_letter: str, correct_answer: str, response_time: float,
                                  score_change: int, breakdown: Dict, user_stats: UserStats):
        """Send the response after an answer is submitted"""
        if is_correct:
            embed = discord.Embed(
                title="✅ Correct!",
                description=f"Excellent work, {message.author.mention}!",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="❌ Incorrect!",
                description=f"Nice try {message.author.mention}!\n"
                           f"The correct answer was **{correct_letter}. {correct_answer}**",
                color=discord.Color.red()
            )
        
        # Add score breakdown
        # Combine everything into one clean field
        score_text = (
            f"**🏆 Score Change:** `{score_change:+d}` points\n"
            f"**📊 Total Score:** `{user_stats.total_score}` points\n"
            f"**🔥 Streak:** `{user_stats.current_streak}`\n"
            f"**⏱️ Response Time:** `{response_time:.1f}s`\n"
        )

        if is_correct:
            score_text += (
                f"\n**🧮 Breakdown:**\n"
                f"• 🟢 Base Points: `{breakdown['base_points']}`\n"
                f"• ⚡ Speed Bonus: `{breakdown['speed_bonus']}`\n"
                f"• 🔁 Streak Multiplier: `x{breakdown['streak_multiplier']}`\n"
            )
        else:
            score_text += (
                f"\n**🔻 Penalty Applied:** `{score_change}` points lost"
            )

        embed.add_field(name="\u200b", value=score_text, inline=False)

        await message.channel.send(embed=embed)
    
    @app_commands.command(name="trivia_stats", description="View trivia statistics for this server")
    async def trivia_stats(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """View trivia statistics for this server"""
        await interaction.response.defer()
        
        guild_id = self.get_guild_id(interaction)
        target_user = user or interaction.user
        user_stats = self.data_manager.get_user_stats(guild_id, str(target_user.id), target_user.name)
        
        server_name = interaction.guild.name if interaction.guild else "DM"
        
        embed = discord.Embed(
            title=f"📊 Trivia Stats - {target_user.name}",
            description=f"**Server:** {server_name}",
            color=discord.Color.blue()
        )
        
        # Basic stats
        accuracy = (user_stats.correct_answers / user_stats.questions_answered * 100) if user_stats.questions_answered > 0 else 0
        embed.add_field(
            name="📊 Overall Performance",
            value=(
                f"**💯 Total Score:** `{user_stats.total_score}`\n"
                f"**❓ Questions Answered:** `{user_stats.questions_answered}`\n"
                f"**🎯 Accuracy:** `{accuracy:.1f}%`\n"
                f"**🔥 Current Streak:** `{user_stats.current_streak}`\n"
                f"**🏅 Best Streak:** `{user_stats.best_streak}`\n"
                f"**⏱️ Avg. Response Time:** `{user_stats.avg_response_time:.1f}s`"
            ),
            inline=False
        )
        # Difficulty breakdown
        diff_text = ""
        for diff, stats in user_stats.difficulty_stats.items():
            if stats["total"] > 0:
                acc = (stats["correct"] / stats["total"] * 100)
                diff_text += f"**{diff.title()}:** {stats['correct']}/{stats['total']} ({acc:.1f}%)\n"
        
        if diff_text:
            embed.add_field(name="Difficulty Breakdown", value=diff_text, inline=False)
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="trivia_leaderboard", description="View the trivia leaderboard for this server")
    async def trivia_leaderboard(self, interaction: discord.Interaction):
        """View the trivia leaderboard for this server"""
        await interaction.response.defer()
        
        guild_id = self.get_guild_id(interaction)
        server_name = interaction.guild.name if interaction.guild else "DM"
        
        leaderboard = self.data_manager.get_server_leaderboard(guild_id, 10)
        
        if not leaderboard:
            await interaction.followup.send(f"No scores yet in **{server_name}**! Use `/trivia` to start playing!")
            return
        
        embed = discord.Embed(
            title=f"🏆 Trivia Leaderboard",
            description=f"**Server:** {server_name}",
            color=discord.Color.gold()
        )
        
        for i, (user_id, stats) in enumerate(leaderboard, 1):
            accuracy = (stats.correct_answers / stats.questions_answered * 100) if stats.questions_answered > 0 else 0
            embed.add_field(
                name=f"{i}. {stats.username}",
                value=f"**Score:** {stats.total_score}\n"
                      f"**Accuracy:** {accuracy:.1f}%\n"
                      f"**Best Streak:** {stats.best_streak}",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="trivia_debug", description="Debug information (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def trivia_debug(self, interaction: discord.Interaction):
        """Debug information for administrators"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = self.get_guild_id(interaction)
        memory_info = self.data_manager.get_memory_usage_info()
        
        embed = discord.Embed(
            title="🔧 Trivia Debug Information",
            color=discord.Color.purple()
        )
        
        embed.add_field(
            name="Memory Usage",
            value=f"**Servers Loaded:** {memory_info['servers_loaded']}\n"
                  f"**Total Users:** {memory_info['total_users']}\n"
                  f"**Pending Saves:** {memory_info['pending_saves']}",
            inline=False
        )
        
        embed.add_field(
            name="Active Questions",
            value=f"**Currently Active:** {len(self.active_questions)}\n"
                  f"**This Server:** {'Yes' if guild_id in self.active_questions else 'No'}",
            inline=False
        )
        
        await interaction.followup.send(embed=embed)