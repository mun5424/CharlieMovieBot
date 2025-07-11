import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
import html
import json
import random
import os

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
        self.active_questions = {}  # user_id -> (correct_letter, message_id)

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

        embed.set_footer(text="Type A, B, C, or D to answer.")

        msg = await interaction.followup.send(embed=embed)

        self.active_questions[interaction.user.id] = (correct_letter, msg.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        user_id = message.author.id
        if user_id not in self.active_questions:
            return

        content = message.content.strip().upper()
        if content not in ["A", "B", "C", "D"]:
            return

        correct_letter, _ = self.active_questions.pop(user_id)

        if content == correct_letter:
            self.scores.setdefault(str(user_id), {"username": message.author.name, "score": 0})
            self.scores[str(user_id)]["score"] += 1
            save_scores(self.scores)
            embed = discord.Embed(
                title="✅ Correct!",
                description=f"Well done {message.author.mention}! Your new score is **{self.scores[str(user_id)]['score']}**.",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="❌ Incorrect!",
                description=f"Nice try {message.author.mention}, but the correct answer was **{correct_letter}**.",
                color=discord.Color.red()
            )

        await message.channel.send(embed=embed)

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