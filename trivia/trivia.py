# trivia/trivia.py - Multi-server trivia system with improved scoring

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
import html
import random
import asyncio
import time
import logging
from typing import Dict, Optional, Tuple, Any, List
from dataclasses import dataclass, asdict
from enum import Enum
from trivia.multi_server_data_manager import MultiServerDataManager

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

# Import config for scoring configuration
try:
    import config
    SCORING_CONFIG = config.TRIVIA_CONFIG["scoring"]
    QUESTION_TIMEOUT = config.TRIVIA_CONFIG.get("question_timeout", 30)
    AUTHORIZED_RESET_USER_ID = config.TRIVIA_CONFIG.get("authorized_reset_user_id", "YOUR_USER_ID_HERE")
except (ImportError, KeyError) as e:
    logger.warning(f"Could not load config, using fallback scoring: {e}")
    # IMPROVED Fallback scoring configuration
    
    # Fallback authorized user ID - CHANGE THIS TO YOUR ACTUAL DISCORD USER ID
    AUTHORIZED_RESET_USER_ID = "YOUR_USER_ID_HERE"

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
    def calculate_speed_bonus(response_time: float) -> Tuple[int, str]:
        """Calculate speed bonus based on response time with diminishing returns"""
        for bonus_config in SCORING_CONFIG["speed_bonuses"]:
            if response_time <= bonus_config["max_time"]:
                # Determine speed tier for display
                if response_time <= 3:
                    tier = "⚡ Lightning Fast!"
                elif response_time <= 6:
                    tier = "🔥 Very Fast!"
                elif response_time <= 10:
                    tier = "⭐ Fast!"
                elif response_time <= 15:
                    tier = "👍 Good Speed"
                elif response_time <= 20:
                    tier = "👌 Decent"
                else:
                    tier = "🐌 Getting Slow..."
                
                return bonus_config["bonus"], tier
        
        return 0, "🐢 Too Slow"
    
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
                            is_correct: bool, streak: int, was_intentional: bool = False) -> Tuple[int, Dict[str, Any]]:
        """
        Calculate final score with breakdown
        
        Args:
            difficulty: The difficulty level
            response_time: How long it took to answer
            is_correct: Whether the answer was correct
            streak: Current streak count
            was_intentional: Whether the user specifically chose this difficulty
        """
        base_points = SCORING_CONFIG["base_points"][difficulty.value]
        speed_bonus, speed_tier = ScoreCalculator.calculate_speed_bonus(response_time)
        streak_multiplier = ScoreCalculator.calculate_streak_multiplier(streak)
        
        breakdown = {
            "base_points": base_points,
            "speed_bonus": speed_bonus,
            "speed_tier": speed_tier,
            "streak_multiplier": streak_multiplier,
            "penalty": 0,
            "penalty_reason": ""
        }
        
        if is_correct:
            final_score = int((base_points + speed_bonus) * streak_multiplier)
        else:
            # Apply improved penalties based on whether difficulty was intentionally chosen
            if was_intentional:
                penalty = SCORING_CONFIG["penalties"]["wrong_answer_intentional"][difficulty.value]
                penalty_reason = f"Wrong on {difficulty.value.title()} (Intentional)"
            else:
                penalty = SCORING_CONFIG["penalties"]["wrong_answer_random"][difficulty.value]
                penalty_reason = f"Wrong on {difficulty.value.title()} (Random)"
            
            # Additional penalty for being wrong AND fast (shows carelessness)
            if response_time < 5:
                penalty += SCORING_CONFIG["penalties"]["wrong_fast_bonus_penalty"]
                penalty_reason += " + Rush Penalty"
            
            breakdown["penalty"] = penalty
            breakdown["penalty_reason"] = penalty_reason
            final_score = penalty
        
        return final_score, breakdown


@dataclass
class SeasonSnapshot:
    season_name: str
    end_date: str
    server_name: str
    leaderboard: List[Dict]
    total_players: int
    total_questions_asked: int
    
    def to_dict(self):
        return asdict(self)


class TriviaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_manager = MultiServerDataManager()
        
        # Track active questions per server - now includes intentional difficulty flag
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
                                  difficulty: Optional[Difficulty] = None,
                                  guild_id: Optional[str] = None,
                                  user_id: Optional[str] = None,
                                  max_attempts: int = 10) -> Optional[Dict]:
        """
        Fetch a trivia question from Open Trivia DB, avoiding duplicates for the user
        
        Args:
            category_id: Category ID for the question
            difficulty: Difficulty level
            guild_id: Server ID for question tracking
            user_id: User ID for duplicate checking
            max_attempts: Maximum attempts to find an unseen question
        """
        base_url = "https://opentdb.com/api.php?amount=1&type=multiple"
        
        if category_id:
            base_url += f"&category={category_id}"
        if difficulty:
            base_url += f"&difficulty={difficulty.value}"
        
        # If we have tracking info, try to find an unseen question
        if guild_id and user_id:
            for attempt in range(max_attempts):
                try:
                    async with self.session.get(base_url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("response_code") == 0 and data.get("results"):
                                question_data = data["results"][0]
                                
                                # Check if user has seen this question
                                if not self.data_manager.has_user_seen_question(guild_id, user_id, question_data):
                                    return question_data
                                
                                logger.debug(f"User {user_id} has seen this question, trying again ({attempt + 1}/{max_attempts})")
                            else:
                                logger.warning(f"API returned error code: {data.get('response_code')}")
                                break
                        else:
                            logger.error(f"HTTP error: {resp.status}")
                            break
                except Exception as e:
                    logger.error(f"Error fetching trivia question (attempt {attempt + 1}): {e}")
                    if attempt == max_attempts - 1:
                        break
                
                # Small delay between attempts
                await asyncio.sleep(0.1)
            
            # If we couldn't find an unseen question, just return any question
            logger.info(f"Could not find unseen question for user {user_id} after {max_attempts} attempts, returning any question")
        
        # Fallback: fetch any question without duplicate checking
        try:
            async with self.session.get(base_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("response_code") == 0 and data.get("results"):
                        return data["results"][0]
                    else:
                        logger.warning(f"API returned error code: {data.get('response_code')}")
                else:
                    logger.error(f"HTTP error: {resp.status}")
        except Exception as e:
            logger.error(f"Error fetching fallback trivia question: {e}")
        
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
        difficulty="Choose your difficulty (optional) - WARNING: Higher penalties for wrong answers!"
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
        
        # Parse difficulty and track if it was intentionally chosen
        diff_enum = None
        was_intentional = difficulty is not None
        
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
        question_data = await self.fetch_trivia_question(category_id, diff_enum, guild_id, str(interaction.user.id))
        if not question_data:
            await interaction.followup.send("❌ Could not fetch a trivia question. Please try again.")
            return
        
        await self._create_trivia_question(interaction, question_data, guild_id, was_intentional)
        
    
    def is_authorized_user(interaction: discord.Interaction) -> bool:
        """Check if user is authorized to reset scores"""
        return str(interaction.user.id) == AUTHORIZED_RESET_USER_ID
    
    @app_commands.command(name="reset_scores", description="Reset all scores for this server (authorized users only)")
    @app_commands.describe(season_name="Name for this season (e.g., 'Season 1', 'Winter 2024')")
    @app_commands.check(is_authorized_user)
    async def reset_scores(self, interaction: discord.Interaction, season_name: str):
        """Reset all trivia scores for the server with hall of fame archival"""
        await interaction.response.defer()

        guild_id = self.get_guild_id(interaction)
        server_name = interaction.guild.name if interaction.guild else "DM"
        
        # Check if there are any scores to reset
        leaderboard = self.data_manager.get_server_leaderboard(guild_id)
        if not leaderboard:
            embed = discord.Embed(
                title="📊 No Scores Found",
                description="There are no scores to reset in this server.",
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed)
            return
        
        # Create confirmation embed
        top_player = leaderboard[0][1]  # Get top player's stats
        embed = discord.Embed(
            title="⚠️ Confirm Score Reset",
            description=f"**Are you sure you want to reset all scores for {server_name}?**\n\n"
                       f"This will:\n"
                       f"• Archive current scores as **'{season_name}'**\n"
                       f"• Reset all {len(leaderboard)} players' scores to 0\n"
                       f"• Keep question history (no repeats)\n\n"
                       f"**Current leader:** {top_player.username} ({top_player.total_score} points)\n"
                       f"**Total players:** {len(leaderboard)}",
            color=discord.Color.orange()
        )
        
        embed.set_footer(text="This action cannot be undone! React with ✅ to confirm or ❌ to cancel.")
        
        msg = await interaction.followup.send(embed=embed)
        
        # Add reactions
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        
        # Wait for reaction
        def check(reaction, user):
            return (user.id == interaction.user.id and 
                   str(reaction.emoji) in ["✅", "❌"] and 
                   reaction.message.id == msg.id)
        
        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
            
            if str(reaction.emoji) == "✅":
                # Create and save snapshot
                snapshot = self.data_manager.create_season_snapshot(guild_id, season_name, server_name)
                self.data_manager.save_season_snapshot(guild_id, snapshot)
                
                # Reset scores
                self.data_manager.reset_server_scores(guild_id)
                
                # Success message
                success_embed = discord.Embed(
                    title="✅ Scores Reset Successfully",
                    description=f"**Season '{season_name}' archived!**\n\n"
                               f"• {len(leaderboard)} players archived\n"
                               f"• All scores reset to 0\n"
                               f"• Question history preserved\n"
                               f"• Use `/hall_of_fame` to view archived seasons",
                    color=discord.Color.green()
                )
                
                await msg.edit(embed=success_embed)
                await msg.clear_reactions()
                
            else:
                # Cancelled
                cancel_embed = discord.Embed(
                    title="❌ Reset Cancelled",
                    description="Score reset has been cancelled.",
                    color=discord.Color.red()
                )
                await msg.edit(embed=cancel_embed)
                await msg.clear_reactions()
                
        except asyncio.TimeoutError:
            timeout_embed = discord.Embed(
                title="⏰ Confirmation Timeout",
                description="Score reset confirmation timed out.",
                color=discord.Color.red()
            )
            await msg.edit(embed=timeout_embed)
            await msg.clear_reactions()

    @reset_scores.error
    async def reset_scores_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle reset_scores command errors"""
        if isinstance(error, app_commands.CheckFailure):
            embed = discord.Embed(
                title="🚫 Access Denied",
                description="You don't have permission to use this command.",
                color=discord.Color.red()
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            # Handle other potential errors
            logger.error(f"Reset scores error: {error}")
            error_embed = discord.Embed(
                title="❌ Command Error",
                description="An error occurred while processing the reset command.",
                color=discord.Color.red()
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)

        
    async def autocomplete_season(self, interaction: discord.Interaction, current: str):
        """Autocomplete for season names in hall of fame"""
        guild_id = self.get_guild_id(interaction)
        hall_of_fame = self.data_manager.get_hall_of_fame(guild_id)
        
        return [
            app_commands.Choice(name=season.season_name, value=season.season_name)
            for season in hall_of_fame
            if current.lower() in season.season_name.lower()
        ][:20]

    @app_commands.command(name="hall_of_fame", description="View archived seasons and past champions")
    @app_commands.describe(season="Specific season to view details")
    @app_commands.autocomplete(season=autocomplete_season)
    async def hall_of_fame(self, interaction: discord.Interaction, season: Optional[str] = None):
        """View the hall of fame with enhanced styling"""
        await interaction.response.defer()
        
        guild_id = self.get_guild_id(interaction)
        server_name = interaction.guild.name if interaction.guild else "DM"
        
        hall_of_fame = self.data_manager.get_hall_of_fame(guild_id)
        
        if not hall_of_fame:
            embed = discord.Embed(
                title="",
                description=f"🏛️ **HALL OF FAME** 🏛️\n"
                        f"### 👑 **{server_name}** 👑\n\n"
                        f"```diff\n"
                        f"🌟 The halls echo with silence... 🌟\n"
                        f"🏺 No legendary seasons yet! 🏺\n"
                        f"⚔️ History awaits your conquest! ⚔️\n"
                        f"```\n"
                        f"📜 **History awaits your greatness!** 📜\n"
                        f"🎭 Seasons will be immortalized here after using `/reset_scores`\n"
                        f"✨ Start building your legendary legacy with `/trivia`!✨ ",
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
            await interaction.followup.send(embed=embed)
            return
        
        if season:
            # Show specific season details with enhanced styling
            season_data = None
            for s in hall_of_fame:
                if s.season_name.lower() == season.lower():
                    season_data = s
                    break
            
            if not season_data:
                available_seasons = "`, `".join([s.season_name for s in hall_of_fame])
                embed = discord.Embed(
                    title="🔍 Season Not Found",
                    description=f"### ❌ **'{season}'** does not exist\n\n"
                            f"**📚 Available Seasons:**\n`{available_seasons}`\n\n"
                            f"💡 *Try using autocomplete to find the right season!*",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed)
                return
            
            # Create detailed season view
            embed = discord.Embed(title="", description="", color=discord.Color.gold())
            
            header = f"🏛️ **HALL OF FAME** 🏛️\n\n"
            header += f"### 👑 **{season_data.season_name}**\n\n"
            
            # Season info box
            season_info = f"```ansi\n"
            season_info += f"\u001b[1;36m🏰 Server:\u001b[0m \u001b[1;33m{season_data.server_name}\u001b[0m\n"
            season_info += f"\u001b[1;35m📅 Ended:\u001b[0m \u001b[1;32m{season_data.end_date}\u001b[0m\n"
            season_info += f"```\n"
            
            # Top 3 legendary champions
            legends_text = ""
            
            top_3_legends = season_data.leaderboard[:3]
            legend_medals = ["👑", "🥈", "🥉"]
            legend_titles = ["ULTIMATE CHAMPION", "ROYAL RUNNER-UP", "TRIUMPHANT THIRD"]
            
            for i, player in enumerate(top_3_legends):
                legends_text += f"\n{legend_medals[i]} **{legend_titles[i]}** {legend_medals[i]}\n"
                legends_text += f"```ansi\n"
                legends_text += f"\u001b[1;36m👤 \u001b[1;33m{player['username']}\u001b[0m\n"
                legends_text += f"\u001b[1;35m🏆 Final Score:\u001b[0m \u001b[1;32m{player['total_score']:,} points\u001b[0m\n"
                legends_text += f"\u001b[1;34m🎯 Mastery:\u001b[0m \u001b[1;31m{player['accuracy']}% accuracy\u001b[0m\n"
                legends_text += f"\u001b[1;33m🔥 Epic Streak:\u001b[0m \u001b[1;36m{player['best_streak']}\u001b[0m\n"
                legends_text += f"\u001b[1;32m⚔️ Questions:\u001b[0m \u001b[1;37m{player['correct_answers']}/{player['questions_answered']}\u001b[0m\n"
                legends_text += f"```"
            
            # Hall of champions (4-10)
            hall_text = ""
            if len(season_data.leaderboard) > 3:
                hall_text = "\n **HALL OF CHAMPIONS** \n\n"
                
                for i, player in enumerate(season_data.leaderboard[3:10], 4):
                    # Add variety to ranking emojis
                    if i <= 5:
                        rank_emojis = "💎"
                    elif i <= 7:
                        rank_emojis = "⭐"
                    else:
                        rank_emojis = "✨"
                        
                    hall_text += f"**#{i}** {rank_emojis} **{player['username']}** {rank_emojis} • "
                    hall_text += f"💯 **{player['total_score']:,}** pts • "
                    hall_text += f"🎯 **{player['accuracy']}%** • "
                    hall_text += f"🔥 **{player['best_streak']}** • "
                    hall_text += f"❓ **{player['correct_answers']}** \n"
            
            embed.description = header + season_info + legends_text + hall_text
            
        else:
            # Show hall of fame overview with enhanced styling
            embed = discord.Embed(title="", description="", color=discord.Color.gold())
            
            header = f"🏛️ **HALL OF FAME** 🏛️\n"
            header += f"### 🏰 **{server_name}**\n\n"
            header += f"📜 **{len(hall_of_fame)} Season{'s' if len(hall_of_fame) != 1 else ''}**\n"
            header += f"💡 *Use `/hall_of_fame season:<name>` for detailed chronicles*\n\n"
            
            seasons_text = "🎭 **CHRONICLES OF CHAMPIONS** 🎭\n"
            
            for i, season_data in enumerate(reversed(hall_of_fame), 1):  # Most recent first
                if season_data.leaderboard:
                    champion = season_data.leaderboard[0]
                    
                    # Season header with fancy styling
                    season_border_emojis = ['🔸', '🔹']
                    season_emoji = season_border_emojis[i % 2]

                    seasons_text += f"### 👑 **{season_data.season_name}** 👑\n"
                    
                    # Champion showcase with colored blocks
                    seasons_text += f"```ansi\n"
                    seasons_text += f"\u001b[1;33m👑 \u001b[0m \u001b[1;36m{champion['username']}\u001b[0m\n"
                    seasons_text += f"\u001b[1;35m🏆 Victory Score:\u001b[0m \u001b[1;32m{champion['total_score']:,} points\u001b[0m\n"
                    seasons_text += f"\u001b[1;34m👥 Total Warriors:\u001b[0m \u001b[1;31m{season_data.total_players}\u001b[0m\n"
                    seasons_text += f"\u001b[1;36m📅 Concluded:\u001b[0m \u001b[1;33m{season_data.end_date.split()[0]}\u001b[0m\n"
                    seasons_text += f"\u001b[1;32m❓ Questions:\u001b[0m \u001b[1;37m{season_data.total_questions_asked:,}\u001b[0m\n"
                    seasons_text += f"```\n"
            
            embed.description = header + seasons_text
        
        # Enhanced footer and thumbnail
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
        
        await interaction.followup.send(embed=embed)
    
    async def _handle_active_question_conflict(self, interaction: discord.Interaction, guild_id: str):
        """Handle when there's already an active question in this server"""
        active_user_id, _, _, _, start_time, _, _, _, _ = self.active_questions[guild_id]
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
                                    question_data: Dict, guild_id: str, was_intentional: bool):
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
        
        # Add difficulty indicator with risk warning
        diff_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
        diff_display = f"{diff_emoji[difficulty.value]} {difficulty.value.title()}"
        
        if was_intentional:
            diff_display += " ⚠️ (Higher Penalty Risk!)"
        else:
            diff_display += " (Random)"
            
        embed.add_field(
            name="Difficulty",
            value=diff_display,
            inline=True
        )
        
        # Show improved scoring info
        base_points = SCORING_CONFIG["base_points"][difficulty.value]
        max_speed_bonus = SCORING_CONFIG["speed_bonuses"][0]["bonus"]  # Best possible speed bonus
        
        embed.add_field(
            name="Scoring",
            value=f"**Base:** {base_points} pts\n**Max Speed Bonus:** +{max_speed_bonus} pts",
            inline=True
        )
        
        # Add penalty warning for intentional difficulty
        if was_intentional:
            wrong_penalty = SCORING_CONFIG["penalties"]["wrong_answer_intentional"][difficulty.value]
            embed.add_field(
                name="⚠️ Wrong Penalty",
                value=f"**{wrong_penalty} points**\n(You chose this difficulty)",
                inline=True
            )
        
        # Get user's current streak in this server
        user_stats = self.data_manager.get_user_stats(guild_id, str(interaction.user.id), interaction.user.name)
        if user_stats.current_streak > 1:
            streak_mult = ScoreCalculator.calculate_streak_multiplier(user_stats.current_streak)
            embed.add_field(
                name="🔥 Current Streak",
                value=f"**{user_stats.current_streak}**",
                inline=True
            )
        
        guild_name = interaction.guild.name if interaction.guild else "DM"
        embed.set_footer(text=f"Only {interaction.user.name} can answer! Type A, B, C, or D. ({QUESTION_TIMEOUT}s timeout)")
        
        msg = await interaction.followup.send(embed=embed)
        
        # Set active question for this server - store question data for tracking
        start_time = time.time()
        self.active_questions[guild_id] = (
            interaction.user.id, correct_letter, correct, msg.id, 
            start_time, interaction.channel.id, difficulty, was_intentional, question_data
        )
        
        # Start timeout task for this server
        self.timeout_tasks[guild_id] = asyncio.create_task(self._timeout_question(guild_id))
        intent_str = "intentional" if was_intentional else "random"
        logger.info(f"Started {intent_str} {difficulty.value} trivia question for {interaction.user.name} in server {guild_name}")
    
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
            user_id, correct_letter, correct_answer, msg_id, _, channel_id, difficulty, was_intentional, question_data = self.active_questions[guild_id]
            
            # Track this question as seen (even if timeout)
            self.data_manager.mark_question_seen(guild_id, str(user_id), question_data)
            
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
                                   f"**Timeout Penalty:** {penalty} points\n"
                                   f"**New Score:** {user_stats.total_score} points",
                        color=discord.Color.red()
                    )
                    
                    await message.edit(embed=timeout_embed)
                    
                except discord.NotFound:
                    logger.warning("Timeout message not found")
                except Exception as e:
                    logger.error(f"Error during timeout: {e}")
    

    def _create_question_hash(self, question_data: Dict) -> str:
        """Create a unique hash for a question to track if it's been seen"""
        # Use question text + correct answer to create unique identifier
        question_text = question_data.get("question", "")
        correct_answer = question_data.get("correct_answer", "")
        
        # Create a simple hash
        import hashlib
        content = f"{question_text}|{correct_answer}".encode('utf-8')
        return hashlib.md5(content).hexdigest()[:12]  # First 12 chars of MD5

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle trivia answers"""
        if message.author.bot or not message.guild:
            return
    
        guild_id = str(message.guild.id)
    
        if guild_id not in self.active_questions:
            return
    
        user_id, correct_letter, correct_answer, msg_id, start_time, channel_id, difficulty, was_intentional, question_data = self.active_questions[guild_id]
    
        # Only the person who started the question can answer
        if message.author.id != user_id or message.channel.id != channel_id:
            return
    
        content = message.content.strip().upper()
        if content not in ["A", "B", "C", "D"]:
            return
    
        # Cancel timeout task
        if guild_id in self.timeout_tasks and not self.timeout_tasks[guild_id].done():
            self.timeout_tasks[guild_id].cancel()
    
        # Calculate response time and score
        response_time = time.time() - start_time
        is_correct = content == correct_letter
    
        # Get user stats for this server
        user_stats = self.data_manager.get_user_stats(guild_id, str(user_id), message.author.name)
    
        # Calculate score with improved system
        score_change, breakdown = ScoreCalculator.calculate_final_score(
            difficulty, response_time, is_correct, user_stats.current_streak, was_intentional
        )
    
        # Track this question as seen - use question_data which we already have
        self.data_manager.mark_question_seen(guild_id, str(user_id), question_data)
    
        # Clear active question for this server AFTER using all the data
        del self.active_questions[guild_id]
        if guild_id in self.timeout_tasks:
            del self.timeout_tasks[guild_id]
    
        # Update user stats for this server
        self.data_manager.update_user_stats(guild_id, str(user_id), difficulty, is_correct, response_time, score_change)
    
        # GET UPDATED USER STATS AFTER THE UPDATE
        updated_user_stats = self.data_manager.get_user_stats(guild_id, str(user_id), message.author.name)
    
        # Create response embed
        await self._send_answer_response(message, is_correct, correct_letter, correct_answer,
                                    response_time, score_change, breakdown, updated_user_stats, was_intentional)
    

    async def _send_answer_response(self, message: discord.Message, is_correct: bool, 
                                  correct_letter: str, correct_answer: str, response_time: float,
                                  score_change: int, breakdown: Dict, user_stats: UserStats, was_intentional: bool):
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
                           f"The correct answer was **{correct_letter}) {correct_answer}**",
                color=discord.Color.red()
            )
        
        # Improved score breakdown with new information
        score_text = (
            f"**🏆 Score Change:** `{score_change:+d}` points\n"
            f"**📊 Total Score:** `{user_stats.total_score}` points\n"
            f"**🔥 Streak:** `{user_stats.current_streak}`\n"
            f"**⏱️ Response Time:** `{response_time:.1f}s` ({breakdown['speed_tier']})\n"
        )

        if is_correct:
            score_text += (
                f"\n**🧮 Score Breakdown:**\n"
                f"• 💎 Base Points: `{breakdown['base_points']}`\n"
                f"• ⚡ Speed Bonus: `+{breakdown['speed_bonus']}`\n"
                f"• 🔁 Streak Multiplier: `x{breakdown['streak_multiplier']}`\n"
            )
        else:
            intent_text = "Intentional Choice" if was_intentional else "Random Difficulty"
            score_text += (
                f"\n**💥 Penalty Applied:**\n"
                f"• ❌ {breakdown['penalty_reason']}: `{score_change}` points\n"
                f"• 🎯 Difficulty Type: {intent_text}"
            )

        embed.add_field(name="\u200b", value=score_text, inline=False)

        # Add strategic tips for wrong answers
        if not is_correct and was_intentional:
            embed.add_field(
                name="💡 Strategy Tip", 
                value="*Choosing specific difficulties increases both rewards AND penalties. Consider random difficulty for safer play!*",
                inline=False
            )

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

        # Enhanced difficulty breakdown with question tracking
        diff_text = ""
        diff_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
        total_unique_questions = self.data_manager.get_user_question_count(guild_id, str(target_user.id))
        
        for diff, stats in user_stats.difficulty_stats.items():
            if stats["total"] > 0:
                acc = (stats["correct"] / stats["total"] * 100)
                diff_text += f"**{diff_emoji[diff]} {diff.title()}:** {stats['correct']}/{stats['total']} ({acc:.1f}%)\n"
        
        if diff_text:
            diff_text += f"\n**🎯 Unique Questions Seen:** {total_unique_questions}"
            embed.add_field(name="🎯 Difficulty Breakdown", value=diff_text, inline=False)
        
        # Add scoring reference
        embed.add_field(
            name="💡 Scoring Reference", 
            value="*Choose specific difficulties for higher rewards but steeper penalties when wrong!*",
            inline=False
        )
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="trivia_leaderboard", description="View the trivia leaderboard for this server")
    async def trivia_leaderboard(self, interaction: discord.Interaction):
        """View the trivia leaderboard for this server with enhanced styling"""
        await interaction.response.defer()
        
        guild_id = self.get_guild_id(interaction)
        server_name = interaction.guild.name if interaction.guild else "DM"
        
        leaderboard = self.data_manager.get_server_leaderboard(guild_id, 10)
        
        if not leaderboard:
            embed = discord.Embed(
                title="🏆 Trivia Leaderboard 🏆",
                description=f"## 🌟 **{server_name}** 🌟\n\n"
                        f"```diff\n"
                        f"+ 🚀 No champions yet! 🚀\n"
                        f"+ 🌟 Be the first to play! 🌟\n"
                        f"+ ⭐ Your legend starts here! ⭐\n"
                        f"```\n\n"
                        f"🚀💫 **Ready to compete?** Use `/trivia` to start your legendary journey! 🎯✨",
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/1234567890.png")
            await interaction.followup.send(embed=embed)
            return
        
        # Create the main leaderboard embed
        embed = discord.Embed(
            title="",
            description="",
            color=discord.Color.gold()
        )
        
        # Custom header with server name
        header = f"🏆🌟 **TRIVIA CHAMPIONS** 🌟🏆\n"
        header += f"###  **{server_name}** \n"
        header += f"\n"
        
        top_3 = leaderboard[:3]
        podium_medals = ["👑", "🥈", "🥉"]
        podium_names = ["CHAMPION", "RUNNER-UP", "THIRD PLACE"]

        podium_text = ""
        
        for i, (user_id, stats) in enumerate(top_3):
            accuracy = (stats.correct_answers / stats.questions_answered * 100) if stats.questions_answered > 0 else 0

            podium_text += f"\n{podium_medals[i]} **{podium_names[i]}** {podium_medals[i]}\n"
            podium_text += f"```ansi\n"
            podium_text += f"\u001b[1;36m👤 {stats.username}\u001b[0m\n"
            podium_text += f"\u001b[1;35m💰 Score:\u001b[0m \u001b[1;32m{stats.total_score:,} points\u001b[0m\n"
            podium_text += f"\u001b[1;34m🎯 Accuracy:\u001b[0m \u001b[1;31m{accuracy:.1f}%\u001b[0m\n"
            podium_text += f"\u001b[1;33m🔥 Best Streak:\u001b[0m \u001b[1;36m{stats.best_streak}\u001b[0m\n"
            podium_text += f"\u001b[1;32m❓ Questions:\u001b[0m \u001b[1;37m{stats.questions_answered}\u001b[0m\n"
            podium_text += f"```"

        # Remaining players (4-10)
        remaining_text = ""
        if len(leaderboard) > 3:
            remaining_text = "\n\n📊 **REMAINING RANKINGS** 📊\n"
            
            for i, (user_id, stats) in enumerate(leaderboard[3:], 4):
                accuracy = (stats.correct_answers / stats.questions_answered * 100) if stats.questions_answered > 0 else 0
                
                # Rank indicators with more variety
                if i == 4:
                    rank_emoji = "💎"
                elif i == 5:
                    rank_emoji = "⭐"
                elif i <= 7:
                    rank_emoji = "⚡" 
                elif i <= 9:
                    rank_emoji = "✨"
                else:
                    rank_emoji = "💫"
                    
                remaining_text += f"\n{rank_emoji} **#{i}** • **{stats.username}** {rank_emoji}\n"
                remaining_text += f"> 💰 **{stats.total_score:,}** pts • 🎯 **{accuracy:.1f}%** • 🔥 **{stats.best_streak}** • ❓ **{stats.questions_answered}** \n"
        
        # Stats summary
        total_players = len(leaderboard)
        total_questions = sum(stats.questions_answered for _, stats in leaderboard)
        avg_accuracy = sum((stats.correct_answers / stats.questions_answered * 100) if stats.questions_answered > 0 else 0 for _, stats in leaderboard) / total_players
        
        embed.description = header + podium_text + remaining_text 
        
        # Add thumbnail and footer
        embed.set_footer(text="✨ Use /trivia to climb the rankings! • 👑 Compete for the crown! ", 
                        icon_url=interaction.user.display_avatar.url)
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="trivia_scoring", description="View the scoring system explanation")
    async def trivia_scoring(self, interaction: discord.Interaction):
        """Explain the trivia scoring system"""
        await interaction.response.defer()
        
        embed = discord.Embed(
            title="🧮 Trivia Scoring System",
            description="Understanding how points are calculated:",
            color=discord.Color.blue()
        )
        
        # Base points
        base_text = ""
        for diff, points in SCORING_CONFIG["base_points"].items():
            emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}[diff]
            base_text += f"**{emoji} {diff.title()}:** {points} points\n"
        
        embed.add_field(name="💎 Base Points", value=base_text, inline=True)
        
        # Speed bonuses
        speed_text = ""
        for bonus in SCORING_CONFIG["speed_bonuses"]:
            speed_text += f"**≤{bonus['max_time']}s:** +{bonus['bonus']} pts\n"
        speed_text += "**>25s:** No bonus"
        
        embed.add_field(name="⚡ Speed Bonuses", value=speed_text, inline=True)
        
        # Streak multipliers
        streak_text = ""
        for streak in SCORING_CONFIG["streak_multipliers"]:
            if streak["min_streak"] > 1:
                streak_text += f"**{streak['min_streak']}+ streak:** x{streak['multiplier']}\n"
        
        embed.add_field(name="🔥 Streak Multipliers", value=streak_text, inline=True)
        
        # Wrong answer penalties
        embed.add_field(
            name="❌ Wrong Answer Penalties",
            value="**Random Difficulty:**\n"
                  "🟢 Easy: -5 pts\n🟡 Medium: -10 pts\n🔴 Hard: -15 pts\n\n"
                  "**Chosen Difficulty:**\n"
                  "🟢 Easy: -5 pts\n🟡 Medium: -12 pts\n🔴 Hard: -20 pts\n\n"
                  "*+5 extra penalty if wrong in <5s*",
            inline=False
        )
        
        # Strategy tip
        embed.add_field(
            name="💡 Strategy Tips",
            value="• **Random difficulty** = Lower risk, lower reward\n"
                  "• **Chosen difficulty** = Higher reward, higher penalty\n"
                  "• **Speed matters** but don't rush wrong answers!\n"
                  "• **Streaks** multiply your points significantly",
            inline=False
        )
        
        embed.set_footer(text="Choose your difficulty wisely - with great reward comes great risk!")
        
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
                  f"**Pending Saves:** {memory_info['pending_saves']}\n"
                  f"**Question Pool:** {memory_info['question_tracking']['question_pool_size']}\n"
                  f"**Avg Questions/User:** {memory_info['question_tracking']['average_questions_per_user']:.1f}",
            inline=False
        )
        
        embed.add_field(
            name="Active Questions",
            value=f"**Currently Active:** {len(self.active_questions)}\n"
                  f"**This Server:** {'Yes' if guild_id in self.active_questions else 'No'}",
            inline=False
        )
        
        # Show current scoring config
        embed.add_field(
            name="Scoring Configuration",
            value=f"**Easy Base:** {SCORING_CONFIG['base_points']['easy']}\n"
                  f"**Medium Base:** {SCORING_CONFIG['base_points']['medium']}\n"
                  f"**Hard Base:** {SCORING_CONFIG['base_points']['hard']}\n"
                  f"**Max Speed Bonus:** {SCORING_CONFIG['speed_bonuses'][0]['bonus']}",
            inline=False
        )
        
        await interaction.followup.send(embed=embed)