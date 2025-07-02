import discord
from discord.ext import commands
from discord import app_commands
from data_store import load_data, save_data
from tmdb_client import search_movie

def setup(bot):
    def get_user_entry(uid):
        data = load_data()
        return data.setdefault(str(uid), {"watchlist": [], "watched": []}), data

    # Autocomplete function for user's movies
    async def user_watchlist_autocomplete(interaction: discord.Interaction, current: str):
        uid = str(interaction.user.id)
        entry, _ = get_user_entry(uid)
        matching_movies = [
            app_commands.Choice(name=f"{movie['title']} ({movie['year']})", value=movie['title'])
            for movie in entry["watchlist"]
            if current.lower() in movie['title'].lower()
        ]
        return matching_movies[:25]

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
    @app_commands.describe(title="The movie title to add to your watchlist")
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
    @app_commands.describe(page="Page number to view (default: 1)")
    async def watchlist_cmd(interaction: discord.Interaction, page: int = 1):
        entry, _ = get_user_entry(str(interaction.user.id))
        movies = entry["watchlist"]
        
        if not movies:
            return await interaction.response.send_message("📭 Your watchlist is empty.")
        
        per_page = 5
        total_pages = (len(movies) + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        
        start = (page - 1) * per_page
        chunk = movies[start:start + per_page]
        
        embed = discord.Embed(
            title=f"🎬 Your Watchlist (Page {page}/{total_pages})",
            description=f"Total movies: {len(movies)}",
            color=0x3498db
        )
        
        for i, movie in enumerate(chunk):
            embed.add_field(
                name=f"{i+1+start}. {movie['title']} ({movie['year']})",
                value=movie.get('overview', 'No description available')[:100] + "..." if movie.get('overview', '') else "No description available",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="watchedlist", description="View your watched movies")
    @app_commands.describe(page="Page number to view (default: 1)")
    async def watchedlist_cmd(interaction: discord.Interaction, page: int = 1):
        entry, _ = get_user_entry(str(interaction.user.id))
        movies = entry["watched"]
        
        if not movies:
            return await interaction.response.send_message("📭 No movies watched yet.")
        
        per_page = 5
        total_pages = (len(movies) + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        
        start = (page - 1) * per_page
        chunk = movies[start:start + per_page]
        
        embed = discord.Embed(
            title=f"✅ Watched Movies (Page {page}/{total_pages})",
            description=f"Total watched: {len(movies)}",
            color=0x2ecc71
        )
        
        for i, movie in enumerate(chunk):
            embed.add_field(
                name=f"{i+1+start}. {movie['title']} ({movie['year']})",
                value=movie.get('overview', 'No description available')[:100] + "..." if movie.get('overview', '') else "No description available",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="watched", description="Mark a movie as watched")
    @app_commands.describe(title="The movie title to mark as watched")
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
    @app_commands.describe(title="The movie title to unmark as watched")
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
            return await interaction.followup.send(f"↩️ Unmarked **{mov['title']} ({mov['year']})** as watched.")
        
        await interaction.followup.send("❌ Movie wasn't marked as watched.")

    @bot.tree.command(name="remove", description="Remove a movie from your watchlist")
    @app_commands.describe(title="The movie title to remove from watchlist")
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
            return await interaction.followup.send(f"🗑️ Removed **{mov['title']} ({mov['year']})** from your watchlist.")
        
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