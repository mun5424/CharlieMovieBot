"""Slash-command UI for storing a user's birthday."""

from __future__ import annotations

import calendar
import datetime

import discord
import config
from discord import app_commands
from discord.ext import commands

from .db import BirthdayRecord, BirthdayStore
from .reminder import (
    BirthdayReminderCog,
    build_birthday_embed,
    load_birthday_deals,
)


def format_birthday(month: int, day: int) -> str:
    """Format a stored birthday for display."""
    return f"{calendar.month_name[month]} {day}"


def parse_birthday(month_value: str, day_value: str) -> tuple[int, int]:
    """
    Parse and validate birthday month/day values.

    The year 2000 is used for validation so February 29 is accepted.
    """
    month_text = month_value.strip()
    day_text = day_value.strip()

    if not month_text.isdigit() or not day_text.isdigit():
        raise ValueError("Month and day must both be numbers.")

    month = int(month_text)
    day = int(day_text)

    if not 1 <= month <= 12:
        raise ValueError("Month must be between 1 and 12.")

    if not 1 <= day <= 31:
        raise ValueError("Day must be between 1 and 31.")

    try:
        datetime.date(2000, month, day)
    except ValueError as exc:
        month_name = calendar.month_name[month]
        raise ValueError(
            f"{month_name} does not have a valid day {day}."
        ) from exc

    return month, day


class BirthdayModal(discord.ui.Modal):
    """Two-field birthday signup form."""

    def __init__(
        self,
        store: BirthdayStore,
        existing: BirthdayRecord | None = None,
    ) -> None:
        super().__init__(title="Set your birthday", timeout=180)

        self.store = store

        default_month = str(existing.month) if existing is not None else None
        default_day = str(existing.day) if existing is not None else None

        self.month_input = discord.ui.TextInput(
            label="Month (1-12)",
            placeholder="Example: 5 for May",
            default=default_month,
            min_length=1,
            max_length=2,
            required=True,
        )

        self.day_input = discord.ui.TextInput(
            label="Day (1-31)",
            placeholder="Example: 27",
            default=default_day,
            min_length=1,
            max_length=2,
            required=True,
        )

        self.add_item(self.month_input)
        self.add_item(self.day_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            month, day = parse_birthday(
                str(self.month_input.value),
                str(self.day_input.value),
            )
        except ValueError as exc:
            await interaction.response.send_message(
                (
                    f"❌ **Invalid birthday:** {exc}\n"
                    "Please run `/birthday` again and enter a valid month and day."
                ),
                ephemeral=True,
            )
            return

        updated = await self.store.upsert_birthday(
            interaction.user.id,
            month,
            day,
        )

        verb = "updated" if updated else "submitted"
        birthday = format_birthday(month, day)

        await interaction.response.send_message(
            (
                f"🎂 Your birthday has been {verb}: **{birthday}**.\n"
                "Only one birthday is stored per user. Running `/birthday` "
                "again will update your saved date.\n\n"
                "Your birthday will only be announced publicly on the saved date."
            ),
            ephemeral=True,
        )


class BirthdayCog(commands.Cog):
    def __init__(self, bot: commands.Bot, store: BirthdayStore) -> None:
        self.bot = bot
        self.store = store

    @app_commands.command(
        name="birthday",
        description="Set or update your birthday",
    )
    async def birthday(self, interaction: discord.Interaction) -> None:
        existing = await self.store.get_birthday(interaction.user.id)

        await interaction.response.send_modal(
            BirthdayModal(self.store, existing)
        )


    @app_commands.command(
        name="birthdaytest",
        description="Preview the birthday announcement embed",
    )
    @app_commands.guild_only()
    async def birthdaytest(self, interaction: discord.Interaction) -> None:
        admin_user_id = getattr(config, "BIRTHDAY_ADMIN_USER_ID", None)

        if admin_user_id is None:
            await interaction.response.send_message(
                "Birthday test command is not configured. "
                "Set `BIRTHDAY_ADMIN_USER_ID` in `config.py`.",
                ephemeral=True,
            )
            return

        if interaction.user.id != int(admin_user_id):
            await interaction.response.send_message(
                "You do not have access to this command.",
                ephemeral=True,
            )
            return

        birthday_deals = load_birthday_deals()
        display_name = getattr(
            interaction.user,
            "display_name",
            interaction.user.name,
        )

        embed = build_birthday_embed(
            display_name=display_name,
            birthday_deals=birthday_deals,
            avatar_url=str(interaction.user.display_avatar.url),
        )

        await interaction.response.send_message(
            content=(
                f"{interaction.user.mention} "
                "*(preview only — no public ping was sent)*"
            ),
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )



async def setup(bot: commands.Bot, db_path: str = "bot.db") -> None:
    """Load slash commands during normal command initialization."""
    store = BirthdayStore(db_path)

    await store.initialize()
    await bot.add_cog(BirthdayCog(bot, store))


async def setup_reminder(bot: commands.Bot, db_path: str = "bot.db") -> None:
    """Start the birthday scheduler after the bot is ready."""
    if bot.get_cog("BirthdayReminderCog") is not None:
        return

    store = BirthdayStore(db_path)

    await store.initialize()
    await bot.add_cog(BirthdayReminderCog(bot, store))