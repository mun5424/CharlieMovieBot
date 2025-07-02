# commands/watchlist.py - Updated with autocomplete
import discord
from discord.ext import commands
from discord import app_commands
from data_store import load_data, save_data
from tmdb_client import search_movie, search_movies_autocomplete

def setup(bot):
    def get_user_entry(uid):
        data = load_data()
        return data.setdefault(str(uid), {"watchlist": [], "watched": []}), data

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
        uid = str(interaction.user.id)
        entry, _ = get_user_entry(uid)
        matching_movies = [
            app_commands.Choice(name=f"{movie['title']} ({movie['year']})", value=movie['title'])
            for movie in entry["watchlist"]
            if current.lower() in movie['title'].lower()
        ]
        return matching_movies[:25]

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
        
        if mov in entry["watchlist"]:
            return await interaction.followup.send("⚠️ Already in your watchlist.")
        
        entry["watchlist"].append(mov)
        save_data(data)
        await interaction.followup.send(f"✅ Added **{mov['title']} ({mov['year']})** to your watchlist.")

    @bot.tree.command(name="watchlist", description="View your watchlist")
    async def watchlist_cmd(interaction: discord.Interaction):
        entry, _ = get_user_entry(str(interaction.user.id))
        movies = entry["watchlist"]
        
        if not movies:
            return await interaction.response.send_message("📭 Your watchlist is empty.")
        
        embed = discord.Embed(
            title=f"🎬 {interaction.user.display_name}'s Watchlist",
            description=f"Total movies: {len(movies)}",
            color=0x3498db
        )
        
        # Create a simple list of movie titles
        movie_list = []
        for i, movie in enumerate(movies, 1):
            movie_list.append(f"{i}. {movie['title']} ({movie['year']})")
        
        # Join all movies into one field
        embed.add_field(
            name="\u200b",  # Invisible character for empty field name
            value="\n".join(movie_list),
            inline=False
        )
        
        await interaction.response.send_message(embed=embed)

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
        
        # Create a simple list of movie titles
        movie_list = []
        for i, movie in enumerate(movies, 1):
            movie_list.append(f"{i}. {movie['title']} ({movie['year']})")
        
        # Join all movies into one field
        embed.add_field(
            name="\u200b",  # Invisible character for empty field name
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
        
        if mov in entry["watched"]:
            return await interaction.followup.send("⚠️ Already marked as watched.")
        
        entry["watched"].append(mov)
        if mov in entry["watchlist"]:
            entry["watchlist"].remove(mov)
        
        save_data(data)
        await interaction.followup.send(f"🎉 Marked **{mov['title']} ({mov['year']})** as watched!")

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
        
        if mov in entry["watched"]:
            entry["watched"].remove(mov)
            save_data(data)
            return await interaction.followup.send(f"↩️ {interaction.user.display_name} Unmarked **{mov['title']} ({mov['year']})** as watched.")
        
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
        
        if mov in entry["watchlist"]:
            entry["watchlist"].remove(mov)
            save_data(data)
            return await interaction.followup.send(f"🗑️ {interaction.user.display_name} removed **{mov['title']} ({mov['year']})** from their watchlist.")
        
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
        embed.add_field(name="📈 Total Movies", value=len(entry["watchlist"]) + len(entry["watched"]), inline=True)
        
        await interaction.response.send_message(embed=embed)