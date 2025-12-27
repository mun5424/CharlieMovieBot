# commands/watchlist.py - Updated with unified watchlist (watched status integrated)
import logging
from datetime import datetime
from typing import Optional, List, Dict
import discord
from discord.ext import commands
from discord import app_commands
from clients.tmdb import search_movie_async
from commands.autocomplete import movie_search_autocomplete, AUTOCOMPLETE_LIMIT

# Import database functions
from db import (
    get_movie_reviews as _get_movie_reviews,
    add_movie_review as _add_movie_review,
    format_reviewers_text as _format_reviewers_text,
    get_random_review as _get_random_review,
    get_user_watchlist,
    get_watchlist_counts,
    add_to_watchlist,
    remove_from_watchlist,
    is_in_watchlist,
    get_watchlist_movie,
    mark_as_watched,
    mark_as_unwatched,
    get_user_watched,
    get_user_pending,
    add_pending_suggestion,
    get_pending_by_movie_id,
    remove_pending_by_movie_id,
)

logger = logging.getLogger(__name__)

# Constants - reduced for Pi Zero 2 W memory efficiency
SUGGESTION_VIEW_TIMEOUT = 120  # 2 minutes (reduced from 5)
WATCHLIST_PAGE_SIZE = 15  # Movies per page in watchlist
REVIEW_VIEW_TIMEOUT = 120  # 2 minutes (reduced from 5)


# Re-export from db for compatibility with general.py imports
async def get_movie_reviews(movie_id: int) -> List[Dict]:
    """Get all reviews for a specific movie"""
    return await _get_movie_reviews(movie_id)


async def add_movie_review(movie_id: int, movie_title: str, movie_year: str,
                           user_id: str, username: str, score: float, review_text: str) -> str:
    """Add a review for a movie"""
    return await _add_movie_review(
        movie_id, movie_title, movie_year, user_id, username, score, review_text
    )


def format_reviewers_text(reviews: List[Dict]) -> str:
    """Format the reviewer names for display"""
    return _format_reviewers_text(reviews)


async def get_random_review() -> Optional[Dict]:
    """Get a random review from all movies"""
    return await _get_random_review()


def format_watchlist_entry(movie: Dict, show_date: bool = True) -> str:
    """Format a single watchlist entry with watched status and optional date."""
    title = movie.get('title', 'Unknown')
    year = movie.get('year', '')
    watched_at = movie.get('watched_at')

    if watched_at:
        if show_date:
            # Format the watched date
            watched_date = datetime.fromtimestamp(watched_at)
            date_str = watched_date.strftime("%b %-d %y")
            return f"✅ {title} ({year}) - watched {date_str}"
        else:
            return f"✅ {title} ({year})"
    else:
        return f"❌ {title} ({year})"


