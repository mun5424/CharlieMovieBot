import discord
import logging
from discord.ext import commands
from discord import app_commands
from clients.tmdb import search_movie_async, get_movie_details_async
from commands.autocomplete import movie_search_autocomplete
from commands.watchlist import get_movie_reviews, format_reviewers_text, add_movie_review

# Constants - balanced for Pi 5 (4GB RAM)
REVIEW_VIEW_TIMEOUT = 300  # 5 minutes


logger = logging.getLogger(__name__)


class SearchReviewModal(discord.ui.Modal):
    """Modal for entering a movie review from search results"""

    def __init__(self, movie_id: int, movie_title: str, movie_year: str):
        # Truncate title if needed (modal title max is 45 chars)
        display_title = f"{movie_title} ({movie_year})"
        if len(display_title) > 45:
            display_title = display_title[:42] + "..."
        super().__init__(title=display_title)
        self.movie_id = movie_id
        self.movie_title = movie_title
        self.movie_year = movie_year

    score = discord.ui.TextInput(
        label="Score (1-10)",
        placeholder="Enter a score from 1 to 10 (e.g., 7.5)",
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
            # Round to 1 decimal place
            score_value = round(score_value, 1)
            if score_value < 1 or score_value > 10:
                return await interaction.response.send_message(
                    "❌ Score must be between 1 and 10.", ephemeral=True
                )
        except ValueError:
            return await interaction.response.send_message(
                "❌ Score must be a number between 1 and 10 (e.g., 7.5).", ephemeral=True
            )

        # Format score for display (remove .0 for whole numbers)
        score_display = int(score_value) if score_value == int(score_value) else score_value

        # Defer before doing database work to avoid interaction timeout
        await interaction.response.defer()

        result = await add_movie_review(
            movie_id=self.movie_id,
            movie_title=self.movie_title,
            movie_year=self.movie_year,
            user_id=str(interaction.user.id),
            username=interaction.user.display_name,
            score=score_value,
            review_text=self.review_text.value
        )

        # Create embed with the review
        embed = discord.Embed(
            title=f"📝 {self.movie_title} ({self.movie_year})",
            description=self.review_text.value,
            color=0x2ecc71
        )
        embed.set_author(name=f"{interaction.user.display_name} - ⭐ {score_display}/10")

        if result == "updated":
            await interaction.followup.send(
                content=f"✅ **{interaction.user.display_name}** updated their review for **{self.movie_title} ({self.movie_year})**",
                embed=embed
            )
        else:
            await interaction.followup.send(
                content=f"✅ **{interaction.user.display_name}** submitted a review for **{self.movie_title} ({self.movie_year})**",
                embed=embed
            )


class ReviewPaginationView(discord.ui.View):
    """Paginated view for displaying reviews"""

    REVIEWS_PER_PAGE = 5

    def __init__(self, reviews: list, movie_title: str, movie_year: str):
        super().__init__(timeout=REVIEW_VIEW_TIMEOUT)
        self.reviews = reviews
        self.movie_title = movie_title
        self.movie_year = movie_year
        self.current_page = 0
        self.total_pages = (len(reviews) + self.REVIEWS_PER_PAGE - 1) // self.REVIEWS_PER_PAGE
        self.message = None
        self.update_buttons()

    def get_page_embeds(self) -> list:
        """Get embeds for current page"""
        start = self.current_page * self.REVIEWS_PER_PAGE
        end = start + self.REVIEWS_PER_PAGE
        page_reviews = self.reviews[start:end]

        embeds = []
        for review in page_reviews:
            score = review['score']
            score_text = int(score) if score == int(score) else score

            embed = discord.Embed(
                title=f"📝 {self.movie_title} ({self.movie_year})",
                description=review['review_text'],
                color=0x9b59b6
            )
            embed.set_author(name=f"{review['username']} - ⭐ {score_text}/10")
            embeds.append(embed)

        return embeds

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.grey)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embeds=self.get_page_embeds(), view=self)

    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.grey)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embeds=self.get_page_embeds(), view=self)


class SearchReviewView(discord.ui.View):
    """View with review buttons for search results"""

    def __init__(self, movie_id: int, movie_title: str, movie_year: str):
        super().__init__(timeout=REVIEW_VIEW_TIMEOUT)
        self.movie_id = movie_id
        self.movie_title = movie_title
        self.movie_year = movie_year
        self.message = None

    async def on_timeout(self):
        # Remove buttons entirely instead of disabling
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.NotFound:
                pass
            except Exception:
                pass

    @discord.ui.button(label="📖 View Reviews", style=discord.ButtonStyle.primary)
    async def view_reviews_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        reviews = await get_movie_reviews(self.movie_id)

        if not reviews:
            return await interaction.response.send_message(
                f"📭 No reviews yet for **{self.movie_title} ({self.movie_year})**"
            )

        # If 5 or fewer reviews, just show them without pagination buttons
        if len(reviews) <= ReviewPaginationView.REVIEWS_PER_PAGE:
            embeds = []
            for review in reviews:
                score = review['score']
                score_text = int(score) if score == int(score) else score
                embed = discord.Embed(
                    title=f"📝 {self.movie_title} ({self.movie_year})",
                    description=review['review_text'],
                    color=0x9b59b6
                )
                embed.set_author(name=f"{review['username']} - ⭐ {score_text}/10")
                embeds.append(embed)
            await interaction.response.send_message(embeds=embeds)
        else:
            # Use pagination view for more reviews
            view = ReviewPaginationView(reviews, self.movie_title, self.movie_year)
            await interaction.response.send_message(embeds=view.get_page_embeds(), view=view)

    @discord.ui.button(label="✍️ Write Review", style=discord.ButtonStyle.success)
    async def write_review_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SearchReviewModal(self.movie_id, self.movie_title, self.movie_year)
        await interaction.response.send_modal(modal)


