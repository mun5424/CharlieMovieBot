import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .store import TwitchStore
from .notifier import TwitchNotifier
from .twitch_client import TwitchClient

logger = logging.getLogger(__name__)

# Twitch brand color
TWITCH_PURPLE = 0x9146FF


def _twitch_embed(title: str, description: str = None, success: bool = True) -> discord.Embed:
    """Create a consistent Twitch-themed embed."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=TWITCH_PURPLE if success else 0xE74C3C,
    )
    embed.set_author(
        name="Twitch Notifications",
        icon_url="https://static.twitchcdn.net/assets/favicon-32-e29e246c157142c94346.png"
    )
    return embed


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
        embed = _twitch_embed(
            "Channel Updated",
            f"Live notifications will be posted in {channel.mention}"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="twitch_set_role", description="Set the role to ping when a tracked streamer goes live (optional).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_role(self, interaction: discord.Interaction, role: discord.Role):
        await self.store.set_guild_role(interaction.guild_id, role.id)
        embed = _twitch_embed(
            "Ping Role Updated",
            f"{role.mention} will be pinged when streamers go live"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="twitch_clear_role", description="Stop pinging a role for Twitch live notifications.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clear_role(self, interaction: discord.Interaction):
        await self.store.set_guild_role(interaction.guild_id, None)
        embed = _twitch_embed(
            "Ping Role Cleared",
            "No role will be pinged for live notifications"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="twitch_add", description="Track a Twitch streamer (by login, e.g. shroud).")
    async def add_streamer(self, interaction: discord.Interaction, user_login: str):
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        user_login = user_login.strip().lower()
        if not user_login:
            await interaction.response.send_message("Please provide a Twitch username.", ephemeral=True)
            return

        # Check if user is admin (manage_guild permission)
        is_admin = interaction.user.guild_permissions.manage_guild if hasattr(interaction.user, 'guild_permissions') else False

        # Non-admins can only have one streamer - remove old one first
        replaced = None
        if not is_admin:
            existing = await self.store.get_user_streamer(interaction.guild_id, interaction.user.id)
            if existing and existing != user_login:
                await self.store.remove_streamer(interaction.guild_id, existing)
                replaced = existing

        await self.store.add_streamer(interaction.guild_id, user_login, added_by=interaction.user.id)

        embed = _twitch_embed(
            "Streamer Added",
            f"Now tracking **[{user_login}](https://twitch.tv/{user_login})**"
        )
        if replaced:
            embed.set_footer(text=f"Replaced: {replaced}")
        embed.set_thumbnail(url=f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{user_login}-320x180.jpg")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="twitch_remove", description="Stop tracking a Twitch streamer (by login).")
    async def remove_streamer(self, interaction: discord.Interaction, user_login: str):
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        user_login = user_login.strip().lower()

        # Check if user is admin
        is_admin = interaction.user.guild_permissions.manage_guild if hasattr(interaction.user, 'guild_permissions') else False

        # Non-admins can only remove their own streamer
        if not is_admin:
            their_streamer = await self.store.get_user_streamer(interaction.guild_id, interaction.user.id)
            if their_streamer != user_login:
                embed = _twitch_embed(
                    "Permission Denied",
                    "You can only remove streamers you added yourself.",
                    success=False
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        await self.store.remove_streamer(interaction.guild_id, user_login)
        embed = _twitch_embed(
            "Streamer Removed",
            f"No longer tracking **{user_login}**"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="twitch_list", description="List tracked Twitch streamers in this server.")
    async def list_streamers(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        await interaction.response.defer()

        cfg = await self.store.get_guild_config(interaction.guild_id)
        streamers = await self.store.list_streamers(interaction.guild_id)

        channel_id = cfg.get("channel_id")
        role_id = cfg.get("role_id")

        channel = interaction.guild.get_channel(channel_id) if (interaction.guild and channel_id) else None
        role = interaction.guild.get_role(role_id) if (interaction.guild and role_id) else None

        # Config embed
        config_parts = []
        config_parts.append(f"**Channel:** {channel.mention if channel else '*Not set*'}")
        config_parts.append(f"**Role:** {role.mention if role else '*None*'}")

        if not streamers:
            embed = _twitch_embed(
                f"Tracked Streamers (0)",
                "\n".join(config_parts) + "\n\n*No streamers yet — use `/twitch_add <username>`*"
            )
            await interaction.followup.send(embed=embed)
            return

        # Fetch profile pics from Twitch API
        user_logins = [s['user_login'] for s in streamers]
        user_info = {}
        try:
            async with aiohttp.ClientSession() as session:
                client = TwitchClient(session)
                user_info = await client.get_users(user_logins)
        except Exception as e:
            logger.warning("Failed to fetch Twitch user info: %s", e)

        # Build streamer list
        streamer_lines = []
        for s in streamers:
            login = s['user_login']
            info = user_info.get(login, {})
            display_name = info.get('display_name', login)
            added_by_id = s.get('added_by')

            # Format: streamer link + who added (bold)
            line = f"• **[{display_name}](https://twitch.tv/{login})**"
            if added_by_id and interaction.guild:
                member = interaction.guild.get_member(int(added_by_id))
                if member:
                    line += f" — added by **{member.display_name}**"
            streamer_lines.append(line)

        # Single embed with everything
        description = "\n".join(config_parts) + "\n\n" + "\n".join(streamer_lines)
        embed = _twitch_embed(f"Tracked Streamers ({len(streamers)})", description)
        await interaction.followup.send(embed=embed)

async def _start_notifier_when_ready(bot: commands.Bot, notifier: TwitchNotifier):
    """Start the notifier after bot is ready."""
    await bot.wait_until_ready()
    try:
        await notifier.start()
    except Exception:
        logger.exception("Failed to start Twitch notifier")


async def setup(bot: commands.Bot, db_path: str = "bot.db", poll_interval_sec: int = 90) -> None:
    """
    Call this from your main bot setup, like: await twitch.setup(bot, db_path="mybot.db")
    """
    store = TwitchStore(db_path)
    await store.connect()

    notifier = TwitchNotifier(bot=bot, store=store, poll_interval_sec=poll_interval_sec)
    cog = TwitchNotifCog(bot=bot, store=store, notifier=notifier)
    await bot.add_cog(cog)

    # Start notifier in background after bot is ready
    asyncio.create_task(_start_notifier_when_ready(bot, notifier))
