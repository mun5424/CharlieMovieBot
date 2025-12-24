# commands/anime.py - Anime watchlist commands using Jikan API
import logging
from datetime import datetime
from typing import Optional, List, Dict
import discord
from discord.ext import commands
from discord import app_commands

import sqlite_store
from jikan_client import search_anime, search_anime_async

logger = logging.getLogger(__name__)

# Constants
ANIME_VIEW_TIMEOUT = 120  # 2 minutes
ANIME_PAGE_SIZE = 15
AUTOCOMPLETE_LIMIT = 10


def format_anime_entry(anime: Dict) -> str:
    """Format a single anime entry with watched status and date."""
    title = anime.get('title', 'Unknown')
    episodes = anime.get('episodes')
    watched_at = anime.get('watched_at')

    # Format episodes
    ep_str = f" ({episodes} eps)" if episodes else ""

    if watched_at:
        watched_date = datetime.fromtimestamp(watched_at)
        date_str = watched_date.strftime("%b %d")
        return f"✅ {title}{ep_str} - watched {date_str}"
    else:
        return f"❌ {title}{ep_str}"


def setup(bot):
    """Setup anime commands"""

    # Autocomplete for anime search (uses Jikan API)
    async def anime_search_autocomplete(interaction: discord.Interaction, current: str):
        """Autocomplete for anime search using Jikan API"""
        if len(current) < 2:
            return []

        try:
            results = await search_anime(current, limit=AUTOCOMPLETE_LIMIT)
            choices = []
            for anime in results:
                title = anime.get("title", "Unknown")
                year = anime.get("year", "")
                eps = anime.get("episodes", "")

                # Format display name
                display = title
                if year:
                    display += f" ({year})"
                if eps:
                    display += f" - {eps} eps"

                # Truncate if too long
                if len(display) > 100:
                    display = display[:97] + "..."

                choices.append(app_commands.Choice(name=display, value=title))

            return choices[:AUTOCOMPLETE_LIMIT]
        except Exception as e:
            logger.error(f"Error in anime autocomplete: {e}")
            return []

    # Autocomplete for user's anime watchlist
    async def user_anime_watchlist_autocomplete(interaction: discord.Interaction, current: str):
        """Autocomplete for user's anime watchlist"""
        try:
            uid = str(interaction.user.id)
            watchlist = await sqlite_store.get_anime_watchlist(uid)

            matching = []
            for anime in watchlist:
                title = anime.get('title', '')
                if not current or current.lower() in title.lower():
                    eps = anime.get('episodes', '')
                    display = f"{title} ({eps} eps)" if eps else title
                    if len(display) > 100:
                        display = display[:97] + "..."
                    matching.append(app_commands.Choice(name=display, value=title))

            return matching[:AUTOCOMPLETE_LIMIT]
        except Exception as e:
            logger.error(f"Error in anime watchlist autocomplete: {e}")
            return []

    # ==================== ANIME WATCHLIST VIEW ====================

    class AnimeWatchlistView(discord.ui.View):
        """Paginated anime watchlist view with filter buttons"""

        def __init__(self, user_id: str, display_name: str, filter_mode: str = "all"):
            super().__init__(timeout=ANIME_VIEW_TIMEOUT)
            self.user_id = user_id
            self.display_name = display_name
            self.filter_mode = filter_mode
            self.current_page = 0
            self.anime_list = []
            self.counts = {"total": 0, "watched": 0, "unwatched": 0}
            self.message = None

        async def load_data(self):
            """Load anime watchlist data from database"""
            self.anime_list = await sqlite_store.get_anime_watchlist(self.user_id, self.filter_mode)
            self.counts = await sqlite_store.get_anime_watchlist_counts(self.user_id)
            self.update_buttons()

        def get_total_pages(self) -> int:
            return max(1, (len(self.anime_list) + ANIME_PAGE_SIZE - 1) // ANIME_PAGE_SIZE)

        def create_embed(self) -> discord.Embed:
            """Create the anime watchlist embed for current page"""
            filter_labels = {
                "all": "Anime Watchlist",
                "unwatched": "Unwatched Anime",
                "watched": "Watched Anime"
            }
            title = f"🎌 {self.display_name}'s {filter_labels.get(self.filter_mode, 'Anime Watchlist')}"

            embed = discord.Embed(title=title, color=0xe91e63)  # Pink for anime

            if not self.anime_list:
                if self.filter_mode == "unwatched":
                    embed.add_field(name="\u200b", value="🎉 All caught up! No unwatched anime.", inline=False)
                elif self.filter_mode == "watched":
                    embed.add_field(name="\u200b", value="📭 No anime watched yet.", inline=False)
                else:
                    embed.add_field(name="\u200b", value="📭 Anime watchlist is empty. Use `/anime_add` to add anime!", inline=False)
            else:
                # Paginate
                start = self.current_page * ANIME_PAGE_SIZE
                end = start + ANIME_PAGE_SIZE
                page_anime = self.anime_list[start:end]

                anime_lines = [format_anime_entry(a) for a in page_anime]
                embed.add_field(name="\u200b", value="\n".join(anime_lines), inline=False)

                # Page indicator - only show if more than 1 page
                total_pages = self.get_total_pages()
                if total_pages > 1:
                    embed.set_footer(text=f"Page {self.current_page + 1} of {total_pages}")

            return embed

        def update_buttons(self):
            """Update button states based on current filter and page"""
            total_pages = self.get_total_pages()

            # Filter buttons - highlight active filter
            self.recent_btn.style = discord.ButtonStyle.primary if self.filter_mode == "all" else discord.ButtonStyle.secondary
            self.unwatched_btn.style = discord.ButtonStyle.primary if self.filter_mode == "unwatched" else discord.ButtonStyle.secondary
            self.watched_btn.style = discord.ButtonStyle.primary if self.filter_mode == "watched" else discord.ButtonStyle.secondary

            # Pagination buttons
            self.prev_btn.disabled = self.current_page == 0
            self.next_btn.disabled = self.current_page >= total_pages - 1

            if total_pages <= 1:
                self.prev_btn.disabled = True
                self.next_btn.disabled = True

        async def refresh(self, interaction: discord.Interaction):
            """Refresh the view with new data"""
            await self.load_data()
            embed = self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)

        async def on_timeout(self):
            """Disable buttons on timeout"""
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    embed = self.create_embed()
                    embed.set_footer(text="⏰ View expired. Use /anime_watchlist to refresh.")
                    await self.message.edit(embed=embed, view=self)
                except Exception:
                    pass

        @discord.ui.button(label="📅 Recent", style=discord.ButtonStyle.primary, row=0)
        async def recent_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.filter_mode = "all"
            self.current_page = 0
            await self.refresh(interaction)

        @discord.ui.button(label="❌ Unwatched", style=discord.ButtonStyle.secondary, row=0)
        async def unwatched_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.filter_mode = "unwatched"
            self.current_page = 0
            await self.refresh(interaction)

        @discord.ui.button(label="✅ Watched", style=discord.ButtonStyle.secondary, row=0)
        async def watched_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.filter_mode = "watched"
            self.current_page = 0
            await self.refresh(interaction)

        @discord.ui.button(label="⬅️", style=discord.ButtonStyle.grey, row=1)
        async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page > 0:
                self.current_page -= 1
                self.update_buttons()
                embed = self.create_embed()
                await interaction.response.edit_message(embed=embed, view=self)

        @discord.ui.button(label="➡️", style=discord.ButtonStyle.grey, row=1)
        async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page < self.get_total_pages() - 1:
                self.current_page += 1
                self.update_buttons()
                embed = self.create_embed()
                await interaction.response.edit_message(embed=embed, view=self)

    # ==================== ANIME COMMANDS ====================

    @bot.tree.command(name="anime_add", description="Add an anime to your watchlist")
    @app_commands.describe(title="Search for an anime to add")
    @app_commands.autocomplete(title=anime_search_autocomplete)
    async def anime_add_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        anime = await search_anime_async(title)

        if not anime:
            return await interaction.followup.send("❌ Anime not found.")

        if await sqlite_store.is_in_anime_watchlist(uid, anime["mal_id"]):
            return await interaction.followup.send(f"⚠️ **{anime['title']}** is already in your anime watchlist.")

        await sqlite_store.add_to_anime_watchlist(uid, anime)

        # Create embed with anime info
        embed = discord.Embed(
            title=f"✅ Added to Anime Watchlist",
            description=f"**{anime['title']}**",
            color=0x2ecc71
        )
        if anime.get("episodes"):
            embed.add_field(name="Episodes", value=anime["episodes"], inline=True)
        if anime.get("score"):
            embed.add_field(name="MAL Score", value=f"⭐ {anime['score']}", inline=True)
        if anime.get("status"):
            embed.add_field(name="Status", value=anime["status"], inline=True)
        if anime.get("image_url"):
            embed.set_thumbnail(url=anime["image_url"])

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="anime_watchlist", description="View your anime watchlist")
    @app_commands.describe(user="Whose anime watchlist do you want to view?")
    async def anime_watchlist_cmd(interaction: discord.Interaction, user: Optional[discord.User] = None):
        await interaction.response.defer()

        target_user = user or interaction.user
        is_self = target_user.id == interaction.user.id
        target_uid = str(target_user.id)

        view = AnimeWatchlistView(target_uid, target_user.display_name)
        await view.load_data()

        embed = view.create_embed()

        if is_self:
            message = await interaction.followup.send(embed=embed, view=view)
            view.message = message
        else:
            await interaction.followup.send(embed=embed)

    @bot.tree.command(name="anime_watched", description="Mark an anime as watched")
    @app_commands.describe(title="Search for an anime to mark as watched")
    @app_commands.autocomplete(title=anime_search_autocomplete)
    async def anime_watched_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        anime = await search_anime_async(title)

        if not anime:
            return await interaction.followup.send("❌ Anime not found.")

        result = await sqlite_store.mark_anime_as_watched(uid, anime["mal_id"], anime)

        if result == "already_watched":
            return await interaction.followup.send(f"⚠️ **{anime['title']}** is already marked as watched.")
        elif result == "marked":
            await interaction.followup.send(f"✅ {interaction.user.display_name} marked **{anime['title']}** as watched!")
        elif result == "added_and_marked":
            await interaction.followup.send(f"✅ {interaction.user.display_name} added **{anime['title']}** to watchlist and marked it as watched!")
        else:
            await interaction.followup.send("❌ Something went wrong. Please try again.")

    @bot.tree.command(name="anime_unwatch", description="Mark an anime as unwatched")
    @app_commands.describe(title="Select an anime from your watchlist")
    @app_commands.autocomplete(title=user_anime_watchlist_autocomplete)
    async def anime_unwatch_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        anime = await search_anime_async(title)

        if not anime:
            return await interaction.followup.send("❌ Anime not found.")

        entry = await sqlite_store.get_anime_watchlist_entry(uid, anime["mal_id"])
        if not entry:
            return await interaction.followup.send("❌ Anime not found in your watchlist.")

        if not entry.get("watched_at"):
            return await interaction.followup.send("❌ Anime isn't marked as watched.")

        await sqlite_store.mark_anime_as_unwatched(uid, anime["mal_id"])
        await interaction.followup.send(f"↩️ {interaction.user.display_name} unmarked **{anime['title']}** as watched.")

    @bot.tree.command(name="anime_remove", description="Remove an anime from your watchlist")
    @app_commands.describe(title="Select an anime from your watchlist")
    @app_commands.autocomplete(title=user_anime_watchlist_autocomplete)
    async def anime_remove_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        anime = await search_anime_async(title)

        if not anime:
            return await interaction.followup.send("❌ Anime not found.")

        removed = await sqlite_store.remove_from_anime_watchlist(uid, anime["mal_id"])
        if removed:
            await interaction.followup.send(f"🗑️ {interaction.user.display_name} removed **{anime['title']}** from their anime watchlist.")
        else:
            await interaction.followup.send("❌ Anime not found in your watchlist.")

    @bot.tree.command(name="anime_search", description="Search for an anime")
    @app_commands.describe(title="Search for an anime")
    @app_commands.autocomplete(title=anime_search_autocomplete)
    async def anime_search_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        anime = await search_anime_async(title)

        if not anime:
            return await interaction.followup.send("❌ Anime not found.")

        # Create detailed embed
        embed = discord.Embed(
            title=anime["title"],
            description=anime.get("synopsis", "No synopsis available.")[:500] + "..." if len(anime.get("synopsis", "")) > 500 else anime.get("synopsis", "No synopsis available."),
            color=0xe91e63,
            url=f"https://myanimelist.net/anime/{anime['mal_id']}"
        )

        if anime.get("title_japanese") and anime["title_japanese"] != anime["title"]:
            embed.add_field(name="Japanese Title", value=anime["title_japanese"], inline=False)

        if anime.get("episodes"):
            embed.add_field(name="Episodes", value=anime["episodes"], inline=True)
        if anime.get("score"):
            embed.add_field(name="MAL Score", value=f"⭐ {anime['score']}", inline=True)
        if anime.get("status"):
            embed.add_field(name="Status", value=anime["status"], inline=True)
        if anime.get("year"):
            embed.add_field(name="Year", value=anime["year"], inline=True)
        if anime.get("type"):
            embed.add_field(name="Type", value=anime["type"], inline=True)

        if anime.get("image_url"):
            embed.set_thumbnail(url=anime["image_url"])


        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="anime_stats", description="View your anime watching statistics")
    async def anime_stats_cmd(interaction: discord.Interaction):
        uid = str(interaction.user.id)
        counts = await sqlite_store.get_anime_watchlist_counts(uid)

        embed = discord.Embed(
            title="🎌 Your Anime Stats",
            color=0xe91e63
        )
        embed.add_field(name="📺 Total in Watchlist", value=counts["total"], inline=True)
        embed.add_field(name="✅ Anime Watched", value=counts["watched"], inline=True)
        embed.add_field(name="❌ Still to Watch", value=counts["unwatched"], inline=True)

        if counts["total"] > 0:
            pct = round(counts["watched"] / counts["total"] * 100)
            embed.add_field(name="📈 Completion", value=f"{pct}%", inline=True)

        await interaction.response.send_message(embed=embed)

    # ==================== ANIME REVIEWS ====================

    class AnimeReviewModal(discord.ui.Modal):
        """Modal for entering an anime review"""

        def __init__(self, mal_id: int, anime_title: str):
            display_title = anime_title
            if len(display_title) > 45:
                display_title = display_title[:42] + "..."
            super().__init__(title=display_title)
            self.mal_id = mal_id
            self.anime_title = anime_title

        score = discord.ui.TextInput(
            label="Score (1-10)",
            placeholder="Enter a score from 1 to 10 (e.g., 8.5)",
            min_length=1,
            max_length=4,
            required=True
        )

        review_text = discord.ui.TextInput(
            label="Your Review",
            style=discord.TextStyle.paragraph,
            placeholder="Write your review here...",
            min_length=10,
            max_length=2000,
            required=True
        )

        async def on_submit(self, interaction: discord.Interaction):
            try:
                score_value = float(self.score.value)
                score_value = round(score_value, 1)
                if score_value < 1 or score_value > 10:
                    return await interaction.response.send_message(
                        "❌ Score must be between 1 and 10.", ephemeral=True
                    )
            except ValueError:
                return await interaction.response.send_message(
                    "❌ Score must be a number between 1 and 10 (e.g., 8.5).", ephemeral=True
                )

            score_display = int(score_value) if score_value == int(score_value) else score_value

            result = await sqlite_store.add_anime_review(
                mal_id=self.mal_id,
                anime_title=self.anime_title,
                user_id=str(interaction.user.id),
                username=interaction.user.display_name,
                score=score_value,
                review_text=self.review_text.value
            )

            embed = discord.Embed(
                title=f"📝 {self.anime_title}",
                description=self.review_text.value,
                color=0x2ecc71
            )
            embed.set_author(name=f"{interaction.user.display_name} - ⭐ {score_display}/10")

            if result == "updated":
                await interaction.response.send_message(
                    content=f"✅ **{interaction.user.display_name}** updated their review for **{self.anime_title}**",
                    embed=embed
                )
            else:
                await interaction.response.send_message(
                    content=f"✅ **{interaction.user.display_name}** submitted a review for **{self.anime_title}**",
                    embed=embed
                )

    class AnimeReviewView(discord.ui.View):
        """View with buttons for viewing and writing anime reviews"""

        def __init__(self, mal_id: int, anime_title: str):
            super().__init__(timeout=ANIME_VIEW_TIMEOUT)
            self.mal_id = mal_id
            self.anime_title = anime_title
            self.message = None

        async def on_timeout(self):
            if self.message:
                try:
                    await self.message.edit(view=None)
                except Exception:
                    pass

        @discord.ui.button(label="📖 View Reviews", style=discord.ButtonStyle.primary)
        async def view_reviews_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            reviews = await sqlite_store.get_anime_reviews(self.mal_id)

            if not reviews:
                return await interaction.response.send_message(
                    f"📭 No reviews yet for **{self.anime_title}**"
                )

            embeds = []
            for review in reviews[:5]:  # Show up to 5 reviews
                score = review['score']
                score_text = int(score) if score == int(score) else score
                embed = discord.Embed(
                    title=f"📝 {self.anime_title}",
                    description=review['review_text'],
                    color=0x9b59b6
                )
                embed.set_author(name=f"{review['username']} - ⭐ {score_text}/10")
                embeds.append(embed)

            await interaction.response.send_message(embeds=embeds)

        @discord.ui.button(label="✍️ Write Review", style=discord.ButtonStyle.success)
        async def write_review_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = AnimeReviewModal(self.mal_id, self.anime_title)
            await interaction.response.send_modal(modal)

    @bot.tree.command(name="anime_review", description="Write a review for an anime")
    @app_commands.describe(title="Search for an anime to review")
    @app_commands.autocomplete(title=anime_search_autocomplete)
    async def anime_review_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer(ephemeral=True)

        anime = await search_anime_async(title)
        if not anime:
            return await interaction.followup.send("❌ Anime not found.", ephemeral=True)

        reviews = await sqlite_store.get_anime_reviews(anime["mal_id"])
        user_review = next((r for r in reviews if r["user_id"] == str(interaction.user.id)), None)

        if user_review:
            embed = discord.Embed(
                title=f"📝 Your existing review for {anime['title']}",
                description=f"**Score:** {user_review['score']}/10\n\n{user_review['review_text']}",
                color=0xf39c12
            )
            embed.set_footer(text="Click 'Write Review' below to update your review")
        else:
            embed = discord.Embed(
                title=f"📝 Review {anime['title']}",
                description="Click the button below to write your review!",
                color=0xe91e63
            )

        view = AnimeReviewView(anime["mal_id"], anime["title"])
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message

    @bot.tree.command(name="anime_review_random", description="Get a random anime review")
    async def anime_review_random_cmd(interaction: discord.Interaction):
        await interaction.response.defer()

        result = await sqlite_store.get_random_anime_review()

        if not result:
            return await interaction.followup.send(
                "📭 No anime reviews have been written yet. Be the first with `/anime_review`!"
            )

        review = result["review"]
        anime_title = review.get("anime_title", "Unknown Anime")

        score = review["score"]
        score_text = int(score) if score == int(score) else score

        embed = discord.Embed(
            title=f"🎲 {anime_title}",
            description=review["review_text"],
            color=0xe91e63
        )
        embed.set_author(name=f"{review['username']} - ⭐ {score_text}/10")

        await interaction.followup.send(embed=embed)

    logger.info("✅ Anime commands loaded")
