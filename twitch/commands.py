import logging
import discord
from discord import app_commands
from discord.ext import commands

from .store import TwitchStore
from .notifier import TwitchNotifier

logger = logging.getLogger(__name__)

class TwitchNotifCog(commands.Cog):
    def __init__(self, bot: commands.Bot, store: TwitchStore, notifier: TwitchNotifier):
        self.bot = bot
        self.store = store
        self.notifier = notifier

    # ---- admin commands ----
    @app_commands.command(name="twitch_set_channel", description="Set the channel where Twitch live notifications will be posted.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.store.set_guild_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"✅ Twitch notifications channel set to {channel.mention}", ephemeral=True)

    @app_commands.command(name="twitch_set_role", description="Set the role to ping when a tracked streamer goes live (optional).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_role(self, interaction: discord.Interaction, role: discord.Role):
        await self.store.set_guild_role(interaction.guild_id, role.id)
        await interaction.response.send_message(f"✅ Twitch ping role set to {role.mention}", ephemeral=True)

    @app_commands.command(name="twitch_clear_role", description="Stop pinging a role for Twitch live notifications.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clear_role(self, interaction: discord.Interaction):
        await self.store.set_guild_role(interaction.guild_id, None)
        await interaction.response.send_message("✅ Twitch ping role cleared.", ephemeral=True)

    @app_commands.command(name="twitch_add", description="Track a Twitch streamer (by login, e.g. shroud).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_streamer(self, interaction: discord.Interaction, user_login: str):
        if not interaction.guild_id:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        user_login = user_login.strip().lower()
        if not user_login:
            await interaction.response.send_message("Give me a Twitch user_login (e.g. shroud).", ephemeral=True)
            return

        await self.store.add_streamer(interaction.guild_id, user_login)
        await interaction.response.send_message(f"✅ Tracking Twitch streamer: `{user_login}`", ephemeral=True)

    @app_commands.command(name="twitch_remove", description="Stop tracking a Twitch streamer (by login).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_streamer(self, interaction: discord.Interaction, user_login: str):
        if not interaction.guild_id:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        user_login = user_login.strip().lower()
        await self.store.remove_streamer(interaction.guild_id, user_login)
        await interaction.response.send_message(f"✅ Removed Twitch streamer: `{user_login}`", ephemeral=True)

    @app_commands.command(name="twitch_list", description="List tracked Twitch streamers in this server.")
    async def list_streamers(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        cfg = await self.store.get_guild_config(interaction.guild_id)
        streamers = await self.store.list_streamers(interaction.guild_id)

        channel_id = cfg.get("channel_id")
        role_id = cfg.get("role_id")

        channel = interaction.guild.get_channel(channel_id) if (interaction.guild and channel_id) else None
        role = interaction.guild.get_role(role_id) if (interaction.guild and role_id) else None

        lines = []
        lines.append(f"**Channel:** {channel.mention if channel else '`(not set)`'}")
        lines.append(f"**Role:** {role.mention if role else '`(none)`'}")
        lines.append("")
        if not streamers:
            lines.append("No streamers tracked yet. Use `/twitch_add`.")
        else:
            lines.append("**Tracked streamers:**")
            lines.extend([f"- `{s['user_login']}`" for s in streamers])

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

async def _ensure_started(bot: commands.Bot, store: TwitchStore, notifier: TwitchNotifier):
    # Start after bot is ready
    await bot.wait_until_ready()
    try:
        await notifier.start()
    except Exception:
        logger.exception("Failed to start Twitch notifier")

def setup(bot: commands.Bot, db_path: str = "bot.db", poll_interval_sec: int = 90) -> None:
    """
    Call this from your main bot setup, like: twitch_notifs.setup(bot, db_path="mybot.db")
    """
    store = TwitchStore(db_path)

    async def init_and_add():
        await store.connect()
        notifier = TwitchNotifier(bot=bot, store=store, poll_interval_sec=poll_interval_sec)
        cog = TwitchNotifCog(bot=bot, store=store, notifier=notifier)
        await bot.add_cog(cog)
        bot.loop.create_task(_ensure_started(bot, store, notifier))

    bot.loop.create_task(init_and_add())
