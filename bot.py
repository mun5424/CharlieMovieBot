import discord
from discord.ext import commands
from discord import app_commands
import config

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", help_command=None, intents=intents)

# Register command modules
from commands import general, watchlist
general.setup(bot)
watchlist.setup(bot)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        guild = discord.Object(id=1217734088593510422)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} command(s) to test server")
        
        synced_global = await bot.tree.sync()
        print(f"Synced {len(synced_global)} command(s) globally")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# Try importing after bot setup
try:
    from commands import general, watchlist
    general.setup(bot)
    watchlist.setup(bot)
    print("✅ Commands loaded successfully")
except Exception as e:
    print(f"❌ Error loading commands: {e}")
    import traceback
    traceback.print_exc()

bot.run(config.DISCORD_TOKEN)