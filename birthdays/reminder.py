"""Daily birthday celebration scheduler."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import config

from .db import BirthdayStore


logger = logging.getLogger(__name__)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
BIRTHDAY_ANNOUNCEMENT_TIME = datetime.time(hour=10, minute=0, tzinfo=PACIFIC_TZ)
BIRTHDAY_DEALS_FILE = Path(__file__).with_name("birthday_deals.json")


def load_birthday_deals() -> list[dict[str, str]]:
    """Load birthday reward links from the local JSON configuration file."""
    try:
        with BIRTHDAY_DEALS_FILE.open("r", encoding="utf-8") as file:
            data: Any = json.load(file)
    except FileNotFoundError:
        logger.error(
            "[Birthday] Deals file not found: %s",
            BIRTHDAY_DEALS_FILE,
        )
        return []
    except json.JSONDecodeError as exc:
        logger.error(
            "[Birthday] Invalid JSON in deals file %s: %s",
            BIRTHDAY_DEALS_FILE,
            exc,
        )
        return []

    if not isinstance(data, list):
        logger.error("[Birthday] Deals JSON must contain a list of deals.")
        return []

    valid_deals: list[dict[str, str]] = []

    for deal in data:
        if not isinstance(deal, dict):
            logger.warning("[Birthday] Skipping non-object deal entry: %s", deal)
            continue

        restaurant = deal.get("restaurant")
        reward = deal.get("reward")
        url = deal.get("url")

        values = (restaurant, reward, url)

        if not all(isinstance(value, str) and value.strip() for value in values):
            logger.warning("[Birthday] Skipping invalid deal entry: %s", deal)
            continue

        valid_deals.append(
            {
                "restaurant": restaurant.strip(),
                "reward": reward.strip(),
                "url": url.strip(),
            }
        )

    return valid_deals


def format_deal_lines(deals: list[dict[str, str]]) -> str:
    """Format birthday rewards as clickable Discord embed lines."""
    return "\n".join(
        f"• **{deal['restaurant']}** — [{deal['reward']}]({deal['url']})"
        for deal in deals
    )


def build_birthday_embed(
    display_name: str,
    birthday_deals: list[dict[str, str]],
    avatar_url: str | None = None,
) -> discord.Embed:
    """Build the public birthday celebration embed or an ephemeral preview."""
    safe_display_name = discord.utils.escape_markdown(display_name)

    embed = discord.Embed(
        title=f"🎂 It's {safe_display_name}'s Birthday Today! 🎂",
        description=(
            f"Let's take a moment to wish **{safe_display_name}** "
            "a happy birthday!\n\n"
            "🎉 **To celebrate, here are your birthday deals today! ** 🎉"
        ),
        color=0xFF8FCB,
    )

    if birthday_deals:
        first_group = birthday_deals[:6]
        second_group = birthday_deals[6:]

        embed.add_field(
            name="🍔 Birthday Meals & Treats",
            value=format_deal_lines(first_group),
            inline=False,
        )

        embed.add_field(
            value=format_deal_lines(second_group),
            inline=False,
        )

    embed.set_footer(
        text=(
            "Sign up your birthday using /birthday. "
            "The bot only announces birthdays on the saved date. "
            "Signup messages are visible only to you"
        )
    )

    if avatar_url is not None:
        embed.set_thumbnail(url=avatar_url)

    return embed


class BirthdayReminderCog(commands.Cog):
    """Posts one birthday celebration embed per birthday user at 10 AM Pacific."""

    def __init__(self, bot: commands.Bot, store: BirthdayStore) -> None:
        self.bot = bot
        self.store = store
        self._send_lock = asyncio.Lock()

        self.announce_birthdays.start()

    def cog_unload(self) -> None:
        self.announce_birthdays.cancel()

    async def _log_schedule_after_ready(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(1)

        logger.warning(
            "[Birthday] Reminder cog loaded. Current PT time=%s | "
            "configured time=%s | task_running=%s | next_iteration=%s",
            datetime.datetime.now(PACIFIC_TZ).isoformat(),
            BIRTHDAY_ANNOUNCEMENT_TIME.isoformat(),
            self.announce_birthdays.is_running(),
            self.announce_birthdays.next_iteration,
        )

    async def send_birthdays_for_date(
        self,
        today: datetime.date | None = None,
    ) -> int:
        """Send birthdays due today and return the number of member pings posted."""
        today = today or datetime.datetime.now(PACIFIC_TZ).date()

        # The scheduled run and restart catch-up can happen at nearly the same time.
        async with self._send_lock:
            return await self._send_birthdays_locked(today)

    async def _send_birthdays_locked(self, today: datetime.date) -> int:
        channel_ids = getattr(config, "BIRTHDAY_CHANNEL_IDS", [])

        if not channel_ids:
            logger.warning(
                "[Birthday] No BIRTHDAY_CHANNEL_IDS configured; skipping %s.",
                today,
            )
            return 0

        birthday_deals = load_birthday_deals()

        if not birthday_deals:
            logger.warning(
                "[Birthday] No valid birthday deals loaded; "
                "birthday announcements will still be posted without deals."
            )

        sent_members = 0

        for channel_id in channel_ids:
            channel = self.bot.get_channel(channel_id)

            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ) as exc:
                    logger.warning(
                        "[Birthday] Cannot access channel %s: %s",
                        channel_id,
                        exc,
                    )
                    continue

            birthdays = await self.store.get_unannounced_birthdays(
                today.month,
                today.day,
                today,
                channel_id,
            )

            logger.info(
                "[Birthday] Channel %s has %s unannounced birthday(s) for %s.",
                channel_id,
                len(birthdays),
                today,
            )

            if not birthdays:
                continue

            for birthday in birthdays:
                mention = f"<@{birthday.user_id}>"
                display_name = "Birthday Demon"

                guild = getattr(channel, "guild", None)
                member: discord.Member | None = None

                if guild is not None:
                    member = guild.get_member(birthday.user_id)

                    if member is None:
                        try:
                            member = await guild.fetch_member(birthday.user_id)
                        except (
                            discord.NotFound,
                            discord.Forbidden,
                            discord.HTTPException,
                        ):
                            member = None

                if member is not None:
                    mention = member.mention
                    display_name = member.display_name
                    avatar_url = str(member.display_avatar.url)

                embed = build_birthday_embed(
                    display_name=display_name,
                    birthday_deals=birthday_deals,
                    avatar_url=avatar_url,
                )

                try:
                    await channel.send(
                        content=mention,
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(
                            users=True,
                            roles=False,
                            everyone=False,
                        ),
                    )

                    await self.store.mark_announced(
                        today,
                        channel_id,
                        [birthday.user_id],
                    )

                    sent_members += 1

                    logger.info(
                        "[Birthday] Posted birthday ping for user %s "
                        "in channel %s for %s.",
                        birthday.user_id,
                        channel_id,
                        today,
                    )

                except discord.HTTPException as exc:
                    logger.error(
                        "[Birthday] Failed posting birthday for user %s "
                        "in channel %s: %s",
                        birthday.user_id,
                        channel_id,
                        exc,
                    )

        return sent_members

    @tasks.loop(time=BIRTHDAY_ANNOUNCEMENT_TIME)
    async def announce_birthdays(self) -> None:
        now = datetime.datetime.now(PACIFIC_TZ)
        today = now.date()

        logger.warning(
            "[Birthday] Scheduled task fired at %s PT for %s.",
            now.strftime("%I:%M:%S %p"),
            today,
        )

        sent_count = await self.send_birthdays_for_date(today)

        logger.warning(
            "[Birthday] Scheduled task completed. Sent %s birthday announcement(s).",
            sent_count,
        )

    @announce_birthdays.error
    async def announce_birthdays_error(self, error: BaseException) -> None:
        logger.error(
            "[Birthday] Scheduled birthday task crashed: %s",
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
        
    @announce_birthdays.before_loop
    async def before_announce_birthdays(self) -> None:
        await self.bot.wait_until_ready()

