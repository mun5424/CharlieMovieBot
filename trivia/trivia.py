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

SCORE_FILE = "trivia_scores.json"

def load_scores():
    if not os.path.exists(SCORE_FILE):
        return {}
    with open(SCORE_FILE, "r") as f:
        return json.load(f)

def save_scores(data):
    with open(SCORE_FILE, "w") as f:
        json.dump(data, f, indent=2)

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

class TriviaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scores = load_scores()
        self.active_question = None  # (user_id, correct_letter, correct_answer, message_id, start_time, channel_id)
        self.question_timeout = 30  # 30 seconds to answer
        self.timeout_task = None

    async def autocomplete_category(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=cat, value=cat)
            for cat in TRIVIA_CATEGORIES
            if current.lower() in cat.lower()
        ][:20]

    @app_commands.command(name="trivia", description="Start a trivia question (category optional)")
    @app_commands.describe(category="Choose a trivia category (or leave blank for random)")
    @app_commands.autocomplete(category=autocomplete_category)
    async def trivia(self, interaction: discord.Interaction, category: str = None):
        await interaction.response.defer()

        # Check if there's already an active question
        if self.active_question is not None:
            active_user_id, _, _, _, start_time, _ = self.active_question
            elapsed_time = time.time() - start_time
            remaining_time = max(0, self.question_timeout - elapsed_time)
            
            # Get the user who's currently answering
            active_user = self.bot.get_user(active_user_id)
            active_username = active_user.name if active_user else "Unknown User"
            
            embed = discord.Embed(
                title="🚫 Trivia In Progress",
                description=f"**{active_username}** is currently answering a trivia question.\n\n"
                           f"⏱️ Time remaining: **{remaining_time:.0f}** seconds\n\n"
                           f"Please wait for them to finish or for the question to timeout.",
                color=discord.Color.orange()
            )
            
            await interaction.followup.send(embed=embed)
            return

        # Pick a category
        if category and category.title() in TRIVIA_CATEGORIES:
            cat_id = TRIVIA_CATEGORIES[category.title()]
            cat_name = category.title()
        else:
            cat_name, cat_id = random.choice(list(TRIVIA_CATEGORIES.items()))

        url = f"https://opentdb.com/api.php?amount=1&category={cat_id}&type=multiple"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

        if not data["results"]:
            await interaction.followup.send("❌ Could not fetch a trivia question.")
            return

        q = data["results"][0]
        question = html.unescape(q["question"])
        correct = html.unescape(q["correct_answer"])
        incorrect = [html.unescape(i) for i in q["incorrect_answers"]]

        options = incorrect + [correct]
        random.shuffle(options)
        correct_letter = chr(65 + options.index(correct))  # A, B, C, D

        embed = discord.Embed(
            title=f"🎬 {cat_name} Trivia",
            color=discord.Color.blurple()
        )

        option_text = "\n".join([f"**{chr(65+i)}. {opt}**" for i, opt in enumerate(options)])
        embed.description = f"**{question}**\n\n{option_text}"

        embed.set_footer(text=f"Only {interaction.user.name} can answer this question! Type A, B, C, or D. ({self.question_timeout}s timeout)")

        msg = await interaction.followup.send(embed=embed)

        start_time = time.time()
        self.active_question = (interaction.user.id, correct_letter, correct, msg.id, start_time, interaction.channel.id)
        
        # Start timeout task
        self.timeout_task = asyncio.create_task(self.timeout_question())

    async def timeout_question(self):
        """Handle question timeout"""
        await asyncio.sleep(self.question_timeout)
        
        if self.active_question is not None:
            user_id, correct_letter, correct_answer, msg_id, _, channel_id = self.active_question
            self.active_question = None
            
            # Get the channel and message
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    message = await channel.fetch_message(msg_id)
                    
                    timeout_embed = discord.Embed(
                        title="⏰ Time's Up!",
                        description=f"<@{user_id}> took too long to answer!\n\n"
                                   f"The correct answer was **{correct_letter}. {correct_answer}**.\n",
                        color=discord.Color.red()
                    )
                    
                    await message.edit(embed=timeout_embed)
                    
                    # Send a follow-up message to notify everyone
                    await channel.send("🎮 **Trivia is now available!** Use `/trivia` to start a new question.")
                    
                except discord.NotFound:
                    # Message was deleted, just clear the active question
                    pass
                except Exception as e:
                    print(f"Error during timeout: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        # Check if there's an active question
        if self.active_question is None:
            return

        user_id, correct_letter, correct_answer, msg_id, start_time, channel_id = self.active_question

        # Only the person who started the question can answer
        if message.author.id != user_id:
            return

        # Only accept answers in the same channel
        if message.channel.id != channel_id:
            return

        content = message.content.strip().upper()
        if content not in ["A", "B", "C", "D"]:
            return

        # Cancel the timeout task
        if self.timeout_task and not self.timeout_task.done():
            self.timeout_task.cancel()

        # Clear the active question
        self.active_question = None

        response_time = time.time() - start_time

        if content == correct_letter:
            self.scores.setdefault(str(user_id), {"username": message.author.name, "score": 0})
            self.scores[str(user_id)]["score"] += 1
            save_scores(self.scores)
            
            embed = discord.Embed(
                title="✅ Correct!",
                description=f"Well done {message.author.mention}! Your new score is **{self.scores[str(user_id)]['score']}**.\n\n"
                           f"⏱️ Response time: **{response_time:.1f}s**",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="❌ Incorrect!",
                description=f"Nice try {message.author.mention}, but the correct answer was **{correct_letter}. {correct_answer}**.\n\n"
                           f"⏱️ Response time: **{response_time:.1f}s**",
                color=discord.Color.red()
            )

        await message.channel.send(embed=embed)
        
        # Send a follow-up message to notify everyone
        await asyncio.sleep(2)  # Brief delay so the result is seen first
        await message.channel.send("🎮 **Trivia is now available!** Use `/trivia` to start a new question.")

    @app_commands.command(name="trivia_leaderboard", description="See the top trivia scorers!")
    async def trivia_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()

        if not self.scores:
            await interaction.followup.send("No scores yet!")
            return

        top = sorted(self.scores.items(), key=lambda x: x[1]["score"], reverse=True)[:10]
        embed = discord.Embed(
            title="🏆 Trivia Leaderboard",
            color=discord.Color.gold()
        )

        for i, (uid, data) in enumerate(top, 1):
            embed.add_field(
                name=f"{i}. {data['username']}",
                value=f"Score: {data['score']}",
                inline=False
            )

        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(TriviaCog(bot))