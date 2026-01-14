# trivia/trivia.py - Multi-server trivia system with 2-player limit and provider pattern

import discord
from discord.ext import commands
from discord import app_commands
import html
import random
import asyncio
import time
import logging
from typing import Dict, Optional, Tuple, Any, List
from dataclasses import dataclass

from trivia.multi_server_data_manager import MultiServerDataManager
from trivia.models import Difficulty, UserStats
from trivia.categories import (
    UnifiedCategory,
    get_unified_categories,
    get_category_emoji,
    CATEGORY_EMOJIS,
)
from trivia.providers import (
    TriviaProvider, StandardQuestion,
    OpenTDBProvider, SF6Provider, TriviaAPIProvider, QuizAPIProvider
)

logger = logging.getLogger(__name__)

# Configuration
QUESTION_TIMEOUT = 30
MAX_CONCURRENT_PLAYERS = 2  # Maximum players per server at once

# Import config for scoring configuration
try:
    import config
    SCORING_CONFIG = config.TRIVIA_CONFIG["scoring"]
    QUESTION_TIMEOUT = config.TRIVIA_CONFIG.get("question_timeout", 30)
    AUTHORIZED_RESET_USER_ID = config.TRIVIA_CONFIG.get("authorized_reset_user_id", "YOUR_USER_ID_HERE")
except (ImportError, KeyError) as e:
    logger.warning(f"Could not load config, using fallback scoring: {e}")
    SCORING_CONFIG = {
        "base_points": {"easy": 10, "medium": 20, "hard": 30},
        "speed_bonuses": [
            {"max_time": 3, "bonus": 15},
            {"max_time": 6, "bonus": 10},
            {"max_time": 10, "bonus": 7},
            {"max_time": 15, "bonus": 5},
            {"max_time": 20, "bonus": 3},
            {"max_time": 25, "bonus": 1},
        ],
        "streak_multipliers": [
            {"min_streak": 3, "multiplier": 1.25},
            {"min_streak": 5, "multiplier": 1.5},
            {"min_streak": 10, "multiplier": 2.0},
        ],
        "penalties": {
            "wrong_answer_random": {"easy": -5, "medium": -10, "hard": -15},
            "wrong_answer_intentional": {"easy": -5, "medium": -12, "hard": -20},
            # Diminishing rush penalties - faster wrong answers penalized more (max -5)
            "rush_penalties": [
                {"max_time": 2, "penalty": -5, "tier": "Instant Guess"},
                {"max_time": 4, "penalty": -4, "tier": "Rush Guess"},
                {"max_time": 6, "penalty": -3, "tier": "Quick Guess"},
                {"max_time": 8, "penalty": -2, "tier": "Hasty"},
                {"max_time": 10, "penalty": -1, "tier": "Slightly Rushed"},
            ],
            "timeout": -10,
        }
    }
    AUTHORIZED_RESET_USER_ID = "YOUR_USER_ID_HERE"


@dataclass
class ActiveQuestion:
    """Tracks an active trivia question for a user"""
    user_id: int
    channel_id: int
    message_id: int
    correct_letter: str
    correct_answer: str
    start_time: float
    difficulty: Difficulty
    was_intentional: bool
    question_data: StandardQuestion
    timeout_task: Optional[asyncio.Task] = None