def setup(bot):
    # Helper functions for finding movies in lists (used by Views)
    def find_movie_by_id(movie_list, movie_id):
        """Find a movie in a list by its TMDB ID. Returns the movie dict or None."""
        for movie in movie_list:
            if movie.get("id") == movie_id:
                return movie
        return None

    def find_pending_by_id(pending_list, movie_id):
        """Find a pending suggestion by movie ID. Returns the suggestion dict or None."""
        for suggestion in pending_list:
            if suggestion.get("movie", {}).get("id") == movie_id:
                return suggestion
        return None

    # Autocomplete for user's watchlist (for /remove command)
    async def user_watchlist_autocomplete(interaction: discord.Interaction, current: str):
        try:
            uid = str(interaction.user.id)
            watchlist = await get_user_watchlist(uid)

            matching_movies = []
            for movie in watchlist:
                try:
                    title = str(movie.get('title', ''))
                    year = str(movie.get('year', 'Unknown'))

                    if not current or current.lower() in title.lower():
                        matching_movies.append(
                            app_commands.Choice(name=f"{title} ({year})", value=title)
                        )
                except Exception:
                    continue

            return matching_movies[:AUTOCOMPLETE_LIMIT]

        except Exception as e:
            logger.error(f"Fatal error in watchlist autocomplete: {e}", exc_info=True)
            return []

    # Autocomplete for user's watched movies
    async def user_watched_autocomplete(interaction: discord.Interaction, current: str):
        try:
            uid = str(interaction.user.id)
            watched = await get_user_watched(uid)

            matching_movies = []
            for movie in watched:
                try:
                    title = str(movie.get('title', ''))
                    year = str(movie.get('year', 'Unknown'))

                    if not current or current.lower() in title.lower():
                        matching_movies.append(
                            app_commands.Choice(name=f"{title} ({year})", value=title)
                        )
                except Exception:
                    continue

            return matching_movies[:AUTOCOMPLETE_LIMIT]
        except Exception as e:
            logger.error(f"Error in watched autocomplete: {e}")
            return []

    # Autocomplete for user's pending suggestions
    async def user_pending_autocomplete(interaction: discord.Interaction, current: str):
        try:
            uid = str(interaction.user.id)
            pending = await get_user_pending(uid)

            matching_movies = []
            for suggestion in pending:
                try:
                    movie = suggestion.get("movie", {})
                    title = str(movie.get('title', ''))
                    year = str(movie.get('year', 'Unknown'))
                    from_user = suggestion.get('from_username', 'Unknown')

                    if not current or current.lower() in title.lower():
                        matching_movies.append(
                            app_commands.Choice(
                                name=f"{title} ({year}) - from {from_user}",
                                value=title
                            )
                        )
                except Exception:
                    continue

            return matching_movies[:AUTOCOMPLETE_LIMIT]
        except Exception as e:
            logger.error(f"Error in pending autocomplete: {e}")
            return []

    # ==================== WATCHLIST VIEW WITH FILTER BUTTONS ====================

    class WatchlistView(discord.ui.View):
        """Paginated watchlist view with filter buttons"""

        def __init__(self, user_id: str, display_name: str, filter_mode: str = "all"):
            super().__init__(timeout=SUGGESTION_VIEW_TIMEOUT)
            self.user_id = user_id
            self.display_name = display_name
            self.filter_mode = filter_mode
            self.current_page = 0
            self.movies = []
            self.counts = {"total": 0, "watched": 0, "unwatched": 0}
            self.message = None

        async def load_data(self):
            """Load watchlist data from database"""
            self.movies = await get_user_watchlist(self.user_id, self.filter_mode)
            self.counts = await get_watchlist_counts(self.user_id)
            self.update_buttons()

        def get_total_pages(self) -> int:
            return max(1, (len(self.movies) + WATCHLIST_PAGE_SIZE - 1) // WATCHLIST_PAGE_SIZE)

        def create_embed(self) -> discord.Embed:
            """Create the watchlist embed for current page"""
            # Title based on filter mode
            filter_labels = {
                "all": "Watchlist",
                "unwatched": "Unwatched Movies",
                "watched": "Watched Movies"
            }
            title = f"🎬 {self.display_name}'s {filter_labels.get(self.filter_mode, 'Watchlist')}"

            embed = discord.Embed(title=title, color=0x3498db)

            if not self.movies:
                if self.filter_mode == "unwatched":
                    embed.add_field(name="\u200b", value="🎉 All caught up! No unwatched movies.", inline=False)
                elif self.filter_mode == "watched":
                    embed.add_field(name="\u200b", value="📭 No movies watched yet.", inline=False)
                else:
                    embed.add_field(name="\u200b", value="📭 Watchlist is empty. Use `/add` to add movies!", inline=False)
            else:
                # Paginate
                start = self.current_page * WATCHLIST_PAGE_SIZE
                end = start + WATCHLIST_PAGE_SIZE
                page_movies = self.movies[start:end]

                # Only show dates in "Watched" filter
                show_date = self.filter_mode == "watched"
                movie_lines = [format_watchlist_entry(m, show_date=show_date) for m in page_movies]
                embed.add_field(name="\u200b", value="\n".join(movie_lines), inline=False)

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

    @bot.tree.command(name="add", description="Add a movie to your watchlist")
    @app_commands.describe(title="Start typing a movie title to see suggestions")
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def add_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        if await is_in_watchlist(uid, mov["id"]):
            return await interaction.followup.send("⚠️ Already in your watchlist.")

        await add_to_watchlist(uid, mov)

        embed = discord.Embed(
            title="✅ Added to Watchlist",
            description=f"**{mov['title']} ({mov['year']})**",
            color=0x2ecc71
        )
        if mov.get("poster_path"):
            embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w200{mov['poster_path']}")

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="suggest", description="Suggest a movie to another user's watchlist")
    @app_commands.describe(
        user="The user to suggest the movie to",
        title="Start typing a movie title to see suggestions"
    )
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def suggest_cmd(interaction: discord.Interaction, user: discord.Member, title: str):
        await interaction.response.defer()

        target_uid = str(user.id)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Check if movie is already in their watchlist (unified - includes watched)
        existing = await get_watchlist_movie(target_uid, mov["id"])
        if existing:
            if existing.get("watched_at"):
                return await interaction.followup.send(f"⚠️ {user.display_name} has already watched **{mov['title']} ({mov['year']})**.")
            else:
                return await interaction.followup.send(f"⚠️ **{mov['title']} ({mov['year']})** is already in {user.display_name}'s watchlist.")

        # Check if suggestion already exists
        existing_pending = await get_pending_by_movie_id(target_uid, mov["id"])
        if existing_pending:
            return await interaction.followup.send(f"⚠️ **{mov['title']} ({mov['year']})** has already been suggested to {user.display_name}.")

        # Add suggestion to pending list
        await add_pending_suggestion(
            target_uid,
            str(interaction.user.id),
            interaction.user.display_name,
            mov
        )

        # Send confirmation to suggester
        await interaction.followup.send(f"📬 Suggested **{mov['title']} ({mov['year']})** to {user.display_name}!")

    @bot.tree.command(name="pending", description="View your pending movie suggestions")
    async def pending_cmd(interaction: discord.Interaction):
        suggestions = await get_user_pending(str(interaction.user.id))

        if not suggestions:
            return await interaction.response.send_message("📭 You have no pending movie suggestions.")

        embed = discord.Embed(
            title=f"📬 {interaction.user.display_name}'s Pending Suggestions",
            description=f"You have {len(suggestions)} pending suggestion{'s' if len(suggestions) != 1 else ''}",
            color=0xf39c12
        )

        suggestion_list = []
        for i, suggestion in enumerate(suggestions, 1):
            movie = suggestion["movie"]
            from_user = suggestion.get("from_username", "Unknown")
            suggestion_list.append(f"{i}. **{movie['title']} ({movie['year']})** - from {from_user}")

        embed.add_field(
            name="🎬 Movies",
            value="\n".join(suggestion_list),
            inline=False
        )

        embed.add_field(
            name="💡 Next Steps",
            value="Use `/approve <movie>` to add to watchlist\nUse `/decline <movie>` to reject suggestion",
            inline=False
        )

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="approve", description="Approve a pending movie suggestion")
    @app_commands.describe(title="Select a movie from your pending suggestions")
    @app_commands.autocomplete(title=user_pending_autocomplete)
    async def approve_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Find the suggestion in pending list by ID
        pending_suggestion = await get_pending_by_movie_id(uid, mov["id"])

        if not pending_suggestion:
            return await interaction.followup.send("❌ No pending suggestion found for this movie.")

        from_user = pending_suggestion.get("from_username", "Unknown")

        # Check if already in watchlist
        if await is_in_watchlist(uid, mov["id"]):
            # Remove from pending but don't add duplicate
            await remove_pending_by_movie_id(uid, mov["id"])
            return await interaction.followup.send(f"⚠️ **{mov['title']} ({mov['year']})** is already in your watchlist. Removed from pending.")

        # Remove from pending and add to watchlist
        await remove_pending_by_movie_id(uid, mov["id"])
        await add_to_watchlist(uid, mov)

        await interaction.followup.send(f"✅ {interaction.user.display_name} approved **{mov['title']} ({mov['year']})** from {from_user} and added it to their watchlist!")

    @bot.tree.command(name="decline", description="Decline a pending movie suggestion")
    @app_commands.describe(title="Select a movie from your pending suggestions")
    @app_commands.autocomplete(title=user_pending_autocomplete)
    async def decline_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Find the suggestion in pending list by ID
        pending_suggestion = await get_pending_by_movie_id(uid, mov["id"])

        if not pending_suggestion:
            return await interaction.followup.send("❌ No pending suggestion found for this movie.")

        from_user = pending_suggestion.get("from_username", "Unknown")

        # Remove from pending
        await remove_pending_by_movie_id(uid, mov["id"])

        await interaction.followup.send(f"❌ {interaction.user.display_name} Declined **{mov['title']} ({mov['year']})** from {from_user}!")

    # View class for handling suggestion buttons
    class SuggestionView(discord.ui.View):
        def __init__(self, user_id: str, suggestions: list):
            super().__init__(timeout=SUGGESTION_VIEW_TIMEOUT)
            self.user_id = user_id
            self.suggestions = suggestions
            self.current_index = 0
            self.message = None  # Store reference to the message for timeout handling

        async def on_timeout(self):
            """Remove buttons on timeout"""
            self.clear_items()
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.NotFound:
                    pass  # Message was deleted
                except Exception:
                    pass  # Silently fail if we can't edit

        def get_current_suggestion(self):
            if self.current_index < len(self.suggestions):
                return self.suggestions[self.current_index]
            return None
            
        def create_embed(self):
            if not self.suggestions:
                return discord.Embed(
                    title="📭 No more pending suggestions!",
                    description="You've reviewed all your suggestions.",
                    color=0x2ecc71
                )

            current = self.get_current_suggestion()
            if not current:
                # No current suggestion available - show empty state
                return discord.Embed(
                    title="📭 No more pending suggestions!",
                    description="You've reviewed all your suggestions.",
                    color=0x2ecc71
                )
                
            embed = discord.Embed(
                title="📬 Pending Movie Suggestion",
                description=f"**{current.get('from_username', 'Someone')}** suggested:",
                color=0xf39c12
            )
            
            movie = current['movie']
            embed.add_field(
                name="🎬 Movie",
                value=f"**{movie['title']} ({movie['year']})**",
                inline=False
            )
            
            embed.add_field(
                name="📊 Progress",
                value=f"Suggestion {self.current_index + 1} of {len(self.suggestions)}",
                inline=True
            )
            
            if len(self.suggestions) > 1:
                embed.add_field(
                    name="⏭️ Navigation",
                    value="Use Next/Previous to browse all suggestions",
                    inline=True
                )
            
            return embed
            
        def update_buttons(self):
            # Enable/disable navigation buttons based on position
            self.previous_button.disabled = self.current_index == 0
            self.next_button.disabled = self.current_index >= len(self.suggestions) - 1
            
            # Disable action buttons if no suggestions left
            has_suggestions = len(self.suggestions) > 0 and self.current_index < len(self.suggestions)
            self.accept_button.disabled = not has_suggestions
            self.decline_button.disabled = not has_suggestions
            
        @discord.ui.button(label='✅ Accept', style=discord.ButtonStyle.green)
        async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your suggestion panel!", ephemeral=True)

            current = self.get_current_suggestion()
            if not current:
                return await interaction.response.send_message("❌ No suggestion to accept!", ephemeral=True)

            movie = current['movie']
            from_user = current.get('from_username', 'Someone')

            # Remove from pending in database
            await remove_pending_by_movie_id(self.user_id, movie['id'])

            # Add to watchlist if not already there
            if not await is_in_watchlist(self.user_id, movie['id']):
                await add_to_watchlist(self.user_id, movie)

            # Remove from local suggestions list
            self.suggestions.remove(current)

            # Adjust current index if needed
            if self.current_index >= len(self.suggestions) and self.current_index > 0:
                self.current_index -= 1

            # Update the message
            self.update_buttons()
            embed = self.create_embed()

            if not self.suggestions:
                # No more suggestions, disable all buttons
                for item in self.children:
                    item.disabled = True

            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(f"✅ {interaction.user.display_name} accepted **{movie['title']} ({movie['year']})** suggested by {from_user} and added it to their watchlist!")
            
        @discord.ui.button(label='❌ Decline', style=discord.ButtonStyle.red)
        async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your suggestion panel!", ephemeral=True)

            current = self.get_current_suggestion()
            if not current:
                return await interaction.response.send_message("❌ No suggestion to decline!", ephemeral=True)

            movie = current['movie']
            from_user = current.get('from_username', 'Someone')

            # Remove from pending in database
            await remove_pending_by_movie_id(self.user_id, movie['id'])

            # Remove from local suggestions list
            self.suggestions.remove(current)

            # Adjust current index if needed
            if self.current_index >= len(self.suggestions) and self.current_index > 0:
                self.current_index -= 1

            # Update the message
            self.update_buttons()
            embed = self.create_embed()

            if not self.suggestions:
                # No more suggestions, disable all buttons
                for item in self.children:
                    item.disabled = True

            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(f"❌ {interaction.user.display_name} declined **{movie['title']} ({movie['year']})** suggested by {from_user}!")
            
        @discord.ui.button(label='⬅️ Previous', style=discord.ButtonStyle.grey)
        async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your suggestion panel!", ephemeral=True)
                
            if self.current_index > 0:
                self.current_index -= 1
                
            self.update_buttons()
            embed = self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)
            
        @discord.ui.button(label='➡️ Next', style=discord.ButtonStyle.grey)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != self.user_id:
                return await interaction.response.send_message("❌ This is not your suggestion panel!", ephemeral=True)
                
            if self.current_index < len(self.suggestions) - 1:
                self.current_index += 1
                
            self.update_buttons()
            embed = self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    @bot.tree.command(name="watchlist", description="View a user's watchlist")
    @app_commands.describe(user="Whose watchlist do you want to view?")
    async def watchlist_cmd(interaction: discord.Interaction, user: Optional[discord.User] = None):
        await interaction.response.defer()

        # Use the provided user, or fallback to the command invoker
        target_user = user or interaction.user
        is_self = target_user.id == interaction.user.id
        target_uid = str(target_user.id)

        # Create and load the watchlist view
        view = WatchlistView(target_uid, target_user.display_name)
        await view.load_data()

        embed = view.create_embed()

        # Send with view (filter/pagination buttons work for everyone)
        message = await interaction.followup.send(embed=embed, view=view)
        view.message = message

        # Show pending suggestions if user is viewing their own watchlist
        pending_suggestions = await get_user_pending(target_uid) if is_self else []
        if is_self and pending_suggestions:
            sugg_view = SuggestionView(str(interaction.user.id), pending_suggestions.copy())
            sugg_view.update_buttons()
            sugg_embed = sugg_view.create_embed()

            # Send as ephemeral (hidden) follow-up message with interactive buttons
            sugg_message = await interaction.followup.send(embed=sugg_embed, view=sugg_view, ephemeral=True)
            sugg_view.message = sugg_message

    @bot.tree.command(name="watched", description="Mark any movie as watched (searches TMDB)")
    @app_commands.describe(title="Search for a movie to mark as watched")
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def watched_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Use the new unified mark_as_watched function
        result = await mark_as_watched(uid, mov["id"], mov)

        if result == "already_watched":
            return await interaction.followup.send(f"⚠️ **{mov['title']} ({mov['year']})** is already marked as watched.")
        elif result == "marked":
            embed = discord.Embed(
                title="✅ Marked as Watched",
                description=f"**{mov['title']} ({mov['year']})**",
                color=0x2ecc71
            )
            if mov.get("poster_path"):
                embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w200{mov['poster_path']}")
            await interaction.followup.send(embed=embed)
        elif result == "added_and_marked":
            embed = discord.Embed(
                title="✅ Added & Marked as Watched",
                description=f"**{mov['title']} ({mov['year']})**",
                color=0x2ecc71
            )
            if mov.get("poster_path"):
                embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w200{mov['poster_path']}")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ Something went wrong. Please try again.")

    @bot.tree.command(name="unwatch", description="Mark a movie as unwatched (keeps it in watchlist)")
    @app_commands.describe(title="Select a movie from your watchlist")
    @app_commands.autocomplete(title=user_watchlist_autocomplete)
    async def unwatch_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Check if movie is in watchlist
        watchlist_movie = await get_watchlist_movie(uid, mov["id"])
        if not watchlist_movie:
            return await interaction.followup.send("❌ Movie not found in your watchlist.")

        if not watchlist_movie.get("watched_at"):
            return await interaction.followup.send("❌ Movie isn't marked as watched.")

        # Mark as unwatched (keeps in watchlist)
        await mark_as_unwatched(uid, mov["id"])
        await interaction.followup.send(f"↩️ {interaction.user.display_name} unmarked **{mov['title']} ({mov['year']})** as watched. It's still in your watchlist.")

    @bot.tree.command(name="remove", description="Remove a movie from your watchlist")
    @app_commands.describe(title="Select a movie from your watchlist")
    @app_commands.autocomplete(title=user_watchlist_autocomplete)
    async def remove_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        removed = await remove_from_watchlist(uid, mov["id"])
        if removed:
            return await interaction.followup.send(f"🗑️ {interaction.user.display_name} removed **{mov['title']} ({mov['year']})** from their watchlist.")
        else:
            await interaction.followup.send("❌ Movie not found in your watchlist.")


    @bot.tree.command(name="stats", description="View your movie watching statistics")
    async def stats_cmd(interaction: discord.Interaction):
        uid = str(interaction.user.id)
        counts = await get_watchlist_counts(uid)
        pending = await get_user_pending(uid)

        embed = discord.Embed(
            title="📊 Your Movie Stats",
            color=0xe74c3c
        )
        embed.add_field(name="🎬 Total in Watchlist", value=counts["total"], inline=True)
        embed.add_field(name="✅ Movies Watched", value=counts["watched"], inline=True)
        embed.add_field(name="❌ Still to Watch", value=counts["unwatched"], inline=True)
        embed.add_field(name="📬 Pending Suggestions", value=len(pending), inline=True)

        # Calculate completion percentage
        if counts["total"] > 0:
            pct = round(counts["watched"] / counts["total"] * 100)
            embed.add_field(name="📈 Completion", value=f"{pct}%", inline=True)

        await interaction.response.send_message(embed=embed)

    # ==================== MOVIE REVIEWS ====================

    class ReviewModal(discord.ui.Modal):
        """Modal for entering a movie review"""

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
            # Validate score
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

            # Save the review
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

    class MovieReviewView(discord.ui.View):
        """View with buttons for viewing and writing reviews"""

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
            modal = ReviewModal(self.movie_id, self.movie_title, self.movie_year)
            await interaction.response.send_modal(modal)

    @bot.tree.command(name="review_movie", description="Write a review for a movie")
    @app_commands.describe(title="Start typing a movie title to see suggestions")
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def review_movie_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer(ephemeral=True)

        movie = await search_movie_async(title)
        if not movie:
            return await interaction.followup.send("❌ Movie not found.", ephemeral=True)

        # Check if user already has a review for this movie
        reviews = await get_movie_reviews(movie["id"])
        user_review = next((r for r in reviews if r["user_id"] == str(interaction.user.id)), None)

        if user_review:
            embed = discord.Embed(
                title=f"📝 Your existing review for {movie['title']} ({movie['year']})",
                description=f"**Score:** {user_review['score']}/10\n\n{user_review['review_text']}",
                color=0xf39c12
            )
            embed.set_footer(text="Click 'Write Review' below to update your review")
        else:
            embed = discord.Embed(
                title=f"📝 Review {movie['title']} ({movie['year']})",
                description="Click the button below to write your review!",
                color=0x3498db
            )

        view = MovieReviewView(movie["id"], movie["title"], movie["year"])
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message

    @bot.tree.command(name="review_random", description="Get a random movie review for inspiration")
    async def review_random_cmd(interaction: discord.Interaction):
        await interaction.response.defer()

        result = await get_random_review()

        if not result:
            return await interaction.followup.send(
                "📭 No reviews have been written yet. Be the first to write one with `/review_movie`!"
            )

        review = result["review"]
        movie_title = review.get("movie_title", "Unknown Movie")
        movie_year = review.get("movie_year", "")

        # Format score
        score = review["score"]
        score_text = int(score) if score == int(score) else score

        embed = discord.Embed(
            title=f"🎲 {movie_title} ({movie_year})",
            description=review["review_text"],
            color=0xe91e63
        )
        embed.set_author(name=f"{review['username']} - ⭐ {score_text}/10")

        await interaction.followup.send(embed=embed)