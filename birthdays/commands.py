"""Slash-command UI for storing a user's birthday."""

from __future__ import annotations

import calendar

import discord
from discord import app_commands
from discord.ext import commands

from .db import BirthdayRecord, BirthdayStore
from .reminder import BirthdayReminderCog


def format_birthday(month: int, day: int) -> str:
    return f"{calendar.month_name[month]} {day}"


class MonthSelect(discord.ui.Select):
    def __init__(self, selected_month: int | None = None) -> None:
        options = [
            discord.SelectOption(
                label=calendar.month_name[month],
                value=str(month),
                default=month == selected_month,
            )
            for month in range(1, 13)
        ]
        super().__init__(
            placeholder="Choose your birth month…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, BirthdayPickerView)
        await self.view.select_month(interaction, int(self.values[0]))


class DaySelect(discord.ui.Select):
    def __init__(self, start_day: int, end_day: int) -> None:
        options = [
            discord.SelectOption(label=str(day), value=str(day))
            for day in range(start_day, end_day + 1)
        ]
        super().__init__(
            placeholder=f"Choose day {start_day}–{end_day}…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, BirthdayPickerView)
        await self.view.submit_birthday(interaction, int(self.values[0]))


class BirthdayPickerView(discord.ui.View):
    def __init__(
        self,
        store: BirthdayStore,
        owner_id: int,
        existing: BirthdayRecord | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.store = store
        self.owner_id = owner_id
        self.selected_month = existing.month if existing else None
        self._render_controls()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "This birthday picker belongs to another user.",
            ephemeral=True,
        )
        return False

    def _render_controls(self) -> None:
        self.clear_items()
        self.add_item(MonthSelect(self.selected_month))

        if self.selected_month is None:
            return

        final_day = calendar.monthrange(2000, self.selected_month)[1]
        self.add_item(DaySelect(1, min(15, final_day)))
        if final_day > 15:
            self.add_item(DaySelect(16, final_day))

    async def select_month(self, interaction: discord.Interaction, month: int) -> None:
        self.selected_month = month
        self._render_controls()
        await interaction.response.edit_message(
            content=(
                f"Month selected: **{calendar.month_name[month]}**. "
                "Now choose the day below."
            ),
            view=self,
        )

    async def submit_birthday(self, interaction: discord.Interaction, day: int) -> None:
        if self.selected_month is None:
            await interaction.response.send_message(
                "Choose your birth month first.",
                ephemeral=True,
            )
            return

        updated = await self.store.upsert_birthday(
            interaction.user.id,
            self.selected_month,
            day,
        )
        verb = "updated" if updated else "submitted"
        birthday = format_birthday(self.selected_month, day)
        self.stop()

        await interaction.response.edit_message(
            content=(
                f"🎂 Your birthday has been {verb}: **{birthday}**.\n"
                "Only one birthday is stored per user. Running `/birthday` again "
                "will update your saved date."
            ),
            view=None,
        )


class BirthdayCog(commands.Cog):
    def __init__(self, bot: commands.Bot, store: BirthdayStore) -> None:
        self.bot = bot
        self.store = store

    @app_commands.command(name="birthday", description="Set or update your birthday")
    async def birthday(self, interaction: discord.Interaction) -> None:
        existing = await self.store.get_birthday(interaction.user.id)
        if existing is None:
            prompt = "Pick your birthday. Your birth year is not stored."
        else:
            saved = format_birthday(existing.month, existing.day)
            prompt = (
                f"Your saved birthday is **{saved}**. "
                "Pick a new date below to update it."
            )

        await interaction.response.send_message(
            prompt,
            view=BirthdayPickerView(self.store, interaction.user.id, existing),
            ephemeral=True,
        )


async def setup(bot: commands.Bot, db_path: str = "bot.db") -> None:
    store = BirthdayStore(db_path)
    await store.initialize()
    await bot.add_cog(BirthdayCog(bot, store))
    await bot.add_cog(BirthdayReminderCog(bot, store))
