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
BIRTHDAY_ANNOUNCEMENT_TIME = datetime.time(hour=12, minute=13, tzinfo=PACIFIC_TZ)
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
        first_group = birthday_deals[:5]
        second_group = birthday_deals[5:]

        embed.add_field(
            name="🍔 Birthday Meals",
            value=format_deal_lines(first_group),
            inline=False,
        )

        embed.add_field(
            name="🍭 Birthday Treats",
            value=format_deal_lines(second_group),
            inline=False,
        )
        
        embed.add_field(
            name="🌟 Dont forget to check your restaurant apps for existing member rewards! ",
            value="",
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
        self._startup_log_task: asyncio.Task[None] | None = None

        logger.warning(
            "[Birthday] BirthdayReminderCog constructed. "
            "Configured announcement time=%s",
            BIRTHDAY_ANNOUNCEMENT_TIME.isoformat(),
        )

        self.announce_birthdays.start()

        logger.warning(
            "[Birthday] announce_birthdays.start() called. "
            "task_running=%s | bot_ready=%s | task=%s",
            self.announce_birthdays.is_running(),
            self.bot.is_ready(),
            self.announce_birthdays.get_task(),
        )

        self._startup_log_task = asyncio.create_task(
            self._log_schedule_after_ready()
        )

    def cog_unload(self) -> None:
        logger.warning("[Birthday] BirthdayReminderCog unloading; cancelling task.")

        self.announce_birthdays.cancel()

        if self._startup_log_task is not None:
            self._startup_log_task.cancel()

    async def _log_schedule_after_ready(self) -> None:
        await asyncio.sleep(1)

        logger.warning(
            "[Birthday] Reminder cog loaded. Current PT time=%s | "
            "configured time=%s | task_running=%s | next_iteration=%s",
            datetime.datetime.now(PACIFIC_TZ).isoformat(),
            BIRTHDAY_ANNOUNCEMENT_TIME.isoformat(),
            self.announce_birthdays.is_running(),
            self.announce_birthdays.next_iteration,
        )

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
