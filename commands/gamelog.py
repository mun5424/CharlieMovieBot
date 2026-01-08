# commands/gamelog.py - Game log commands using IGDB API
import logging
from datetime import datetime
from typing import Optional, List, Dict
import discord
from discord.ext import commands
from discord import app_commands

from db import (
    get_gamelog,
    get_gamelog_counts,
    add_to_gamelog,
    remove_from_gamelog,
    is_in_gamelog,
    get_gamelog_entry,
    mark_game_as_played,
    mark_game_as_unplayed,
    get_game_reviews,
    add_game_review,
    get_random_game_review,
)
from clients.igdb import search_games_async, search_games_autocomplete, get_game_by_id

logger = logging.getLogger(__name__)

# Constants
GAMELOG_VIEW_TIMEOUT = 300  # 5 minutes
GAMELOG_PAGE_SIZE = 15
AUTOCOMPLETE_LIMIT = 10


def format_game_entry(game: Dict, show_date: bool = True) -> str:
    """Format a single game entry with played status and optional date."""
    name = game.get('name', 'Unknown')
    platforms = game.get('platforms', [])
    played_at = game.get('played_at')

    # Format platforms (show first 2)
    plat_str = ""
    if platforms:
        plat_str = f" ({', '.join(platforms[:2])})"

    if played_at:
        if show_date:
            played_date = datetime.fromtimestamp(played_at)
            date_str = played_date.strftime("%b %d, %Y").lstrip("0").replace(" 0", " ")
            return f"✅ {name}{plat_str} - played {date_str}"
        else:
            return f"✅ {name}{plat_str}"
    else:
        return f"🎮 {name}{plat_str}"


def format_release_year(release_date: Optional[int]) -> Optional[int]:
    """Extract year from IGDB Unix timestamp."""
    if not release_date:
        return None
    return datetime.fromtimestamp(release_date).year


