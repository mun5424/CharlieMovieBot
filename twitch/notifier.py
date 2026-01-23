import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List

import aiohttp
import discord

from .store import TwitchStore
from .twitch_client import TwitchClient

logger = logging.getLogger(__name__)

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class TwitchNotifier:
    def __init__(
        self,
        bot: discord.Client,
        store: TwitchStore,
        poll_interval_sec: int = 90,
        batch_size: int = 100,
    ):
        self.bot = bot
        self.store = store
        self.poll_interval_sec = poll_interval_sec
        self.batch_size = batch_size

        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._session: Optional[aiohttp.ClientSession] = None
        self._client: Optional[TwitchClient] = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._session = aiohttp.ClientSession()
        self._client = TwitchClient(self._session)
        self._task = asyncio.create_task(self._run_loop(), name="twitch_notifier_loop")
        logger.info("TwitchNotifier started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        self._task = None
        if self._session:
            await self._session.close()
            self._session = None
        self._client = None
        logger.info("TwitchNotifier stopped")

    async def _run_loop(self) -> None:
        assert self._client is not None
        backoff = 0

        while not self._stop_event.is_set():
            try:
                await self._poll_once()
                backoff = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("TwitchNotifier poll failed: %s", e)
                # simple backoff: 30s, 60s, 120s, max 300s
                backoff = min(300, 30 if backoff == 0 else backoff * 2)

            wait_time = backoff if backoff > 0 else self.poll_interval_sec
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_time)
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self) -> None:
        assert self._client is not None

        guild_streamers = await self.store.list_all_guild_streamers()
        if not guild_streamers:
            return

        # Collect unique logins for batch querying
        unique_logins = sorted({login for logins in guild_streamers.values() for login in logins})
        live_map: Dict[str, dict] = {}

        # Batch requests if you ever grow beyond 100
        for i in range(0, len(unique_logins), self.batch_size):
            chunk = unique_logins[i:i+self.batch_size]
            chunk_live = await self._client.get_live_streams(chunk)
            live_map.update(chunk_live)

        # Process per guild
        for guild_id, logins in guild_streamers.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            cfg = await self.store.get_guild_config(guild_id)
            channel_id = cfg.get("channel_id")
            role_id = cfg.get("role_id")

            if not channel_id:
                # No configured channel -> skip silently
                continue

            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
                continue

            for login in logins:
                await self._handle_streamer(guild, channel, role_id, login, live_map.get(login))

    async def _handle_streamer(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        role_id: Optional[int],
        user_login: str,
        live_item: Optional[dict],
    ) -> None:
        state = await self.store.get_state(guild.id, user_login)
        was_live = bool(state.get("is_live"))

        if live_item is None:
            # currently offline
            if was_live:
                await self.store.set_state(guild.id, user_login, False, None, None, state.get("last_notified_at"))
            return

        # currently live
        stream_id = live_item.get("id")
        started_at = live_item.get("started_at")
        title = live_item.get("title") or "Live on Twitch"
        display_name = live_item.get("user_name") or user_login
        url = f"https://twitch.tv/{user_login}"

        # Notify only on transition offline -> live OR new stream id (safety)
        last_stream_id = state.get("last_stream_id")
        should_notify = (not was_live) or (stream_id and last_stream_id and stream_id != last_stream_id)

        if should_notify:
            # Check bot permissions in the channel
            if hasattr(channel, 'permissions_for'):
                bot_perms = channel.permissions_for(guild.me)
                if not bot_perms.send_messages:
                    logger.warning(
                        "Twitch: Missing send_messages permission in guild=%s channel=%s. "
                        "Please give the bot permission to send messages in that channel.",
                        guild.id, channel.id
                    )
                    return

            role_mention = ""
            allowed = discord.AllowedMentions.none()
            if role_id:
                role = guild.get_role(int(role_id))
                if role:
                    role_mention = role.mention + " "
                    allowed = discord.AllowedMentions(roles=[role])

            msg = f"{role_mention}**{display_name}** is live: **{title}**\n{url}"

            try:
                await channel.send(msg, allowed_mentions=allowed)
                notified_at = utc_now_iso()
            except discord.Forbidden:
                logger.warning(
                    "Twitch: Bot lacks permission to post in guild=%s channel=%s. "
                    "Check channel permissions.",
                    guild.id, channel.id
                )
                notified_at = state.get("last_notified_at")
            except Exception:
                logger.exception("Failed to send Twitch notification in guild=%s channel=%s", guild.id, channel.id)
                notified_at = state.get("last_notified_at")

            await self.store.set_state(guild.id, user_login, True, stream_id, started_at, notified_at)
        else:
            # Keep state fresh but don't re-notify
            await self.store.set_state(guild.id, user_login, True, stream_id, started_at, state.get("last_notified_at"))
