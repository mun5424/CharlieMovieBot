import discord
from discord.ext import commands
from discord import app_commands
import config

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", help_command=None, intents=intents)

# Load commands BEFORE on_ready()
try:
    from commands import general, watchlist
    general.setup(bot)
    watchlist.setup(bot)
    print("✅ Commands loaded successfully")
    print(f"Registered commands: {[cmd.name for cmd in bot.tree.get_commands()]}")
except Exception as e:
    print(f"❌ Error loading commands: {e}")
    import traceback
    traceback.print_exc()

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    
    try:
        guild = discord.Object(id=1217734088593510422)
        
        # Method 1: Clear guild commands first, then add new ones
        bot.tree.clear_commands(guild=guild)
        await bot.tree.sync(guild=guild)  # Sync the clear
        print("Cleared guild commands")
        
        # Add commands to guild and sync
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Guild synced {len(synced)} command(s): {[cmd.name for cmd in synced]}")
        
    except Exception as e:
        print(f"Failed to sync: {e}")

if __name__ == "__main__":
    bot.run(config.DISCORD_TOKEN)