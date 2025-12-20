# commands/watchlist.py - Updated with movie suggestions and reviews
import logging
import time
import random
from typing import Optional, List, Dict
import discord
from discord.ext import commands
from discord import app_commands
from data_store import load_data_async, save_data_async
from tmdb_client import search_movie_async
from commands.autocomplete import movie_search_autocomplete, AUTOCOMPLETE_LIMIT

logger = logging.getLogger(__name__)

# Constants
SUGGESTION_VIEW_TIMEOUT = 300  # 5 minutes
WATCHED_LIST_PAGE_SIZE = 5
REVIEW_VIEW_TIMEOUT = 300  # 5 minutes


# Helper functions for reviews (module level so they can be imported)
async def get_reviews_data():
    """Get the reviews section from data store"""
    data = await load_data_async()
    if "reviews" not in data:
        data["reviews"] = {}
    return data["reviews"], data


async def get_movie_reviews(movie_id: int) -> List[Dict]:
    """Get all reviews for a specific movie"""
    reviews, _ = await get_reviews_data()
    return reviews.get(str(movie_id), [])


async def add_movie_review(movie_id: int, movie_title: str, movie_year: str,
                           user_id: str, username: str, score: int, review_text: str):
    """Add a review for a movie"""
    reviews, data = await get_reviews_data()
    movie_key = str(movie_id)

    if movie_key not in reviews:
        reviews[movie_key] = []

    # Check if user already reviewed this movie
    for i, review in enumerate(reviews[movie_key]):
        if review["user_id"] == user_id:
            # Update existing review
            reviews[movie_key][i] = {
                "user_id": user_id,
                "username": username,
                "score": score,
                "review_text": review_text,
                "movie_title": movie_title,
                "movie_year": movie_year,
                "timestamp": time.time()
            }
            await save_data_async(data)
            return "updated"

    # Add new review
    reviews[movie_key].append({
        "user_id": user_id,
        "username": username,
        "score": score,
        "review_text": review_text,
        "movie_title": movie_title,
        "movie_year": movie_year,
        "timestamp": time.time()
    })
    await save_data_async(data)
    return "added"


def format_reviewers_text(reviews: List[Dict]) -> str:
    """Format the reviewer names for display"""
    if not reviews:
        return ""

    usernames = [r["username"] for r in reviews]

    if len(usernames) == 1:
        return f"**{usernames[0]}** has reviewed and rated this movie"
    elif len(usernames) == 2:
        return f"**{usernames[0]}** and **{usernames[1]}** have reviewed and rated this movie"
    else:
        # 3 or more: "User1, User2, and User3 have reviewed..."
        all_but_last = ", ".join(f"**{name}**" for name in usernames[:-1])
        return f"{all_but_last}, and **{usernames[-1]}** have reviewed and rated this movie"


async def get_random_review() -> Optional[Dict]:
    """Get a random review from all movies"""
    reviews, _ = await get_reviews_data()

    if not reviews:
        return None

    # Get all movies that have reviews
    movies_with_reviews = [(movie_id, movie_reviews) for movie_id, movie_reviews in reviews.items() if movie_reviews]

    if not movies_with_reviews:
        return None

    # Pick a random movie
    movie_id, movie_reviews = random.choice(movies_with_reviews)

    # Pick a random review from that movie
    review = random.choice(movie_reviews)

    return {
        "movie_id": movie_id,
        "review": review
    }


