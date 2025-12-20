# commands/watchlist.py - Updated with movie suggestions
from typing import Optional
import discord
from discord.ext import commands
from discord import app_commands
from data_store import load_data, save_data
from tmdb_client import search_movie, search_movies_autocomplete

def setup(bot):
    def get_user_entry(uid):
        data = load_data()
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

    # Autocomplete function for movie search
    async def movie_search_autocomplete(interaction: discord.Interaction, current: str):
        """Autocomplete function for movie titles"""
        if len(current) < 2:
            return []
        
        try:
            movies = await search_movies_autocomplete(current, limit=25)
            return [
                app_commands.Choice(name=movie["name"], value=movie["value"])
                for movie in movies
            ]
        except Exception as e:
            print(f"Autocomplete error: {e}")
            return []

    # Autocomplete for user's watchlist (for /remove command)
    async def user_watchlist_autocomplete(interaction: discord.Interaction, current: str):
        try:
            uid = str(interaction.user.id)
            entry, _ = get_user_entry(uid)
            
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
            
            return matching_movies[:25]
            
        except Exception as e:
            print(f"DEBUG: Fatal error in watchlist autocomplete: {e}")
            import traceback
            traceback.print_exc()
            return []

    # Autocomplete for user's watched movies
    async def user_watched_autocomplete(interaction: discord.Interaction, current: str):
        uid = str(interaction.user.id)
        entry, _ = get_user_entry(uid)
        matching_movies = [
            app_commands.Choice(name=f"{movie['title']} ({movie['year']})", value=movie['title'])
            for movie in entry["watched"]
            if current.lower() in movie['title'].lower()
        ]
        return matching_movies[:25]

    # Autocomplete for user's pending suggestions
    async def user_pending_autocomplete(interaction: discord.Interaction, current: str):
        uid = str(interaction.user.id)
        entry, _ = get_user_entry(uid)
        matching_movies = [
            app_commands.Choice(
                name=f"{suggestion['movie']['title']} ({suggestion['movie']['year']}) - from {suggestion['from_user']}", 
                value=suggestion['movie']['title']
            )
            for suggestion in entry["pending"]
            if current.lower() in suggestion['movie']['title'].lower()
        ]
        return matching_movies[:25]

    @bot.tree.command(name="add", description="Add a movie to your watchlist")
    @app_commands.describe(title="Start typing a movie title to see suggestions")
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def add_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = get_user_entry(uid)
        mov = search_movie(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        if find_movie_by_id(entry["watchlist"], mov["id"]):
            return await interaction.followup.send("⚠️ Already in your watchlist.")

        entry["watchlist"].append(mov)
        save_data(data)
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
        target_entry, data = get_user_entry(target_uid)
        mov = search_movie(title)
        
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
        save_data(data)
        
        # Send confirmation to suggester
        await interaction.followup.send(f"📬 Suggested **{mov['title']} ({mov['year']})** to {user.display_name}!")
        
        # Send DM to target user about pending suggestion
        # try:
        #     pending_count = len(target_entry["pending"])
        #     dm_embed = discord.Embed(
        #         title="🎬 New Movie Suggestion!",
        #         description=f"**{interaction.user.display_name}** suggested **{mov['title']} ({mov['year']})** for your watchlist!",
        #         color=0xf39c12
        #     )
        #     dm_embed.add_field(
        #         name="📋 Pending Suggestions", 
        #         value=f"You have {pending_count} pending suggestion{'s' if pending_count != 1 else ''}", 
        #         inline=False
        #     )
        #     dm_embed.add_field(
        #         name="💡 How to manage", 
        #         value="Use `/pending` to view all suggestions\nUse `/approve` or `/decline` to manage them", 
        #         inline=False
        #     )
            
        #     await user.send(embed=dm_embed)
        # except discord.Forbidden:
        #     # User has DMs disabled, that's okay
        #     pass

    @bot.tree.command(name="pending", description="View your pending movie suggestions")
    async def pending_cmd(interaction: discord.Interaction):
        entry, _ = get_user_entry(str(interaction.user.id))
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
        entry, data = get_user_entry(uid)
        mov = search_movie(title)

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
            save_data(data)
            return await interaction.followup.send(f"⚠️ **{mov['title']} ({mov['year']})** is already in your watchlist. Removed from pending.")

        # Remove from pending and add to watchlist
        entry["pending"].remove(suggestion_to_remove)
        entry["watchlist"].append(mov)
        save_data(data)

        from_user = suggestion_to_remove["from_user"]
        await interaction.followup.send(f"✅ {interaction.user.display_name} approved **{mov['title']} ({mov['year']})** from {from_user} and added it to their watchlist!")

    @bot.tree.command(name="decline", description="Decline a pending movie suggestion")
    @app_commands.describe(title="Select a movie from your pending suggestions")
    @app_commands.autocomplete(title=user_pending_autocomplete)
    async def decline_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = get_user_entry(uid)
        mov = search_movie(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Find the suggestion in pending list by ID
        suggestion_to_remove = find_pending_by_id(entry["pending"], mov["id"])

        if not suggestion_to_remove:
            return await interaction.followup.send("❌ No pending suggestion found for this movie.")

        # Remove from pending
        entry["pending"].remove(suggestion_to_remove)
        save_data(data)

        from_user = suggestion_to_remove["from_user"]
        await interaction.followup.send(f"❌ {interaction.user.display_name} Declined **{mov['title']} ({mov['year']})** from {from_user}!")

    # View class for handling suggestion buttons
    class SuggestionView(discord.ui.View):
        def __init__(self, user_id: str, suggestions: list):
            super().__init__(timeout=300)  # 5 minute timeout
            self.user_id = user_id
            self.suggestions = suggestions
            self.current_index = 0
            
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
                return self.create_embed()  # Fallback
                
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
            entry, data = get_user_entry(self.user_id)
            movie = current['movie']
            from_user = current['from_user']

            # Find and remove from pending by ID
            pending_to_remove = find_pending_by_id(entry['pending'], movie['id'])
            if pending_to_remove:
                entry['pending'].remove(pending_to_remove)

            # Check for duplicates before adding to watchlist
            if not find_movie_by_id(entry['watchlist'], movie['id']):
                entry['watchlist'].append(movie)

            save_data(data)

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
            entry, data = get_user_entry(self.user_id)
            movie = current['movie']
            from_user = current['from_user']

            # Find and remove from pending by ID
            pending_to_remove = find_pending_by_id(entry['pending'], movie['id'])
            if pending_to_remove:
                entry['pending'].remove(pending_to_remove)
                save_data(data)

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

        entry, _ = get_user_entry(str(target_user.id))
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
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @bot.tree.command(name="watchedlist", description="View your watched movies")
    @app_commands.describe(page="Page number to view (default: 1)")
    async def watchedlist_cmd(interaction: discord.Interaction, page: int = 1):
        entry, _ = get_user_entry(str(interaction.user.id))
        movies = entry["watched"]
        
        if not movies:
            return await interaction.response.send_message(f"📭 {interaction.user.display_name} has no movies watched yet.")
        
        per_page = 5
        total_pages = (len(movies) + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        
        start = (page - 1) * per_page
        chunk = movies[start:start + per_page]
        
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
        entry, data = get_user_entry(uid)
        mov = search_movie(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        if find_movie_by_id(entry["watched"], mov["id"]):
            return await interaction.followup.send("⚠️ Already marked as watched.")

        entry["watched"].append(mov)

        # Remove from watchlist by ID
        existing_in_watchlist = find_movie_by_id(entry["watchlist"], mov["id"])
        if existing_in_watchlist:
            entry["watchlist"].remove(existing_in_watchlist)

        save_data(data)
        await interaction.followup.send(f"🎉 {interaction.user.display_name} marked **{mov['title']} ({mov['year']})** as watched!")

    @bot.tree.command(name="unwatch", description="Remove a movie from watched list")
    @app_commands.describe(title="Select a movie from your watched list")
    @app_commands.autocomplete(title=user_watched_autocomplete)
    async def unwatch_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = get_user_entry(uid)
        mov = search_movie(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        existing_in_watched = find_movie_by_id(entry["watched"], mov["id"])
        if existing_in_watched:
            entry["watched"].remove(existing_in_watched)
            save_data(data)
            return await interaction.followup.send(f"↩️ {interaction.user.display_name} unmarked **{mov['title']} ({mov['year']})** as watched.")

        await interaction.followup.send("❌ Movie wasn't marked as watched.")

    @bot.tree.command(name="remove", description="Remove a movie from your watchlist")
    @app_commands.describe(title="Select a movie from your watchlist")
    @app_commands.autocomplete(title=user_watchlist_autocomplete)
    async def remove_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        uid = str(interaction.user.id)
        entry, data = get_user_entry(uid)

        mov = search_movie(title)

        if not mov:
            return await interaction.followup.send("❌ Movie not found.")

        # Find movie by ID
        movie_to_remove = find_movie_by_id(entry["watchlist"], mov["id"])

        if movie_to_remove:
            entry["watchlist"].remove(movie_to_remove)
            save_data(data)
            return await interaction.followup.send(f"🗑️ {interaction.user.display_name} removed **{movie_to_remove['title']} ({movie_to_remove['year']})** from their watchlist.")
        else:
            await interaction.followup.send("❌ Movie not found in your watchlist.")


    @bot.tree.command(name="stats", description="View your movie watching statistics")
    async def stats_cmd(interaction: discord.Interaction):
        entry, _ = get_user_entry(str(interaction.user.id))
        
        embed = discord.Embed(
            title="📊 Your Movie Stats",
            color=0xe74c3c
        )
        embed.add_field(name="🎬 Movies in Watchlist", value=len(entry["watchlist"]), inline=True)
        embed.add_field(name="✅ Movies Watched", value=len(entry["watched"]), inline=True)
        embed.add_field(name="📬 Pending Suggestions", value=len(entry["pending"]), inline=True)
        embed.add_field(name="📈 Total Movies", value=len(entry["watchlist"]) + len(entry["watched"]), inline=True)
        
        await interaction.response.send_message(embed=embed)