def setup(bot):
    """Setup gamelog commands"""

    def parse_igdb_id(title: str) -> Optional[int]:
        """Extract IGDB ID from autocomplete value like 'igdb:12345'."""
        if title.startswith("igdb:"):
            try:
                return int(title[5:])
            except (ValueError, IndexError):
                pass
        return None

    # Helper to parse game input (could be ID from autocomplete or search term)
    async def resolve_game(title: str) -> Optional[Dict]:
        """Resolve a game from autocomplete ID or search term."""
        # Check if it's an IGDB ID (from autocomplete selection)
        igdb_id = parse_igdb_id(title)
        if igdb_id is not None:
            return await get_game_by_id(igdb_id)
        # Otherwise search by name
        return await search_games_async(title)

    # Autocomplete for game search (uses IGDB API with fast timeout)
    async def game_search_autocomplete(interaction: discord.Interaction, current: str):
        """Autocomplete for game search using IGDB API"""
        if len(current) < 2:
            return []

        try:
            # Use fast autocomplete function (2.8s timeout, cache-first)
            results = await search_games_autocomplete(current, limit=AUTOCOMPLETE_LIMIT)
            choices = []
            for game in results:
                name = game.get("name", "")
                game_id = game.get("id")
                if not name or game_id is None:
                    continue

                year = game.get("year", "")
                platforms = game.get("platforms", [])

                # Format display name
                display = name
                if year:
                    display += f" ({year})"
                if platforms:
                    display += f" - {', '.join(platforms[:2])}"

                # Discord requires name to be 1-100 characters
                if len(display) > 100:
                    display = display[:97] + "..."
                if len(display) < 1:
                    continue

                # Use IGDB ID as value to ensure correct game is selected
                choices.append(app_commands.Choice(name=display, value=f"igdb:{game_id}"))

            return choices[:AUTOCOMPLETE_LIMIT]
        except Exception as e:
            logger.debug(f"Game autocomplete error: {e}")
            return []

    # Autocomplete for user's gamelog
    async def user_gamelog_autocomplete(interaction: discord.Interaction, current: str):
        """Autocomplete for user's gamelog"""
        try:
            uid = str(interaction.user.id)
            gamelog_list = await get_gamelog(uid)

            matching = []
            for game in gamelog_list:
                name = game.get('name', '')
                igdb_id = game.get('igdb_id')
                if not current or current.lower() in name.lower():
                    platforms = game.get('platforms', [])
                    display = f"{name} ({', '.join(platforms[:2])})" if platforms else name
                    if len(display) > 100:
                        display = display[:97] + "..."
                    # Use IGDB ID as value
                    matching.append(app_commands.Choice(name=display, value=f"igdb:{igdb_id}"))

            return matching[:AUTOCOMPLETE_LIMIT]
        except Exception as e:
            logger.error(f"Error in gamelog autocomplete: {e}")
            return []

    # ==================== GAMELOG VIEW ====================

    class GamelogView(discord.ui.View):
        """Paginated gamelog view with filter buttons"""

        def __init__(self, user_id: str, display_name: str, filter_mode: str = "all"):
            super().__init__(timeout=GAMELOG_VIEW_TIMEOUT)
            self.user_id = user_id
            self.display_name = display_name
            self.filter_mode = filter_mode
            self.current_page = 0
            self.game_list = []
            self.counts = {"total": 0, "played": 0, "backlog": 0}
            self.message = None

        async def load_data(self):
            """Load gamelog data from database"""
            self.game_list = await get_gamelog(self.user_id, self.filter_mode)
            self.counts = await get_gamelog_counts(self.user_id)
            self.update_buttons()

        def get_total_pages(self) -> int:
            return max(1, (len(self.game_list) + GAMELOG_PAGE_SIZE - 1) // GAMELOG_PAGE_SIZE)

        def create_embed(self) -> discord.Embed:
            """Create the gamelog embed for current page"""
            filter_labels = {
                "all": "Game Log",
                "backlog": "Backlog",
                "played": "Played Games"
            }
            title = f"🎮 {self.display_name}'s {filter_labels.get(self.filter_mode, 'Game Log')}"

            embed = discord.Embed(title=title, color=0x9146ff)  # Twitch purple for games

            if not self.game_list:
                if self.filter_mode == "backlog":
                    embed.add_field(name="\u200b", value="🎉 Backlog clear! No games to play.", inline=False)
                elif self.filter_mode == "played":
                    embed.add_field(name="\u200b", value="📭 No games played yet.", inline=False)
                else:
                    embed.add_field(name="\u200b", value="📭 Game log is empty. Use `/game_add` to add games!", inline=False)
            else:
                # Paginate
                start = self.current_page * GAMELOG_PAGE_SIZE
                end = start + GAMELOG_PAGE_SIZE
                page_games = self.game_list[start:end]

                # Only show dates in "played" filter mode
                show_date = self.filter_mode == "played"
                game_lines = [format_game_entry(g, show_date=show_date) for g in page_games]
                embed.add_field(name="\u200b", value="\n".join(game_lines), inline=False)

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
            self.backlog_btn.style = discord.ButtonStyle.primary if self.filter_mode == "backlog" else discord.ButtonStyle.secondary
            self.played_btn.style = discord.ButtonStyle.primary if self.filter_mode == "played" else discord.ButtonStyle.secondary

            # Hide/show pagination buttons based on page count
            if total_pages <= 1:
                # Remove pagination buttons if only one page
                if self.prev_btn in self.children:
                    self.remove_item(self.prev_btn)
                if self.next_btn in self.children:
                    self.remove_item(self.next_btn)
            else:
                # Add pagination buttons back if needed
                if self.prev_btn not in self.children:
                    self.add_item(self.prev_btn)
                if self.next_btn not in self.children:
                    self.add_item(self.next_btn)
                # Update disabled state
                self.prev_btn.disabled = self.current_page == 0
                self.next_btn.disabled = self.current_page >= total_pages - 1

        async def refresh(self, interaction: discord.Interaction):
            """Refresh the view with new data"""
            await self.load_data()
            embed = self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)

        async def on_timeout(self):
            """Remove buttons on timeout"""
            self.clear_items()
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

        @discord.ui.button(label="📅 Recent", style=discord.ButtonStyle.primary, row=0)
        async def recent_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.filter_mode = "all"
            self.current_page = 0
            await self.refresh(interaction)

        @discord.ui.button(label="🎮 Backlog", style=discord.ButtonStyle.secondary, row=0)
        async def backlog_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.filter_mode = "backlog"
            self.current_page = 0
            await self.refresh(interaction)

        @discord.ui.button(label="✅ Played", style=discord.ButtonStyle.secondary, row=0)
        async def played_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.filter_mode = "played"
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

    # ==================== GAMELOG COMMANDS ====================

    @bot.tree.command(name="game_add", description="Add a game to your backlog")
    @app_commands.describe(title="Search for a game to add")
    @app_commands.autocomplete(title=game_search_autocomplete)
    async def game_add_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        game = await resolve_game(title)

        if not game:
            return await interaction.followup.send("❌ Game not found.")

        if await is_in_gamelog(uid, game["id"]):
            return await interaction.followup.send(f"⚠️ **{game['name']}** is already in your game log.")

        await add_to_gamelog(uid, game)

        # Create embed with game info
        embed = discord.Embed(
            title=f"✅ Added to Backlog",
            description=f"**{game['name']}**",
            color=0x2ecc71
        )

        year = format_release_year(game.get("release_date"))
        if year:
            embed.add_field(name="Year", value=str(year), inline=True)
        if game.get("platforms"):
            embed.add_field(name="Platforms", value=", ".join(game["platforms"][:3]), inline=True)
        if game.get("rating"):
            embed.add_field(name="IGDB Rating", value=f"⭐ {game['rating']:.1f}/100", inline=True)
        if game.get("cover_url"):
            embed.set_thumbnail(url=game["cover_url"])

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="gamelog", description="View your game log")
    @app_commands.describe(user="Whose game log do you want to view?")
    async def gamelog_cmd(interaction: discord.Interaction, user: Optional[discord.User] = None):
        await interaction.response.defer()

        target_user = user or interaction.user
        target_uid = str(target_user.id)

        view = GamelogView(target_uid, target_user.display_name)
        await view.load_data()

        embed = view.create_embed()

        # Send with view (filter/pagination buttons work for everyone)
        message = await interaction.followup.send(embed=embed, view=view)
        view.message = message

    @bot.tree.command(name="game_played", description="Mark a game as played")
    @app_commands.describe(title="Search for a game to mark as played")
    @app_commands.autocomplete(title=game_search_autocomplete)
    async def game_played_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        game = await resolve_game(title)

        if not game:
            return await interaction.followup.send("❌ Game not found.")

        result = await mark_game_as_played(uid, game["id"], game)

        if result == "already_played":
            return await interaction.followup.send(f"⚠️ **{game['name']}** is already marked as played.")
        elif result == "marked":
            embed = discord.Embed(
                title="✅ Marked as Played",
                description=f"**{game['name']}**",
                color=0x2ecc71
            )
            if game.get("cover_url"):
                embed.set_thumbnail(url=game["cover_url"])
            await interaction.followup.send(embed=embed)
        elif result == "added_and_marked":
            embed = discord.Embed(
                title="✅ Added & Marked as Played",
                description=f"**{game['name']}**",
                color=0x2ecc71
            )
            if game.get("cover_url"):
                embed.set_thumbnail(url=game["cover_url"])
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ Something went wrong. Please try again.")

    @bot.tree.command(name="game_unplay", description="Mark a game as not played (back to backlog)")
    @app_commands.describe(title="Select a game from your game log")
    @app_commands.autocomplete(title=user_gamelog_autocomplete)
    async def game_unplay_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)

        # Try to get from user's gamelog first (avoids API call)
        igdb_id = parse_igdb_id(title)
        if igdb_id is not None:
            entry = await get_gamelog_entry(uid, igdb_id)
        else:
            # Fallback: search API then check gamelog
            game = await resolve_game(title)
            if not game:
                return await interaction.followup.send("❌ Game not found.")
            entry = await get_gamelog_entry(uid, game["id"])

        if not entry:
            return await interaction.followup.send("❌ Game not found in your game log.")

        if not entry.get("played_at"):
            return await interaction.followup.send("❌ Game isn't marked as played.")

        await mark_game_as_unplayed(uid, entry["igdb_id"])
        await interaction.followup.send(f"↩️ {interaction.user.display_name} moved **{entry['name']}** back to backlog.")

    @bot.tree.command(name="game_remove", description="Remove a game from your game log")
    @app_commands.describe(title="Select a game from your game log")
    @app_commands.autocomplete(title=user_gamelog_autocomplete)
    async def game_remove_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)

        # Try to get from user's gamelog first (avoids API call)
        igdb_id = parse_igdb_id(title)
        if igdb_id is not None:
            entry = await get_gamelog_entry(uid, igdb_id)
            if entry:
                await remove_from_gamelog(uid, igdb_id)
                return await interaction.followup.send(
                    f"🗑️ {interaction.user.display_name} removed **{entry['name']}** from their game log."
                )
            else:
                return await interaction.followup.send("❌ Game not found in your game log.")

        # Fallback: search API then remove
        game = await resolve_game(title)
        if not game:
            return await interaction.followup.send("❌ Game not found.")

        removed = await remove_from_gamelog(uid, game["id"])
        if removed:
            await interaction.followup.send(f"🗑️ {interaction.user.display_name} removed **{game['name']}** from their game log.")
        else:
            await interaction.followup.send("❌ Game not found in your game log.")

    @bot.tree.command(name="game", description="Search for a game")
    @app_commands.describe(title="Search for a game")
    @app_commands.autocomplete(title=game_search_autocomplete)
    async def game_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        game = await resolve_game(title)

        if not game:
            return await interaction.followup.send("❌ Game not found.")

        # Create detailed embed
        summary = game.get("summary", "No summary available.")
        if len(summary) > 1000:
            summary = summary[:997] + "..."

        embed = discord.Embed(
            title=game["name"],
            description=summary,
            color=0x9146ff,  # Twitch purple
            url=game.get("url")  # Link to IGDB page
        )

        year = format_release_year(game.get("release_date"))
        if year:
            embed.add_field(name="Year", value=str(year), inline=True)

        if game.get("platforms"):
            embed.add_field(name="Platforms", value=", ".join(game["platforms"][:5]), inline=True)

        if game.get("genres"):
            embed.add_field(name="Genres", value=", ".join(game["genres"][:3]), inline=True)

        if game.get("developer"):
            embed.add_field(name="Developer", value=game["developer"], inline=True)

        if game.get("rating"):
            embed.add_field(name="IGDB Rating", value=f"⭐ {game['rating']:.1f}/100", inline=True)

        if game.get("cover_url"):
            embed.set_image(url=game["cover_url"])

        # Add review buttons
        view = GameReviewView(game["id"], game["name"])
        message = await interaction.followup.send(embed=embed, view=view)
        view.message = message

    @bot.tree.command(name="game_stats", description="View your gaming statistics")
    async def game_stats_cmd(interaction: discord.Interaction):
        uid = str(interaction.user.id)
        counts = await get_gamelog_counts(uid)

        embed = discord.Embed(
            title="🎮 Your Gaming Stats",
            color=0x9146ff
        )
        embed.add_field(name="📚 Total in Log", value=counts["total"], inline=True)
        embed.add_field(name="✅ Games Played", value=counts["played"], inline=True)
        embed.add_field(name="🎮 Backlog", value=counts["backlog"], inline=True)

        if counts["total"] > 0:
            pct = round(counts["played"] / counts["total"] * 100)
            embed.add_field(name="📈 Completion", value=f"{pct}%", inline=True)

        await interaction.response.send_message(embed=embed)

    # ==================== GAME REVIEWS ====================

    class GameReviewModal(discord.ui.Modal):
        """Modal for entering a game review"""

        def __init__(self, igdb_id: int, game_name: str):
            display_title = game_name
            if len(display_title) > 45:
                display_title = display_title[:42] + "..."
            super().__init__(title=display_title)
            self.igdb_id = igdb_id
            self.game_name = game_name

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

            # Defer before doing database work to avoid interaction timeout
            await interaction.response.defer()

            result = await add_game_review(
                igdb_id=self.igdb_id,
                game_name=self.game_name,
                user_id=str(interaction.user.id),
                username=interaction.user.display_name,
                score=score_value,
                review_text=self.review_text.value
            )

            embed = discord.Embed(
                title=f"📝 {self.game_name}",
                description=self.review_text.value,
                color=0x2ecc71
            )
            embed.set_author(name=f"{interaction.user.display_name} - ⭐ {score_display}/10")

            if result == "updated":
                await interaction.followup.send(
                    content=f"✅ **{interaction.user.display_name}** updated their review for **{self.game_name}**",
                    embed=embed
                )
            else:
                await interaction.followup.send(
                    content=f"✅ **{interaction.user.display_name}** submitted a review for **{self.game_name}**",
                    embed=embed
                )

    class GameReviewView(discord.ui.View):
        """View with buttons for viewing and writing game reviews"""

        def __init__(self, igdb_id: int, game_name: str):
            super().__init__(timeout=GAMELOG_VIEW_TIMEOUT)
            self.igdb_id = igdb_id
            self.game_name = game_name
            self.message = None

        async def on_timeout(self):
            if self.message:
                try:
                    await self.message.edit(view=None)
                except Exception:
                    pass

        @discord.ui.button(label="View Reviews", style=discord.ButtonStyle.primary)
        async def view_reviews_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            reviews = await get_game_reviews(self.igdb_id)

            if not reviews:
                return await interaction.response.send_message(
                    f"📭 No reviews yet for **{self.game_name}**"
                )

            embeds = []
            for review in reviews[:5]:  # Show up to 5 reviews
                score = review['score']
                score_text = int(score) if score == int(score) else score
                embed = discord.Embed(
                    title=f"📝 {self.game_name}",
                    description=review['review_text'],
                    color=0x9b59b6
                )
                embed.set_author(name=f"{review['username']} - ⭐ {score_text}/10")
                embeds.append(embed)

            await interaction.response.send_message(embeds=embeds)

        @discord.ui.button(label="Write Review", style=discord.ButtonStyle.success)
        async def write_review_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = GameReviewModal(self.igdb_id, self.game_name)
            await interaction.response.send_modal(modal)

    @bot.tree.command(name="game_review", description="Write a review for a game")
    @app_commands.describe(title="Search for a game to review")
    @app_commands.autocomplete(title=game_search_autocomplete)
    async def game_review_cmd(interaction: discord.Interaction, title: str):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            # Interaction expired before we could respond (slow network/server)
            logger.warning("game_review: Interaction expired before defer")
            return

        game = await resolve_game(title)
        if not game:
            return await interaction.followup.send("❌ Game not found.", ephemeral=True)

        reviews = await get_game_reviews(game["id"])
        user_review = next((r for r in reviews if r["user_id"] == str(interaction.user.id)), None)

        if user_review:
            embed = discord.Embed(
                title=f"📝 Your existing review for {game['name']}",
                description=f"**Score:** {user_review['score']}/10\n\n{user_review['review_text']}",
                color=0xf39c12
            )
            embed.set_footer(text="Click 'Write Review' below to update your review")
        else:
            embed = discord.Embed(
                title=f"📝 Review {game['name']}",
                description="Click the button below to write your review!",
                color=0x9146ff
            )

        view = GameReviewView(game["id"], game["name"])
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message

    @bot.tree.command(name="game_review_random", description="Get a random game review")
    async def game_review_random_cmd(interaction: discord.Interaction):
        await interaction.response.defer()

        result = await get_random_game_review()

        if not result:
            return await interaction.followup.send(
                "📭 No game reviews have been written yet. Be the first with `/game_review`!"
            )

        review = result["review"]
        game_name = review.get("game_name", "Unknown Game")

        score = review["score"]
        score_text = int(score) if score == int(score) else score

        embed = discord.Embed(
            title=f"🎲 {game_name}",
            description=review["review_text"],
            color=0x9146ff
        )
        embed.set_author(name=f"{review['username']} - ⭐ {score_text}/10")

        await interaction.followup.send(embed=embed)

    logger.info("✅ Gamelog commands loaded")
