# commands/general.py - Updated with better error handling
import discord
from discord.ext import commands
from discord import app_commands
from tmdb_client import search_movie, get_movie_details

def setup(bot):
    @bot.tree.command(name="search", description="Search for a movie")
    @app_commands.describe(title="The movie title to search for")
    async def search_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()
        
        movie = search_movie(title)
        if movie:
            embed = discord.Embed(
                title=f"🔎 {movie.get('title', 'Unknown Title')}",
                color=0x2ecc71
            )
            
            # Add year if available
            if movie.get('year') and movie['year'] != 'Unknown':
                embed.title += f" ({movie['year']})"
            
            # Add overview if available
            overview = movie.get('overview', '')
            if overview and overview != 'No description available':
                if len(overview) > 300:
                    overview = overview[:300] + "..."
                embed.add_field(name="📖 Overview", value=overview, inline=False)
            
            # Add rating if available
            rating = movie.get('rating', 0)
            if rating and rating > 0:
                embed.add_field(name="⭐ Rating", value=f"{rating}/10", inline=True)
            
            # Add poster if available
            poster_path = movie.get('poster_path')
            if poster_path:
                embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w300{poster_path}")
            
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ Movie not found. Try a different search term.")

    @bot.tree.command(name="info", description="Get detailed information about a movie")
    @app_commands.describe(title="The movie title to get info for")
    async def info_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()
        
        # First search for the movie
        movie = search_movie(title)
        if not movie:
            return await interaction.followup.send("❌ Movie not found.")
        
        # Get detailed info if we have an ID
        if movie.get('id'):
            detailed_movie = get_movie_details(movie['id'])
            if detailed_movie:
                movie = detailed_movie
        
        embed = discord.Embed(
            title=f"🎬 {movie.get('title', 'Unknown Title')}",
            color=0x9b59b6
        )
        
        # Add year
        if movie.get('year') and movie['year'] != 'Unknown':
            embed.title += f" ({movie['year']})"
        
        # Add overview
        overview = movie.get('overview', '')
        if overview and overview != 'No description available':
            if len(overview) > 500:
                overview = overview[:500] + "..."
            embed.add_field(name="📖 Overview", value=overview, inline=False)
        
        # Add details in a more compact format
        details = []
        if movie.get('rating') and movie['rating'] > 0:
            details.append(f"⭐ **Rating:** {movie['rating']}/10")
        if movie.get('director') and movie['director'] != 'Unknown':
            details.append(f"🎬 **Director:** {movie['director']}")
        if movie.get('genre') and movie['genre'] != 'Unknown':
            details.append(f"🎭 **Genre:** {movie['genre']}")
        if movie.get('runtime') and movie['runtime'] > 0:
            details.append(f"⏱️ **Runtime:** {movie['runtime']} minutes")
        
        if details:
            embed.add_field(name="Details", value="\n".join(details), inline=False)
        
        # Add poster
        poster_path = movie.get('poster_path')
        if poster_path:
            embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w300{poster_path}")
        
        await interaction.followup.send(embed=embed)