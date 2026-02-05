# trivia/trivia.py - Multi-server trivia system with 2-player limit and provider pattern

import discord
from discord.ext import commands
from discord import app_commands
import html
import random
import asyncio
import time
import datetime
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
except (ImportError, KeyError, AttributeError) as e:
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
try:
    import config as _cfg
    AUTHORIZED_RESET_USER_ID = getattr(_cfg, 'AUTHORIZED_RESET_USER_ID', "YOUR_USER_ID_HERE")
except ImportError:
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
                    tier = "⚡ Lightning Fast!"
                elif response_time <= 6:
                    tier = "🔥 Very Fast!"
                elif response_time <= 10:
                    tier = "💨 Fast!"
                elif response_time <= 15:
                    tier = "👍 Good Speed"
                elif response_time <= 20:
                    tier = "🐢 Decent"
                else:
                    tier = "🦥 Getting Slow..."
                return bonus_config["bonus"], tier
        return 0, "⏰ Too Slow"

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
        Weights: OpenTDB 55%, Trivia API 40%, SF6 5%
        QuizAPI disabled (programming-focused questions removed)
        """
        available_providers = []
        weights = []

        # OpenTDB - primary provider
        if self.providers.get("opentdb") and self.providers["opentdb"].is_available:
            available_providers.append(self.providers["opentdb"])
            weights.append(55)

        # The Trivia API - good variety
        if self.providers.get("trivia_api") and self.providers["trivia_api"].is_available:
            # Check if category is supported
            trivia_api = self.providers["trivia_api"]
            if unified_category is None or unified_category in trivia_api.get_supported_categories():
                available_providers.append(trivia_api)
                weights.append(40)

        # SF6 - only for Gaming or random (rare - 5%)
        if self.providers.get("sf6") and self.providers["sf6"].is_available:
            if unified_category is None or unified_category == UnifiedCategory.GAMING:
                available_providers.append(self.providers["sf6"])
                weights.append(5)

        # QuizAPI disabled - programming questions removed

        if not available_providers:
            # Fallback to OpenTDB
            return self.providers.get("opentdb")

        # Weighted random selection
        return random.choices(available_providers, weights=weights, k=1)[0]

    def _select_provider_for_category(self, unified_category: UnifiedCategory) -> TriviaProvider:
        """
        Select a provider that supports the given category.
        Randomly chooses between available providers with weighting.
        QuizAPI disabled (programming questions removed)
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

        # QuizAPI disabled - programming questions removed

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
            # QuizAPI removed from fallback (programming questions disabled)
            current_provider = None
            fallback_order = ["opentdb", "trivia_api", "sf6"]

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

        choices = []
        for cat in all_options:
            if current.lower() in cat.lower():
                # Get emoji for display, but value is always plain text
                if cat == "Street Fighter 6":
                    emoji = "🎮"
                else:
                    try:
                        emoji = get_category_emoji(UnifiedCategory(cat))
                    except ValueError:
                        emoji = "❓"
                # name = what user sees (with emoji), value = what's sent (plain text)
                choices.append(app_commands.Choice(name=f"{emoji} {cat}", value=cat))

        return choices[:25]

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
            title = f"🎮⚔️ Street Fighter 6 Trivia"
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
            diff_display += " ⚠️ (Higher Penalty Risk!)"
        else:
            diff_display += " (Random)"
        embed.add_field(name="⚡ Difficulty", value=diff_display, inline=True)

        # Scoring info
        if is_sf6:
            base_points = SCORING_CONFIG["base_points"]["easy"]
            scoring_note = "\n*(🎮 SF6 uses Easy scoring)*"
        else:
            base_points = SCORING_CONFIG["base_points"][difficulty.value]
            scoring_note = ""
        max_speed_bonus = SCORING_CONFIG["speed_bonuses"][0]["bonus"]
        embed.add_field(
            name="💰 Scoring",
            value=f"**Base:** {base_points} pts\n**Max Speed:** +{max_speed_bonus} pts{scoring_note}",
            inline=True
        )

        # Show current streak
        user_stats = self.data_manager.get_user_stats(guild_id, str(interaction.user.id), interaction.user.name)
        if user_stats.current_streak > 1:
            streak_fires = "🔥" * min(user_stats.current_streak // 3, 3)
            embed.add_field(name="🔥 Current Streak", value=f"**{user_stats.current_streak}** {streak_fires}", inline=True)

        # Show concurrent players info
        active_count = self._get_active_count(guild_id) + 1
        footer_text = f"🎯 Only {interaction.user.name} can answer! Type A, B, C, or D. ({QUESTION_TIMEOUT}s) | 🎮 Players: {active_count}/{MAX_CONCURRENT_PLAYERS}"
        if is_sf6:
            footer_text += " | ⚔️ Frame data mastery required!"
        embed.set_footer(text=footer_text)

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
                        title="⏰ Time's Up!",
                        description=f"<@{user_id}> took too long to answer!\n\n"
                                   f"The correct answer was **{active_q.correct_letter}) {active_q.correct_answer}**\n\n"
                                   f"**⚠️ Timeout Penalty:** {penalty} points\n"
                                   f"**📊 New Score:** {user_stats.total_score} points",
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

                # Check for milestone (every 10 questions)
                logger.info(f"[Milestone] {message.author.name} now at {updated_stats.questions_answered} questions")
                if updated_stats.questions_answered > 0 and updated_stats.questions_answered % 10 == 0:
                    logger.info(f"[Milestone] Triggering milestone for {message.author.name} at {updated_stats.questions_answered}")
                    try:
                        await self._send_milestone_reminder(
                            message.channel, guild_id, str(user_id), updated_stats
                        )
                    except Exception as milestone_err:
                        logger.error(f"Error sending milestone reminder: {milestone_err}", exc_info=True)

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

        # Build streak display with fire emoji for streaks
        streak_display = f"`{user_stats.current_streak}`"
        if user_stats.current_streak >= 10:
            streak_display = f"🔥🔥🔥 `{user_stats.current_streak}`"
        elif user_stats.current_streak >= 5:
            streak_display = f"🔥🔥 `{user_stats.current_streak}`"
        elif user_stats.current_streak >= 3:
            streak_display = f"🔥 `{user_stats.current_streak}`"

        score_text = (
            f"**🏆 Score Change:** `{score_change:+d}` points\n"
            f"**📊 Total Score:** `{user_stats.total_score}` points\n"
            f"**🔥 Streak:** {streak_display}\n"
            f"**⏱️ Response Time:** `{response_time:.1f}s` ({breakdown['speed_tier']})\n"
        )

        if is_correct:
            score_text += (
                f"\n**🧮 Score Breakdown:**\n"
                f"• Base Points: `{breakdown['base_points']}`\n"
                f"• Speed Bonus: `+{breakdown['speed_bonus']}`\n"
                f"• 🔁 Streak Multiplier: `x{breakdown['streak_multiplier']}`"
            )
        else:
            intent_text = "Intentional Choice" if was_intentional else "Random Difficulty"
            score_text += (
                f"\n**⚠️ Penalty Applied:**\n"
                f"{breakdown['penalty_reason']}: `{score_change}` points\n"
                f"Difficulty Type: {intent_text}"
            )

        embed.add_field(name="\u200b", value=score_text, inline=False)

        # SF6 explanation
        if question_data.explanation and question_data.provider == "sf6":
            embed.add_field(
                name="🎮 Frame Data Explanation",
                value=f"*{question_data.explanation}*",
                inline=False
            )

        await message.channel.send(embed=embed)

    async def _send_milestone_reminder(self, channel, guild_id, user_id, user_stats):
        """Send a simple milestone reminder every 10 questions"""
        from services.trivia_season import get_season_info

        # Get user's rank
        leaderboard = self.data_manager.get_server_leaderboard(guild_id, 100)
        total_players = len(self.data_manager.load_server_data(guild_id))
        user_rank = total_players
        for i, (uid, stats) in enumerate(leaderboard, 1):
            if uid == user_id:
                user_rank = i
                break

        _, days_until = get_season_info()

        # Ordinal suffix (1st, 2nd, 3rd, 4th...)
        if 11 <= user_rank % 100 <= 13:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(user_rank % 10, "th")

        embed = discord.Embed(
            description=f"🕒 Heads up: Trivia season resets in **{days_until}** days. "
                        f"You are **{user_rank}{suffix}** on the current standings!",
            color=discord.Color.light_grey()
        )
        await channel.send(embed=embed)

    @app_commands.command(name="trivia_stats", description="View your trivia statistics")
    async def trivia_stats(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """View trivia statistics"""
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

        accuracy = (user_stats.correct_answers / user_stats.questions_answered * 100) if user_stats.questions_answered > 0 else 0
        embed.add_field(
            name="🏆 Overall Performance",
            value=(
                f"**💰 Total Score:** `{user_stats.total_score}`\n"
                f"**❓ Questions Answered:** `{user_stats.questions_answered}`\n"
                f"**🎯 Accuracy:** `{accuracy:.1f}%`\n"
                f"**🔥 Current Streak:** `{user_stats.current_streak}`\n"
                f"**🏅 Best Streak:** `{user_stats.best_streak}`\n"
                f"**⏱️ Avg Response Time:** `{user_stats.avg_response_time:.1f}s`"
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
                        f"🚀💫 **Ready to compete?** Use `/trivia` to start your legendary journey! 🎯✨\n\n"
                        f"🎮 **NEW:** Try `Street Fighter 6` category for frame data mastery!",
                color=discord.Color.gold()
            )
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
        footer_text = f"🎮 Players active: {self._get_active_count(guild_id)}/{MAX_CONCURRENT_PLAYERS} | 👥 Total ranked: {total_players}"

        embed.description = header + podium_text + remaining_text
        embed.set_footer(text=footer_text)

        # Add server icon as thumbnail
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        await interaction.followup.send(embed=embed)

    async def autocomplete_season(self, interaction: discord.Interaction, current: str):
        """Autocomplete for hall of fame seasons"""
        guild_id = self.get_guild_id(interaction)
        hof_data = self.data_manager.get_hall_of_fame(guild_id)

        return [
            app_commands.Choice(name=s.season_name, value=s.season_name)
            for s in hof_data
            if current.lower() in s.season_name.lower()
        ][:20]

    @app_commands.command(name="hall_of_fame", description="View archived seasons and past champions")
    @app_commands.describe(season="Specific season to view details")
    @app_commands.autocomplete(season=autocomplete_season)
    async def hall_of_fame_cmd(self, interaction: discord.Interaction, season: Optional[str] = None):
        """View the hall of fame with enhanced styling"""
        await interaction.response.defer()

        guild_id = self.get_guild_id(interaction)
        server_name = interaction.guild.name if interaction.guild else "DM"

        hof_data = self.data_manager.get_hall_of_fame(guild_id)

        if not hof_data:
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
                        f"🎭 Seasons are automatically archived on the 7th of every month!\n"
                        f"✨ Start building your legendary legacy with `/trivia`!\n\n"
                        f"🎮 **NEW:** Try Street Fighter 6 trivia for hardcore frame data challenges!✨ ",
                color=discord.Color.gold()
            )
            if interaction.guild and interaction.guild.icon:
                embed.set_thumbnail(url=interaction.guild.icon.url)
            await interaction.followup.send(embed=embed)
            return

        if season:
            # Show specific season details with enhanced styling
            season_data = None
            for s in hof_data:
                if s.season_name.lower() == season.lower():
                    season_data = s
                    break

            if not season_data:
                available_seasons = "`, `".join([s.season_name for s in hof_data])
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
            header += f"📜 **{len(hof_data)} Season{'s' if len(hof_data) != 1 else ''}**\n"
            header += f"💡 *Use `/hall_of_fame season:<name>` for detailed chronicles*\n\n"

            seasons_text = "🎭 **CHRONICLES OF CHAMPIONS** 🎭\n"

            for i, season_data in enumerate(reversed(hof_data), 1):  # Most recent first
                if season_data.leaderboard:
                    champion = season_data.leaderboard[0]

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
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

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

    @app_commands.command(name="trivia_reset_title", description="Set a custom title for the current trivia season")
    @app_commands.describe(title="The title for this season (used when the season resets on the 7th)")
    async def trivia_reset_title(self, interaction: discord.Interaction, title: str):
        """Set a custom season title that will be used at the next auto-reset"""
        if str(interaction.user.id) != AUTHORIZED_RESET_USER_ID:
            await interaction.response.send_message(
                "You don't have permission to set the season title.", ephemeral=True
            )
            return

        guild_id = self.get_guild_id(interaction)
        self.data_manager.set_season_title(guild_id, title)

        from services.trivia_season import get_season_info
        _, days_until = get_season_info()

        await interaction.response.send_message(
            f"Season title set to **{title}**. "
            f"It will be used when the season resets in **{days_until}** days."
        )
