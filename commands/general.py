import discord
import logging
from discord.ext import commands
from discord import app_commands
from tmdb_client import search_movie, get_movie_details, search_movies_autocomplete


logger = logging.getLogger(__name__)

def setup(bot):
    print("🔍 Setting up general commands...")
    
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

    # ALL COMMANDS MUST BE INSIDE setup(bot) FUNCTION
    @bot.tree.command(name="search", description="Search for a movie")
    @app_commands.describe(title="Start typing a movie title to see suggestions")
    @app_commands.autocomplete(title=movie_search_autocomplete)
    async def search_cmd(interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        movie = search_movie(title)
        if movie:
            # Get detailed info for genres, runtime, etc.
            detailed_movie = movie
            if movie.get('id'):
                detailed_info = get_movie_details(movie['id'])
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
            if detailed_movie.get('vote_average') and detailed_movie['vote_average'] > 0:
                rating = detailed_movie['vote_average']
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
            
            # Add TMDB disclaimer at the bottom, You dont need this if youre using a private discord
            # embed.set_footer(text="This product uses the TMDB API but is not endorsed or certified by TMDB.")
            
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ Movie not found. Try a different search term.")

            
    @bot.tree.command(name="reminder_tournament", description="Run the daily tournament reminder check manually")
    async def reminder_tournament(interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)  # Acknowledge immediately

        try:
            from tourney_reminder import check_todays_tournament

            await check_todays_tournament(manual=True)

            await interaction.followup.send("✅ Manual tournament reminder check completed.", ephemeral=True)

        except ImportError as e:
            error_msg = f"❌ Import error: {e}"
            print(error_msg)
            await interaction.followup.send(error_msg, ephemeral=True)
            
        except discord.errors.NotFound as e:
            error_msg = f"❌ Discord error (channel not found?): {e}"
            print(error_msg)
            await interaction.followup.send(error_msg, ephemeral=True)
            
        except discord.errors.Forbidden as e:
            error_msg = f"❌ Discord permission error: {e}"
            print(error_msg)
            await interaction.followup.send(error_msg, ephemeral=True)
            
        except Exception as e:
            error_msg = f"❌ Unexpected error: {str(e)}"
            print(f"Error in /reminder_tournament: {e}")
            logger.error(f"Discord command error: {e}")
            try:
                await interaction.followup.send(error_msg, ephemeral=True)
            except:
                # If followup fails, the interaction might be dead
                print("Failed to send error message to Discord")