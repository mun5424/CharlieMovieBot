"""Slash-command UI for storing a user's birthday."""

from __future__ import annotations

import calendar
import datetime

import discord
from discord import app_commands
from discord.ext import commands

from .db import BirthdayRecord, BirthdayStore
from .reminder import BirthdayReminderCog


def format_birthday(month: int, day: int) -> str:
    """Format a stored birthday for display."""
    return f"{calendar.month_name[month]} {day}"


def parse_birthday(value: str) -> tuple[int, int]:
    """
    Parse an MM/DD birthday value.

    The year 2000 is used for validation so February 29 is accepted.
    """
    normalized = value.strip().replace("-", "/").replace(".", "/")
    parts = normalized.split("/")

    if len(parts) != 2:
        raise ValueError("Birthday must use MM/DD format.")

    try:
        month = int(parts[0])
        day = int(parts[1])
        datetime.date(2000, month, day)
    except ValueError as exc:
        raise ValueError("Please enter a valid birthday in MM/DD format.") from exc

    return month, day


class BirthdayModal(discord.ui.Modal):
    """One-field birthday signup form."""

    def __init__(
        self,
        store: BirthdayStore,
        existing: BirthdayRecord | None = None,
    ) -> None:
        super().__init__(title="Set your birthday", timeout=180)

        self.store = store

        default_value = None
        if existing is not None:
            default_value = f"{existing.month:02d}/{existing.day:02d}"

        self.birthday_input = discord.ui.TextInput(
            label="Birthday",
            placeholder="MM/DD, for example 05/27",
            default=default_value,
            min_length=3,
            max_length=5,
            required=True,
        )

        self.add_item(self.birthday_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            month, day = parse_birthday(str(self.birthday_input.value))
        except ValueError:
            await interaction.response.send_message(
                "That date is not valid. Please run `/birthday` again and "
                "enter your birthday as `MM/DD`, for example `05/27`.",
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


async def setup(bot: commands.Bot, db_path: str = "bot.db") -> None:
    store = BirthdayStore(db_path)

    await store.initialize()
    await bot.add_cog(BirthdayCog(bot, store))
    await bot.add_cog(BirthdayReminderCog(bot, store))