def setup(bot):
    logger.info("Setting up general commands...")

    # Shared movie search logic
    async def do_movie_search(interaction: discord.Interaction, title: str):
        """Shared logic for /search and /film commands"""
        movie = await search_movie_async(title)
        if movie:
            # Get detailed info for genres, runtime, etc.
            detailed_movie = movie
            if movie.get('id'):
                detailed_info = await get_movie_details_async(movie['id'])
                if detailed_info:
                    detailed_movie = detailed_info
            
            # Format the release date nicely
            release_date = "Unknown"
            if detailed_movie.get('year') and detailed_movie['year'] != 'Unknown':
                # You could expand this to get full date from API
                release_date = f"{detailed_movie['year']}"
            
            # Format runtime
            runtime_text = "Unknown"
            if detailed_movie.get('runtime') and detailed_movie['runtime'] > 0:
                hours = detailed_movie['runtime'] // 60
                minutes = detailed_movie['runtime'] % 60
                if hours > 0:
                    runtime_text = f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
                else:
                    runtime_text = f"{minutes}m"
            
            # Format rating with star
            rating_text = "N/A"
            if detailed_movie.get('rating') and detailed_movie['rating'] > 0:
                rating = detailed_movie['rating']
                rating_text = f"⭐ {rating:.1f}/10"
            
            # Create embed with green line
            embed = discord.Embed(
                title=detailed_movie.get('title', 'Unknown Title'),
                description=detailed_movie.get('overview', 'No description available.'),
                color=0x2ecc71  # Green color for the left line
            )
            
            # Use three inline fields to create left, center, right alignment
            genre_text = detailed_movie.get('genre', 'Unknown')
            
            embed.add_field(name="**Genre**", value=genre_text, inline=True)
            embed.add_field(name="**Runtime**", value=runtime_text, inline=True)
            embed.add_field(name="**Release**", value=release_date, inline=True)
            
            # Add rating on its own line
            embed.add_field(name="**Rating**", value=rating_text, inline=False)
            
            # Add the large poster
            poster_path = detailed_movie.get('poster_path')
            if poster_path:
                embed.set_image(url=f"https://image.tmdb.org/t/p/original{poster_path}")

            # Check for reviews
            movie_id = detailed_movie.get('id') or movie.get('id')
            movie_title = detailed_movie.get('title', 'Unknown')
            movie_year = detailed_movie.get('year', 'Unknown')

            reviews = await get_movie_reviews(movie_id)

            if reviews:
                # Add reviewer names to embed
                reviewers_text = format_reviewers_text(reviews)
                embed.add_field(name="\u200b", value=reviewers_text, inline=False)

            # Create view with review buttons
            view = SearchReviewView(movie_id, movie_title, str(movie_year))
            message = await interaction.followup.send(embed=embed, view=view)
            view.message = message
        else:
            await interaction.followup.send("❌ Movie not found. Try a different search term.")

    # ALL COMMANDS MUST BE INSIDE setup(bot) FUNCTION
    @bot.tree.command(name="search", description="Search for a movie")
    @app_commands.describe(title="Start typing a movie title to see suggestions")
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def search_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()
        await do_movie_search(interaction, title)

    @bot.tree.command(name="film", description="Search for a movie")
    @app_commands.describe(title="Start typing a movie title to see suggestions")
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def film_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()
        await do_movie_search(interaction, title)

    @bot.tree.command(name="reminder_tournament", description="Run the daily tournament reminder check manually")
    async def reminder_tournament(interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)  # Acknowledge immediately

        try:
            from tourney_reminder import check_todays_tournament

            await check_todays_tournament(manual=True)

            await interaction.followup.send("✅ Manual tournament reminder check completed.", ephemeral=True)

        except ImportError as e:
            error_msg = f"❌ Import error: {e}"
            logger.error(error_msg)
            await interaction.followup.send(error_msg, ephemeral=True)

        except discord.errors.NotFound as e:
            error_msg = f"❌ Discord error (channel not found?): {e}"
            logger.error(error_msg)
            await interaction.followup.send(error_msg, ephemeral=True)

        except discord.errors.Forbidden as e:
            error_msg = f"❌ Discord permission error: {e}"
            logger.error(error_msg)
            await interaction.followup.send(error_msg, ephemeral=True)

        except Exception as e:
            error_msg = f"❌ Unexpected error: {str(e)}"
            logger.error(f"Error in /reminder_tournament: {e}")
            try:
                await interaction.followup.send(error_msg, ephemeral=True)
            except Exception:
                logger.error("Failed to send error message to Discord")