def setup(bot):
    async def get_user_entry(uid):
        data = await load_data_async()
        user_data = data.setdefault(str(uid), {"watchlist": [], "watched": [], "pending": []})
        # Ensure pending key exists for existing users
        if "pending" not in user_data:
            user_data["pending"] = []
        return user_data, data

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
            entry, _ = await get_user_entry(uid)
            
            matching_movies = []
            for movie in entry["watchlist"]:
                try:
                    # Safety checks
                    if not isinstance(movie, dict):
                        continue
                    
                    if 'title' not in movie:
                        continue
                    
                    title = str(movie['title'])
                    year = str(movie.get('year', 'Unknown'))
                    
                    # Check if current input matches (allow empty current for showing all)
                    if not current or current.lower() in title.lower():
                        choice_name = f"{title} ({year})"
                        choice_value = title
                        matching_movies.append(app_commands.Choice(name=choice_name, value=choice_value))
                        
                except Exception as movie_error:
                    continue
            
            return matching_movies[:AUTOCOMPLETE_LIMIT]

        except Exception as e:
            logger.error(f"Fatal error in watchlist autocomplete: {e}", exc_info=True)
            return []

    # Autocomplete for user's watched movies
    async def user_watched_autocomplete(interaction: discord.Interaction, current: str):
        try:
            uid = str(interaction.user.id)
            entry, _ = await get_user_entry(uid)

            matching_movies = []
            for movie in entry["watched"]:
                try:
                    if not isinstance(movie, dict) or 'title' not in movie:
                        continue

                    title = str(movie['title'])
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
            entry, _ = await get_user_entry(uid)

            matching_movies = []
            for suggestion in entry["pending"]:
                try:
                    movie = suggestion.get("movie", {})
                    if not isinstance(movie, dict) or 'title' not in movie:
                        continue

                    title = str(movie['title'])
                    year = str(movie.get('year', 'Unknown'))
                    from_user = suggestion.get('from_user', 'Unknown')

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

    @bot.tree.command(name="add", description="Add a movie to your watchlist")
    @app_commands.describe(title="Start typing a movie title to see suggestions")
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def add_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = await get_user_entry(uid)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        if find_movie_by_id(entry["watchlist"], mov["id"]):
            return await interaction.followup.send("⚠️ Already in your watchlist.")

        entry["watchlist"].append(mov)
        await save_data_async(data)
        await interaction.followup.send(f"✅ {interaction.user.display_name} added **{mov['title']} ({mov['year']})** to their watchlist.")

    @bot.tree.command(name="suggest", description="Suggest a movie to another user's watchlist")
    @app_commands.describe(
        user="The user to suggest the movie to",
        title="Start typing a movie title to see suggestions"
    )
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def suggest_cmd(interaction: discord.Interaction, user: discord.Member, title: str):
        await interaction.response.defer()
        
        # disable for testing 
        # if user.id == interaction.user.id:
        #     return await interaction.followup.send("❌ You can't suggest movies to yourself! Use `/add` instead.")
        
        target_uid = str(user.id)
        target_entry, data = await get_user_entry(target_uid)
        mov = await search_movie_async(title)
        
        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Check if movie is already in their watchlist or watched
        if find_movie_by_id(target_entry["watchlist"], mov["id"]):
            return await interaction.followup.send(f"⚠️ **{mov['title']} ({mov['year']})** is already in {user.display_name}'s watchlist.")

        if find_movie_by_id(target_entry["watched"], mov["id"]):
            return await interaction.followup.send(f"⚠️ {user.display_name} has already watched **{mov['title']} ({mov['year']})**.")

        # Check if suggestion already exists
        if find_pending_by_id(target_entry["pending"], mov["id"]):
            return await interaction.followup.send(f"⚠️ **{mov['title']} ({mov['year']})** has already been suggested to {user.display_name}.")
        
        # Add suggestion to pending list
        suggestion = {
            "movie": mov,
            "from_user": interaction.user.display_name,
            "from_id": str(interaction.user.id)
        }
        target_entry["pending"].append(suggestion)
        await save_data_async(data)
        
        # Send confirmation to suggester
        await interaction.followup.send(f"📬 Suggested **{mov['title']} ({mov['year']})** to {user.display_name}!")

    @bot.tree.command(name="pending", description="View your pending movie suggestions")
    async def pending_cmd(interaction: discord.Interaction):
        entry, _ = await get_user_entry(str(interaction.user.id))
        suggestions = entry["pending"]
        
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
            from_user = suggestion["from_user"]
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
        entry, data = await get_user_entry(uid)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Find the suggestion in pending list by ID
        suggestion_to_remove = find_pending_by_id(entry["pending"], mov["id"])

        if not suggestion_to_remove:
            return await interaction.followup.send("❌ No pending suggestion found for this movie.")

        # Check if already in watchlist
        if find_movie_by_id(entry["watchlist"], mov["id"]):
            # Remove from pending but don't add duplicate
            entry["pending"].remove(suggestion_to_remove)
            await save_data_async(data)
            return await interaction.followup.send(f"⚠️ **{mov['title']} ({mov['year']})** is already in your watchlist. Removed from pending.")

        # Remove from pending and add to watchlist
        entry["pending"].remove(suggestion_to_remove)
        entry["watchlist"].append(mov)
        await save_data_async(data)

        from_user = suggestion_to_remove["from_user"]
        await interaction.followup.send(f"✅ {interaction.user.display_name} approved **{mov['title']} ({mov['year']})** from {from_user} and added it to their watchlist!")

    @bot.tree.command(name="decline", description="Decline a pending movie suggestion")
    @app_commands.describe(title="Select a movie from your pending suggestions")
    @app_commands.autocomplete(title=user_pending_autocomplete)
    async def decline_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = await get_user_entry(uid)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Find the suggestion in pending list by ID
        suggestion_to_remove = find_pending_by_id(entry["pending"], mov["id"])

        if not suggestion_to_remove:
            return await interaction.followup.send("❌ No pending suggestion found for this movie.")

        # Remove from pending
        entry["pending"].remove(suggestion_to_remove)
        await save_data_async(data)

        from_user = suggestion_to_remove["from_user"]
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
            """Called when the view times out - disable all buttons"""
            for item in self.children:
                item.disabled = True

            if self.message:
                try:
                    embed = self.message.embeds[0] if self.message.embeds else None
                    if embed:
                        embed.set_footer(text="⏰ This suggestion panel has expired. Use /pending to view suggestions again.")
                    await self.message.edit(embed=embed, view=self)
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
                description=f"**{current['from_user']}** suggested:",
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

            # Get user data and add to watchlist
            entry, data = await get_user_entry(self.user_id)
            movie = current['movie']
            from_user = current['from_user']

            # Find and remove from pending by ID
            pending_to_remove = find_pending_by_id(entry['pending'], movie['id'])
            if pending_to_remove:
                entry['pending'].remove(pending_to_remove)

            # Check for duplicates before adding to watchlist
            if not find_movie_by_id(entry['watchlist'], movie['id']):
                entry['watchlist'].append(movie)

            await save_data_async(data)

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

            # Get user data and remove from pending
            entry, data = await get_user_entry(self.user_id)
            movie = current['movie']
            from_user = current['from_user']

            # Find and remove from pending by ID
            pending_to_remove = find_pending_by_id(entry['pending'], movie['id'])
            if pending_to_remove:
                entry['pending'].remove(pending_to_remove)
                await save_data_async(data)

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
        # Use the provided user, or fallback to the command invoker
        target_user = user or interaction.user
        is_self = target_user.id == interaction.user.id

        entry, _ = await get_user_entry(str(target_user.id))
        movies = entry["watchlist"]
        pending_suggestions = entry["pending"]

        if not movies:
            message = "📭 Your watchlist is empty." if is_self else f"📭 {target_user.display_name}'s watchlist is empty."
            await interaction.response.send_message(message)
            return

        embed = discord.Embed(
            title=f"🎬 {target_user.display_name}'s Watchlist",
            color=0x3498db
        )

        movie_list = [f"{i}. {movie['title']} ({movie['year']})" for i, movie in enumerate(movies, 1)]
        embed.add_field(name="\u200b", value="\n".join(movie_list), inline=False)

        await interaction.response.send_message(embed=embed)

        # Only show pending suggestions if user is viewing their own watchlist
        if is_self and pending_suggestions:
            view = SuggestionView(str(interaction.user.id), pending_suggestions.copy())
            view.update_buttons()
            embed = view.create_embed()

            # Send as ephemeral (hidden) follow-up message with interactive buttons
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            view.message = message  # Store reference for timeout handling

    @bot.tree.command(name="watchedlist", description="View your watched movies")
    @app_commands.describe(page="Page number to view (default: 1)")
    async def watchedlist_cmd(interaction: discord.Interaction, page: int = 1):
        entry, _ = await get_user_entry(str(interaction.user.id))
        movies = entry["watched"]
        
        if not movies:
            return await interaction.response.send_message(f"📭 {interaction.user.display_name} has no movies watched yet.")

        total_pages = (len(movies) + WATCHED_LIST_PAGE_SIZE - 1) // WATCHED_LIST_PAGE_SIZE
        page = max(1, min(page, total_pages))

        start = (page - 1) * WATCHED_LIST_PAGE_SIZE
        chunk = movies[start:start + WATCHED_LIST_PAGE_SIZE]
        
        embed = discord.Embed(
            title=f"✅ {interaction.user.display_name}'s Watched Movies (Page {page}/{total_pages})",
            description=f"Total watched: {len(movies)}",
            color=0x2ecc71
        )
        
        movie_list = []
        for i, movie in enumerate(chunk, start + 1):
            movie_list.append(f"{i}. {movie['title']} ({movie['year']})")
        
        embed.add_field(
            name="\u200b",
            value="\n".join(movie_list),
            inline=False
        )
        
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="watched", description="Mark a movie as watched")
    @app_commands.describe(title="Select from your watchlist or search for a movie")
    @app_commands.autocomplete(title=user_watchlist_autocomplete)
    async def watched_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = await get_user_entry(uid)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        if find_movie_by_id(entry["watched"], mov["id"]):
            return await interaction.followup.send("⚠️ Already marked as watched.")

        entry["watched"].append(mov)

        # Remove from watchlist by ID
        existing_in_watchlist = find_movie_by_id(entry["watchlist"], mov["id"])
        if existing_in_watchlist:
            entry["watchlist"].remove(existing_in_watchlist)

        await save_data_async(data)
        await interaction.followup.send(f"🎉 {interaction.user.display_name} marked **{mov['title']} ({mov['year']})** as watched!")

    @bot.tree.command(name="unwatch", description="Remove a movie from watched list")
    @app_commands.describe(title="Select a movie from your watched list")
    @app_commands.autocomplete(title=user_watched_autocomplete)
    async def unwatch_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = await get_user_entry(uid)
        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        existing_in_watched = find_movie_by_id(entry["watched"], mov["id"])
        if existing_in_watched:
            entry["watched"].remove(existing_in_watched)
            await save_data_async(data)
            return await interaction.followup.send(f"↩️ {interaction.user.display_name} unmarked **{mov['title']} ({mov['year']})** as watched.")

        await interaction.followup.send("❌ Movie wasn't marked as watched.")

    @bot.tree.command(name="remove", description="Remove a movie from your watchlist")
    @app_commands.describe(title="Select a movie from your watchlist")
    @app_commands.autocomplete(title=user_watchlist_autocomplete)
    async def remove_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = await get_user_entry(uid)

        mov = await search_movie_async(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Find movie by ID
        movie_to_remove = find_movie_by_id(entry["watchlist"], mov["id"])

        if movie_to_remove:
            entry["watchlist"].remove(movie_to_remove)
            await save_data_async(data)
            return await interaction.followup.send(f"🗑️ {interaction.user.display_name} removed **{movie_to_remove['title']} ({movie_to_remove['year']})** from their watchlist.")
        else:
            await interaction.followup.send("❌ Movie not found in your watchlist.")


    @bot.tree.command(name="stats", description="View your movie watching statistics")
    async def stats_cmd(interaction: discord.Interaction):
        entry, _ = await get_user_entry(str(interaction.user.id))

        embed = discord.Embed(
            title="📊 Your Movie Stats",
            color=0xe74c3c
        )
        embed.add_field(name="🎬 Movies in Watchlist", value=len(entry["watchlist"]), inline=True)
        embed.add_field(name="✅ Movies Watched", value=len(entry["watched"]), inline=True)
        embed.add_field(name="📬 Pending Suggestions", value=len(entry["pending"]), inline=True)
        embed.add_field(name="📈 Total Movies", value=len(entry["watchlist"]) + len(entry["watched"]), inline=True)

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

            if result == "updated":
                await interaction.response.send_message(
                    f"✅ Updated your review for **{self.movie_title} ({self.movie_year})** - {score_display}/10",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"✅ Review submitted for **{self.movie_title} ({self.movie_year})** - {score_display}/10"
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
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
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

            # Use pagination view
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
            title=f"🎲 Random Review: {movie_title} ({movie_year})",
            description=f"*Maybe you should watch this one?*",
            color=0xe91e63
        )

        embed.add_field(
            name=f"**{review['username']}** gave it ⭐ {score_text}/10",
            value=review["review_text"],
            inline=False
        )

        embed.set_footer(text="Use /review_movie to write your own review!")

        await interaction.followup.send(embed=embed)