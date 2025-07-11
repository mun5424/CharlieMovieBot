import discord
from discord.ext import commands
from discord import app_commands
import config
import logging
import asyncio

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", help_command=None, intents=intents)

async def load_commands():
    """Load all command modules"""
    try:
        from commands import general, watchlist
        from trivia.trivia import TriviaCog

        general.setup(bot)
        watchlist.setup(bot)
        await bot.add_cog(TriviaCog(bot))

        logger.info("✅ Commands loaded successfully")
        logger.info(f"Registered commands: {[cmd.name for cmd in bot.tree.get_commands()]}")
        return True
    except Exception as e:
        logger.error(f"❌ Error loading commands: {e}")
        import traceback
        traceback.print_exc()
        return False


async def sync_commands():
    """Sync slash commands to Discord"""
    try:
        if hasattr(config, 'GUILD_IDS_TEST') and config.GUILD_IDS_TEST:
            # Guild-specific sync (faster for testing)
            for guild_id in config.GUILD_IDS_TEST:
                guild = discord.Object(id=guild_id)
                
                # Copy global commands to guild and sync
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                logger.info(f"Guild {guild_id} synced {len(synced)} command(s): {[cmd.name for cmd in synced]}")
        else:
            # Global sync (takes up to 1 hour to update)
            synced = await bot.tree.sync()
            logger.info(f"Globally synced {len(synced)} command(s): {[cmd.name for cmd in synced]}")
            
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")


@bot.event
async def on_ready():
    """Bot ready event"""
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    
    # Sync commands based on config
    await sync_commands()

    # Load daily tournament reminder
    try:
        import tourney_reminder
        tourney_reminder.setup_reminder(bot)
        logger.info("✅ Tournament reminder loaded")
    except Exception as e:
        logger.error(f"❌ Failed to load tournament reminder: {e}")


async def main():
    if not await load_commands():
        logger.error("Failed to load commands. Exiting.")
        return

    if not hasattr(config, 'DISCORD_TOKEN') or not config.DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN not found in config!")
        return

    try:
        await bot.start(config.DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == "__main__":
    asyncio.run(main())