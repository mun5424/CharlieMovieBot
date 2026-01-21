"""
/handheld command
- Autocomplete from SQLite
- Embed output from stored JSON row
- Background refresh task
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from handhelds import db
from handhelds import images
from handhelds import ingest

logger = logging.getLogger(__name__)


def _format_value(v: Optional[str], max_len: int = 256) -> str:
    if not v:
        return "—"
    v = str(v).strip()
    if len(v) > max_len:
        return v[: max_len - 1] + "…"
    return v


def _pretty_embed_from_row(row: dict) -> discord.Embed:
    data = json.loads(row["data_json"])

    title = row["name"]
    embed = discord.Embed(title=title)

    # Set image if available (DB first, then JSON override fallback)
    image_url = row.get("image_url")
    if not (isinstance(image_url, str) and image_url.startswith("http")):
        image_url = images.get_image_url(row["slug"])
    if image_url:
        embed.set_thumbnail(url=image_url)

    # "Nice" fields if present
    embed.add_field(name="Brand", value=_format_value(row.get("brand")), inline=True)
    embed.add_field(name="OS", value=_format_value(row.get("os")), inline=True)
    embed.add_field(name="Released", value=_format_value(row.get("released")), inline=True)

    embed.add_field(name="Form Factor", value=_format_value(row.get("form_factor")), inline=True)
    embed.add_field(name="Performance", value=_format_value(row.get("performance")), inline=True)
    embed.add_field(name="Price (avg)", value=_format_value(row.get("price_avg")), inline=True)

    vendor = row.get("vendor_link")
    if vendor and vendor.startswith("http"):
        embed.add_field(name="Vendor Link", value=vendor, inline=False)

    # Add some extra interesting fields if they exist in the sheet
    for key in ["System On A Chip (SoC)", "CPU", "GPU", "RAM", "Screen Size", "Resolution", "Weight"]:
        if key in data and str(data[key]).strip():
            embed.add_field(name=key, value=_format_value(data[key]), inline=True)

    # Footer metadata
    embed.set_footer(text=f"slug: {row['slug']}")
    return embed


async def _autocomplete_handheld(interaction: discord.Interaction, current: str):
    # Return up to 25 choices
    matches = await db.search_names(current, limit=25)
    choices = []
    for m in matches:
        label = m["name"]
        # Add a tiny bit of disambiguation if we can
        suffix_bits = []
        if m.get("brand"):
            suffix_bits.append(m["brand"])
        if m.get("performance"):
            suffix_bits.append(m["performance"])
        if suffix_bits:
            label = f"{label} ({' • '.join(suffix_bits)})"

        # Choice "value" must be <= 100 chars; use the real name (not label)
        value = m["name"]
        if len(value) > 100:
            value = value[:100]

        choices.append(app_commands.Choice(name=label[:100], value=value))

    return choices


class HandheldCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="handheld", description="Look up a handheld from the spreadsheet")
    @app_commands.describe(name="Handheld name (autocomplete supported)")
    @app_commands.autocomplete(name=_autocomplete_handheld)
    async def handheld(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(thinking=True)

        await db.init_db()
        row = await db.get_by_slug_or_exact_name(name)

        if not row:
            # fallback: show suggestions
            sugg = await db.search_names(name, limit=8)
            if sugg:
                lines = [f"- **{s['name']}**" + (f" ({s['brand']})" if s.get("brand") else "") for s in sugg]
                return await interaction.followup.send(
                    "I couldn't find an exact match. Did you mean:\n" + "\n".join(lines)
                )
            return await interaction.followup.send("No match found for that handheld.")

        embed = _pretty_embed_from_row(row)
        await interaction.followup.send(embed=embed)


async def _refresh_loop(bot: commands.Bot, hours: int):
    # refresh on boot
    await asyncio.sleep(3)

    sheet_id = getattr(config, "HANDHELDS_SHEET_ID", None)
    gid = getattr(config, "HANDHELDS_SHEET_GID", "0")
    if not sheet_id:
        logger.warning("HANDHELDS_SHEET_ID not set; handheld refresh loop disabled.")
        return

    while True:
        try:
            await ingest.refresh_from_sheet(sheet_id=sheet_id, gid=str(gid))
        except Exception as e:
            logger.exception("Handheld refresh failed: %s", e)

        # sleep
        await asyncio.sleep(max(1, hours) * 3600)


async def setup(bot: commands.Bot):
    await bot.add_cog(HandheldCommands(bot))

    # Background refresh task - use asyncio.create_task instead of bot.loop
    hours = int(getattr(config, "HANDHELDS_REFRESH_HOURS", 24))

    task = asyncio.create_task(_refresh_loop(bot, hours=hours))
    setattr(bot, "_handhelds_refresh_task", task)

    # Make sure it gets cancelled on shutdown
    if hasattr(bot, "add_shutdown_handler"):
        bot.add_shutdown_handler(lambda: task.cancel())