class ScoreCalculator:
    @staticmethod
    def calculate_speed_bonus(response_time: float) -> Tuple[int, str]:
        """Calculate speed bonus based on response time"""
        for bonus_config in SCORING_CONFIG["speed_bonuses"]:
            if response_time <= bonus_config["max_time"]:
                if response_time <= 3:
                    tier = "Lightning Fast!"
                elif response_time <= 6:
                    tier = "Very Fast!"
                elif response_time <= 10:
                    tier = "Fast!"
                elif response_time <= 15:
                    tier = "Good Speed"
                elif response_time <= 20:
                    tier = "Decent"
                else:
                    tier = "Getting Slow..."
                return bonus_config["bonus"], tier
        return 0, "Too Slow"

    @staticmethod
    def calculate_streak_multiplier(streak: int) -> float:
        """Calculate streak multiplier based on current streak"""
        multiplier = 1.0
        for streak_config in SCORING_CONFIG["streak_multipliers"]:
            if streak >= streak_config["min_streak"]:
                multiplier = streak_config["multiplier"]
        return multiplier

    @staticmethod
    def calculate_rush_penalty(response_time: float) -> Tuple[int, str]:
        """Calculate diminishing rush penalty - faster wrong answers penalized more"""
        rush_penalties = SCORING_CONFIG["penalties"].get("rush_penalties", [])
        for rush_config in rush_penalties:
            if response_time <= rush_config["max_time"]:
                return rush_config["penalty"], rush_config["tier"]
        return 0, "Considered"  # No rush penalty for slower answers

    @staticmethod
    def calculate_final_score(
        difficulty: Difficulty,
        response_time: float,
        is_correct: bool,
        streak: int,
        was_intentional: bool = False,
        is_sf6: bool = False
    ) -> Tuple[int, Dict[str, Any]]:
        """Calculate final score with breakdown"""
        scoring_difficulty = Difficulty.EASY if is_sf6 else difficulty
        base_points = SCORING_CONFIG["base_points"][scoring_difficulty.value]
        speed_bonus, speed_tier = ScoreCalculator.calculate_speed_bonus(response_time)
        streak_multiplier = ScoreCalculator.calculate_streak_multiplier(streak)

        breakdown = {
            "base_points": base_points,
            "speed_bonus": speed_bonus,
            "speed_tier": speed_tier,
            "streak_multiplier": streak_multiplier,
            "penalty": 0,
            "penalty_reason": "",
            "rush_penalty": 0,
            "rush_tier": "",
            "is_sf6": is_sf6
        }

        if is_correct:
            final_score = int((base_points + speed_bonus) * streak_multiplier)
        else:
            # Base wrong answer penalty
            if was_intentional:
                penalty = SCORING_CONFIG["penalties"]["wrong_answer_intentional"][scoring_difficulty.value]
                penalty_reason = f"Wrong on {difficulty.value.title()} (Intentional)"
            else:
                penalty = SCORING_CONFIG["penalties"]["wrong_answer_random"][scoring_difficulty.value]
                penalty_reason = f"Wrong on {difficulty.value.title()} (Random)"

            if is_sf6:
                penalty_reason += " - SF6 Easy Penalty"

            # Diminishing rush penalty - faster = more penalty
            rush_penalty, rush_tier = ScoreCalculator.calculate_rush_penalty(response_time)
            if rush_penalty < 0:
                penalty += rush_penalty
                penalty_reason += f" + {rush_tier} ({rush_penalty})"
                breakdown["rush_penalty"] = rush_penalty
                breakdown["rush_tier"] = rush_tier

            breakdown["penalty"] = penalty
            breakdown["penalty_reason"] = penalty_reason
            final_score = penalty

        return final_score, breakdown


class TriviaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_manager = MultiServerDataManager()

        # Track active questions per server per user
        # Structure: {guild_id: {user_id: ActiveQuestion}}
        self.active_questions: Dict[str, Dict[int, ActiveQuestion]] = {}

        # Lock to prevent race conditions when processing answers
        self.answer_locks: Dict[str, asyncio.Lock] = {}

        # Initialize providers
        self.providers: Dict[str, TriviaProvider] = {}
        self._init_providers()

        logger.info("TriviaCog with 2-player limit and provider pattern initialized")

    def _init_providers(self):
        """Initialize all trivia providers"""
        # OpenTDB provider (primary)
        opentdb = OpenTDBProvider(self.data_manager)
        self.providers["opentdb"] = opentdb

        # SF6 provider (frame data)
        sf6 = SF6Provider()
        self.providers["sf6"] = sf6

        # The Trivia API provider (no API key needed)
        trivia_api = TriviaAPIProvider()
        self.providers["trivia_api"] = trivia_api

        # QuizAPI provider (tech/programming - requires API key)
        try:
            import config
            quizapi_key = getattr(config, 'QUIZAPI_KEY', None)
            if quizapi_key:
                quizapi = QuizAPIProvider(api_key=quizapi_key)
                self.providers["quizapi"] = quizapi
                logger.info("QuizAPI provider enabled")
            else:
                logger.info("QuizAPI provider disabled - no API key in config")
        except (ImportError, AttributeError):
            logger.info("QuizAPI provider disabled - config not available")

        logger.info(f"Initialized {len(self.providers)} trivia providers")

    def _select_random_provider(self, unified_category: Optional[UnifiedCategory] = None) -> TriviaProvider:
        """
        Select a random provider with weighted probabilities.
        Weights: OpenTDB 50%, Trivia API 30%, SF6 10%, QuizAPI 10%
        """
        available_providers = []
        weights = []

        # OpenTDB - primary provider
        if self.providers.get("opentdb") and self.providers["opentdb"].is_available:
            available_providers.append(self.providers["opentdb"])
            weights.append(50)

        # The Trivia API - good variety
        if self.providers.get("trivia_api") and self.providers["trivia_api"].is_available:
            # Check if category is supported
            trivia_api = self.providers["trivia_api"]
            if unified_category is None or unified_category in trivia_api.get_supported_categories():
                available_providers.append(trivia_api)
                weights.append(30)

        # SF6 - only for Gaming or random
        if self.providers.get("sf6") and self.providers["sf6"].is_available:
            if unified_category is None or unified_category == UnifiedCategory.GAMING:
                available_providers.append(self.providers["sf6"])
                weights.append(10)

        # QuizAPI - only for Science & Tech or random
        if self.providers.get("quizapi") and self.providers["quizapi"].is_available:
            if unified_category is None or unified_category == UnifiedCategory.SCIENCE_TECH:
                available_providers.append(self.providers["quizapi"])
                weights.append(10)

        if not available_providers:
            # Fallback to OpenTDB
            return self.providers.get("opentdb")

        # Weighted random selection
        return random.choices(available_providers, weights=weights, k=1)[0]

    def _select_provider_for_category(self, unified_category: UnifiedCategory) -> TriviaProvider:
        """
        Select a provider that supports the given category.
        Randomly chooses between available providers with weighting.
        """
        available_providers = []
        weights = []

        # OpenTDB supports all categories
        if self.providers.get("opentdb") and self.providers["opentdb"].is_available:
            available_providers.append(self.providers["opentdb"])
            weights.append(60)

        # The Trivia API - check if it supports this category
        if self.providers.get("trivia_api") and self.providers["trivia_api"].is_available:
            trivia_api = self.providers["trivia_api"]
            if unified_category in trivia_api.get_supported_categories():
                available_providers.append(trivia_api)
                weights.append(40)

        # QuizAPI - only for Science & Tech
        if unified_category == UnifiedCategory.SCIENCE_TECH:
            if self.providers.get("quizapi") and self.providers["quizapi"].is_available:
                available_providers.append(self.providers["quizapi"])
                weights.append(20)

        if not available_providers:
            return self.providers.get("opentdb")

        return random.choices(available_providers, weights=weights, k=1)[0]

    async def _fetch_question_with_fallback(
        self,
        provider: TriviaProvider,
        unified_category: Optional[UnifiedCategory],
        difficulty: Optional[str],
        guild_id: str,
        user_id: str,
    ) -> Optional[StandardQuestion]:
        """
        Fetch a question from the given provider, falling back to others if it fails.
        Tries up to 3 providers before giving up.
        """
        tried_providers = set()
        current_provider = provider
        max_attempts = 3

        for attempt in range(max_attempts):
            if current_provider is None:
                break

            tried_providers.add(current_provider.provider_id)

            # Try to fetch from current provider
            question = await current_provider.get_question(
                unified_category=unified_category,
                difficulty=difficulty,
                guild_id=guild_id,
                user_id=user_id
            )

            if question:
                return question

            logger.info(f"Provider {current_provider.name} failed, attempting fallback ({attempt + 1}/{max_attempts})")

            # Find a fallback provider we haven't tried
            current_provider = None
            fallback_order = ["opentdb", "trivia_api", "sf6", "quizapi"]

            for pid in fallback_order:
                if pid in tried_providers:
                    continue
                fallback = self.providers.get(pid)
                if fallback and fallback.is_available:
                    # Check if fallback supports the category
                    if unified_category is None or unified_category in fallback.get_supported_categories():
                        current_provider = fallback
                        break

        return None

    def _get_answer_lock(self, guild_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific guild"""
        if guild_id not in self.answer_locks:
            self.answer_locks[guild_id] = asyncio.Lock()
        return self.answer_locks[guild_id]

    async def cog_load(self):
        """Called when the cog is loaded"""
        for provider in self.providers.values():
            await provider.initialize()
        logger.info("TriviaCog providers initialized")

    async def cog_unload(self):
        """Called when the cog is unloaded"""
        # Cancel all timeout tasks
        for guild_questions in self.active_questions.values():
            for active_q in guild_questions.values():
                if active_q.timeout_task and not active_q.timeout_task.done():
                    active_q.timeout_task.cancel()

        self.active_questions.clear()
        self.answer_locks.clear()

        # Cleanup providers
        for provider in self.providers.values():
            await provider.cleanup()

        self.data_manager.force_save_all()
        logger.info("TriviaCog cleaned up")

    def _get_active_count(self, guild_id: str) -> int:
        """Get number of active questions in a guild"""
        return len(self.active_questions.get(guild_id, {}))

    def _has_active_question(self, guild_id: str, user_id: int) -> bool:
        """Check if a user has an active question"""
        return user_id in self.active_questions.get(guild_id, {})

    def _cleanup_question(self, guild_id: str, user_id: int):
        """Safely cleanup a user's question data"""
        if guild_id in self.active_questions:
            if user_id in self.active_questions[guild_id]:
                active_q = self.active_questions[guild_id][user_id]
                if active_q.timeout_task and not active_q.timeout_task.done():
                    active_q.timeout_task.cancel()
                del self.active_questions[guild_id][user_id]

            # Clean up empty guild dict
            if not self.active_questions[guild_id]:
                del self.active_questions[guild_id]

    def get_guild_id(self, interaction: discord.Interaction) -> str:
        """Get guild ID as string"""
        return str(interaction.guild.id) if interaction.guild else "DM"

    async def autocomplete_category(self, interaction: discord.Interaction, current: str):
        """Autocomplete for unified trivia categories"""
        categories = get_unified_categories()
        # Add SF6 as special option under Gaming
        all_options = categories + ["Street Fighter 6"]
        return [
            app_commands.Choice(name=f"{get_category_emoji(UnifiedCategory(cat)) if cat in categories else '🎮'} {cat}", value=cat)
            for cat in all_options
            if current.lower() in cat.lower()
        ][:25]

    async def autocomplete_difficulty(self, interaction: discord.Interaction, current: str):
        """Autocomplete for difficulty levels"""
        difficulties = ["easy", "medium", "hard"]
        return [
            app_commands.Choice(name=diff.title(), value=diff)
            for diff in difficulties
            if current.lower() in diff.lower()
        ]

    @app_commands.command(name="trivia", description="Start a trivia question!")
    @app_commands.describe(
        category="Choose a category (or leave blank for random)",
        difficulty="Choose difficulty (or leave blank for random)"
    )
    @app_commands.autocomplete(category=autocomplete_category)
    @app_commands.autocomplete(difficulty=autocomplete_difficulty)
    async def trivia(
        self,
        interaction: discord.Interaction,
        category: Optional[str] = None,
        difficulty: Optional[str] = None
    ):
        """Main trivia command with 2-player limit"""
        await interaction.response.defer()

        guild_id = self.get_guild_id(interaction)
        user_id = interaction.user.id

        # Check if user already has an active question
        if self._has_active_question(guild_id, user_id):
            embed = discord.Embed(
                title="You Already Have a Question!",
                description="Finish your current question before starting a new one.",
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Check if server is at max concurrent players
        active_count = self._get_active_count(guild_id)
        if active_count >= MAX_CONCURRENT_PLAYERS:
            # Show who's currently playing
            current_players = []
            for uid in self.active_questions.get(guild_id, {}).keys():
                user = self.bot.get_user(uid)
                if user:
                    current_players.append(user.display_name)

            embed = discord.Embed(
                title=f"Server at Max Capacity ({MAX_CONCURRENT_PLAYERS} players)",
                description=f"**Currently playing:** {', '.join(current_players)}\n\n"
                           f"Please wait for someone to finish their question.",
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Parse difficulty
        diff_enum = None
        was_intentional = difficulty is not None
        if difficulty:
            try:
                diff_enum = Difficulty(difficulty.lower())
            except ValueError:
                await interaction.followup.send("Invalid difficulty. Use: easy, medium, or hard", ephemeral=True)
                return

        # Determine provider and category
        provider = None
        unified_category = None

        if category == "Street Fighter 6":
            provider = self.providers.get("sf6")
            unified_category = UnifiedCategory.GAMING
        elif category:
            try:
                unified_category = UnifiedCategory(category)
                # Select provider that supports this category
                provider = self._select_provider_for_category(unified_category)
            except ValueError:
                await interaction.followup.send("Invalid category.", ephemeral=True)
                return
        else:
            # Random provider selection with weighted probabilities
            provider = self._select_random_provider(unified_category)

        if not provider or not provider.is_available:
            # Fallback to OpenTDB
            provider = self.providers.get("opentdb")
            if not provider or not provider.is_available:
                await interaction.followup.send("No trivia sources are available.", ephemeral=True)
                return

        # Fetch question with fallback
        question = await self._fetch_question_with_fallback(
            provider=provider,
            unified_category=unified_category,
            difficulty=diff_enum.value if diff_enum else None,
            guild_id=guild_id,
            user_id=str(user_id)
        )

        if not question:
            embed = discord.Embed(
                title="No Questions Available",
                description="Could not fetch a question from any source. Please try again.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return

        await self._create_trivia_question(interaction, question, guild_id, was_intentional)

    async def _create_trivia_question(
        self,
        interaction: discord.Interaction,
        question: StandardQuestion,
        guild_id: str,
        was_intentional: bool
    ):
        """Create and display a trivia question"""
        # Create options
        options = question.incorrect_answers + [question.correct_answer]
        random.shuffle(options)
        correct_letter = chr(65 + options.index(question.correct_answer))

        # Get difficulty enum
        difficulty = Difficulty(question.difficulty)
        is_sf6 = question.provider == "sf6"

        # Create embed
        category_emoji = get_category_emoji(question.unified_category)
        if is_sf6:
            title = f"🎮 Street Fighter 6 Trivia"
            embed_color = discord.Color.from_rgb(255, 215, 0)
        else:
            title = f"{category_emoji} {question.category} Trivia"
            embed_color = self._get_difficulty_color(difficulty)

        embed = discord.Embed(title=title, color=embed_color)

        option_text = "\n".join([f"**{chr(65+i)}. {opt}**" for i, opt in enumerate(options)])
        embed.description = f"**{question.question}**\n\n{option_text}"

        # Difficulty indicator
        diff_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
        diff_display = f"{diff_emoji[difficulty.value]} {difficulty.value.title()}"
        if was_intentional:
            diff_display += " (Higher Penalty Risk!)"
        else:
            diff_display += " (Random)"
        embed.add_field(name="Difficulty", value=diff_display, inline=True)

        # Scoring info
        if is_sf6:
            base_points = SCORING_CONFIG["base_points"]["easy"]
            scoring_note = "\n*(SF6 uses Easy scoring)*"
        else:
            base_points = SCORING_CONFIG["base_points"][difficulty.value]
            scoring_note = ""
        max_speed_bonus = SCORING_CONFIG["speed_bonuses"][0]["bonus"]
        embed.add_field(
            name="Scoring",
            value=f"**Base:** {base_points} pts\n**Max Speed:** +{max_speed_bonus} pts{scoring_note}",
            inline=True
        )

        # Show current streak
        user_stats = self.data_manager.get_user_stats(guild_id, str(interaction.user.id), interaction.user.name)
        if user_stats.current_streak > 1:
            embed.add_field(name="Current Streak", value=f"**{user_stats.current_streak}**", inline=True)

        # Show concurrent players info
        active_count = self._get_active_count(guild_id) + 1
        embed.set_footer(text=f"Only {interaction.user.name} can answer! Type A, B, C, or D. ({QUESTION_TIMEOUT}s) | Players: {active_count}/{MAX_CONCURRENT_PLAYERS}")

        msg = await interaction.followup.send(embed=embed)

        # Store active question
        if guild_id not in self.active_questions:
            self.active_questions[guild_id] = {}

        active_q = ActiveQuestion(
            user_id=interaction.user.id,
            channel_id=interaction.channel.id,
            message_id=msg.id,
            correct_letter=correct_letter,
            correct_answer=question.correct_answer,
            start_time=time.time(),
            difficulty=difficulty,
            was_intentional=was_intentional,
            question_data=question
        )

        # Start timeout task
        active_q.timeout_task = asyncio.create_task(
            self._timeout_question(guild_id, interaction.user.id)
        )

        self.active_questions[guild_id][interaction.user.id] = active_q

        logger.info(f"Started trivia for {interaction.user.name} in {guild_id} ({active_count}/{MAX_CONCURRENT_PLAYERS} players)")

    def _get_difficulty_color(self, difficulty: Difficulty) -> discord.Color:
        """Get color based on difficulty"""
        colors = {
            Difficulty.EASY: discord.Color.green(),
            Difficulty.MEDIUM: discord.Color.yellow(),
            Difficulty.HARD: discord.Color.red()
        }
        return colors.get(difficulty, discord.Color.blurple())

    async def _timeout_question(self, guild_id: str, user_id: int):
        """Handle question timeout"""
        try:
            await asyncio.sleep(QUESTION_TIMEOUT)

            if guild_id not in self.active_questions:
                return
            if user_id not in self.active_questions[guild_id]:
                return

            active_q = self.active_questions[guild_id][user_id]

            # Mark question as seen
            self.data_manager.mark_question_seen(guild_id, str(user_id), active_q.question_data.to_dict())

            # Apply timeout penalty
            penalty = SCORING_CONFIG["penalties"]["timeout"]
            self.data_manager.update_user_stats(guild_id, str(user_id), active_q.difficulty, False, QUESTION_TIMEOUT, penalty)
            user_stats = self.data_manager.get_user_stats(guild_id, str(user_id), "Unknown")

            # Update message
            channel = self.bot.get_channel(active_q.channel_id)
            if channel:
                try:
                    message = await channel.fetch_message(active_q.message_id)
                    timeout_embed = discord.Embed(
                        title="Time's Up!",
                        description=f"<@{user_id}> took too long to answer!\n\n"
                                   f"The correct answer was **{active_q.correct_letter}) {active_q.correct_answer}**\n\n"
                                   f"**Timeout Penalty:** {penalty} points\n"
                                   f"**New Score:** {user_stats.total_score} points",
                        color=discord.Color.red()
                    )
                    await message.edit(embed=timeout_embed)
                except discord.NotFound:
                    pass
                except Exception as e:
                    logger.error(f"Error updating timeout message: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in timeout handler: {e}")
        finally:
            self._cleanup_question(guild_id, user_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for trivia answers"""
        if message.author.bot or not message.guild:
            return

        guild_id = str(message.guild.id)
        user_id = message.author.id

        # Quick check before acquiring lock
        if guild_id not in self.active_questions:
            return
        if user_id not in self.active_questions.get(guild_id, {}):
            return

        # Acquire lock
        lock = self._get_answer_lock(guild_id)
        async with lock:
            try:
                # Check again under lock
                if guild_id not in self.active_questions:
                    return
                if user_id not in self.active_questions[guild_id]:
                    return

                active_q = self.active_questions[guild_id][user_id]

                # Verify channel
                if message.channel.id != active_q.channel_id:
                    return

                content = message.content.strip().upper()
                if content not in ["A", "B", "C", "D"]:
                    return

                # Cancel timeout
                if active_q.timeout_task and not active_q.timeout_task.done():
                    active_q.timeout_task.cancel()

                # Calculate score
                response_time = time.time() - active_q.start_time
                is_correct = content == active_q.correct_letter
                is_sf6 = active_q.question_data.provider == "sf6"

                user_stats = self.data_manager.get_user_stats(guild_id, str(user_id), message.author.name)

                score_change, breakdown = ScoreCalculator.calculate_final_score(
                    active_q.difficulty,
                    response_time,
                    is_correct,
                    user_stats.current_streak,
                    active_q.was_intentional,
                    is_sf6
                )

                # Mark question as seen
                self.data_manager.mark_question_seen(guild_id, str(user_id), active_q.question_data.to_dict())

                # Clean up before updating stats
                self._cleanup_question(guild_id, user_id)

                # Update stats
                self.data_manager.update_user_stats(guild_id, str(user_id), active_q.difficulty, is_correct, response_time, score_change)
                updated_stats = self.data_manager.get_user_stats(guild_id, str(user_id), message.author.name)

                # Send response
                await self._send_answer_response(
                    message, is_correct, active_q.correct_letter, active_q.correct_answer,
                    response_time, score_change, breakdown, updated_stats,
                    active_q.was_intentional, active_q.question_data
                )

            except Exception as e:
                logger.error(f"Error in trivia answer handler: {e}")
                self._cleanup_question(guild_id, user_id)

    async def _send_answer_response(
        self,
        message: discord.Message,
        is_correct: bool,
        correct_letter: str,
        correct_answer: str,
        response_time: float,
        score_change: int,
        breakdown: Dict,
        user_stats: UserStats,
        was_intentional: bool,
        question_data: StandardQuestion
    ):
        """Send the answer response embed"""
        if is_correct:
            embed = discord.Embed(
                title="Correct!",
                description=f"Excellent work, {message.author.mention}!",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="Incorrect!",
                description=f"Nice try {message.author.mention}!\n"
                           f"The correct answer was **{correct_letter}) {correct_answer}**",
                color=discord.Color.red()
            )

        score_text = (
            f"**Score Change:** `{score_change:+d}` points\n"
            f"**Total Score:** `{user_stats.total_score}` points\n"
            f"**Streak:** `{user_stats.current_streak}`\n"
            f"**Response Time:** `{response_time:.1f}s` ({breakdown['speed_tier']})\n"
        )

        if is_correct:
            score_text += (
                f"\n**Score Breakdown:**\n"
                f"Base Points: `{breakdown['base_points']}`\n"
                f"Speed Bonus: `+{breakdown['speed_bonus']}`\n"
                f"Streak Multiplier: `x{breakdown['streak_multiplier']}`"
            )
        else:
            intent_text = "Intentional Choice" if was_intentional else "Random Difficulty"
            score_text += (
                f"\n**Penalty Applied:**\n"
                f"{breakdown['penalty_reason']}: `{score_change}` points\n"
                f"Difficulty Type: {intent_text}"
            )

        embed.add_field(name="\u200b", value=score_text, inline=False)

        # SF6 explanation
        if question_data.explanation and question_data.provider == "sf6":
            embed.add_field(
                name="Frame Data Explanation",
                value=f"*{question_data.explanation}*",
                inline=False
            )

        await message.channel.send(embed=embed)

    @app_commands.command(name="trivia_stats", description="View your trivia statistics")
    async def trivia_stats(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """View trivia statistics"""
        await interaction.response.defer()

        guild_id = self.get_guild_id(interaction)
        target_user = user or interaction.user
        user_stats = self.data_manager.get_user_stats(guild_id, str(target_user.id), target_user.name)
        server_name = interaction.guild.name if interaction.guild else "DM"

        embed = discord.Embed(
            title=f"Trivia Stats - {target_user.name}",
            description=f"**Server:** {server_name}",
            color=discord.Color.blue()
        )

        accuracy = (user_stats.correct_answers / user_stats.questions_answered * 100) if user_stats.questions_answered > 0 else 0
        embed.add_field(
            name="Overall Performance",
            value=(
                f"**Total Score:** `{user_stats.total_score}`\n"
                f"**Questions Answered:** `{user_stats.questions_answered}`\n"
                f"**Accuracy:** `{accuracy:.1f}%`\n"
                f"**Current Streak:** `{user_stats.current_streak}`\n"
                f"**Best Streak:** `{user_stats.best_streak}`\n"
                f"**Avg Response Time:** `{user_stats.avg_response_time:.1f}s`"
            ),
            inline=False
        )

        # Difficulty breakdown
        diff_text = ""
        diff_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
        for diff, stats in user_stats.difficulty_stats.items():
            if stats["total"] > 0:
                acc = (stats["correct"] / stats["total"] * 100)
                diff_text += f"**{diff_emoji[diff]} {diff.title()}:** {stats['correct']}/{stats['total']} ({acc:.1f}%)\n"

        if diff_text:
            embed.add_field(name="Difficulty Breakdown", value=diff_text, inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="trivia_leaderboard", description="View the trivia leaderboard")
    async def trivia_leaderboard(self, interaction: discord.Interaction):
        """View the trivia leaderboard"""
        await interaction.response.defer()

        guild_id = self.get_guild_id(interaction)
        server_name = interaction.guild.name if interaction.guild else "DM"

        leaderboard = self.data_manager.get_server_leaderboard(guild_id, 10)

        if not leaderboard:
            embed = discord.Embed(
                title="Trivia Leaderboard",
                description=f"**{server_name}**\n\nNo players yet! Use `/trivia` to start playing.",
                color=discord.Color.gold()
            )
            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title="Trivia Leaderboard",
            description=f"**{server_name}**",
            color=discord.Color.gold()
        )

        medals = ["🥇", "🥈", "🥉"]
        leaderboard_text = ""

        for i, (user_id, stats) in enumerate(leaderboard):
            accuracy = (stats.correct_answers / stats.questions_answered * 100) if stats.questions_answered > 0 else 0
            medal = medals[i] if i < 3 else f"#{i+1}"
            leaderboard_text += f"{medal} **{stats.username}** - {stats.total_score:,} pts ({accuracy:.1f}%)\n"

        embed.add_field(name="Rankings", value=leaderboard_text, inline=False)
        embed.set_footer(text=f"Players active: {self._get_active_count(guild_id)}/{MAX_CONCURRENT_PLAYERS}")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="trivia_categories", description="View available trivia categories")
    async def trivia_categories(self, interaction: discord.Interaction):
        """Show available trivia categories"""
        await interaction.response.defer()

        embed = discord.Embed(
            title="Trivia Categories",
            description="Choose a category when starting trivia with `/trivia category:<name>`",
            color=discord.Color.blue()
        )

        for category in UnifiedCategory:
            emoji = CATEGORY_EMOJIS.get(category, "")
            # Get sub-categories
            from trivia.categories import get_category_display_info
            info = get_category_display_info()
            sub_cats = info[category.value]["sub_categories"]
            sub_text = ", ".join(sub_cats[:5])
            if len(sub_cats) > 5:
                sub_text += f" +{len(sub_cats)-5} more"

            embed.add_field(
                name=f"{emoji} {category.value}",
                value=sub_text or "Various topics",
                inline=True
            )

        embed.set_footer(text="Tip: Try 'Street Fighter 6' for hardcore frame data challenges!")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="trivia_debug", description="Debug information (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def trivia_debug(self, interaction: discord.Interaction):
        """Debug information for administrators"""
        await interaction.response.defer(ephemeral=True)

        guild_id = self.get_guild_id(interaction)

        embed = discord.Embed(
            title="Trivia Debug Information",
            color=discord.Color.purple()
        )

        # Active questions info
        active_in_guild = self._get_active_count(guild_id)
        total_active = sum(len(q) for q in self.active_questions.values())

        embed.add_field(
            name="Active Questions",
            value=f"**This Server:** {active_in_guild}/{MAX_CONCURRENT_PLAYERS}\n"
                  f"**Total (all servers):** {total_active}",
            inline=False
        )

        # Provider status
        provider_text = ""
        for pid, provider in self.providers.items():
            status = "Available" if provider.is_available else "Unavailable"
            provider_text += f"**{provider.name}:** {status}\n"

        embed.add_field(name="Providers", value=provider_text, inline=False)

        # Memory info
        memory_info = self.data_manager.get_memory_usage_info()
        embed.add_field(
            name="Memory Usage",
            value=f"**Servers Loaded:** {memory_info['servers_loaded']}\n"
                  f"**Total Users:** {memory_info['total_users']}\n"
                  f"**Pending Saves:** {memory_info['pending_saves']}",
            inline=False
        )

        await interaction.followup.send(embed=embed)
