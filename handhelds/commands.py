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
from datetime import datetime, time, timedelta
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


def _get_data_field(data: dict, *keys: str) -> Optional[str]:
    """Try multiple possible field names and return the first non-empty value."""
    for key in keys:
        val = data.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return None


def _performance_color(perf: Optional[str]) -> int:
    """Return embed color based on performance rating."""
    if not perf:
        return 0x5865F2  # Discord blurple
    perf_lower = perf.lower()
    if "s+" in perf_lower or "s tier" in perf_lower:
        return 0xFFD700  # Gold
    if "s" in perf_lower and "a" not in perf_lower:
        return 0xC0C0C0  # Silver
    if "a+" in perf_lower:
        return 0x00FF00  # Bright green
    if "a" in perf_lower:
        return 0x32CD32  # Lime green
    if "b" in perf_lower:
        return 0x3498DB  # Blue
    if "c" in perf_lower:
        return 0xE67E22  # Orange
    return 0x5865F2  # Discord blurple


def _pretty_embed_from_row(row: dict) -> discord.Embed:
    data = json.loads(row["data_json"])

    name = row["name"]
    brand = row.get("brand") or ""
    perf = row.get("performance")

    # Build a short description/tagline
    desc_parts = []
    if brand:
        desc_parts.append(f"**{brand}**")
    os_val = row.get("os")
    if os_val:
        desc_parts.append(f"{os_val}")
    form = row.get("form_factor")
    if form:
        desc_parts.append(f"{form}")

    description = " · ".join(desc_parts) if desc_parts else None

    embed = discord.Embed(
        title=name,
        description=description,
        color=_performance_color(perf),
    )

    # Large image at the bottom
    image_url = row.get("image_url")
    if not (isinstance(image_url, str) and image_url.startswith("http")):
        image_url = images.get_image_url(row["slug"])
    if image_url:
        embed.set_image(url=image_url)

    # --- Row 1: Key Info ---
    price = row.get("price_avg")
    if price:
        embed.add_field(name="Price", value=price, inline=True)

    released = row.get("released")
    if released:
        embed.add_field(name="Released", value=released, inline=True)

    if perf:
        embed.add_field(name="Performance", value=perf, inline=True)

    # --- Row 2: Display ---
    screen = _get_data_field(data, "Screen Size", "Screen", "Display Size")
    resolution = _get_data_field(data, "Resolution", "Screen Resolution", "Display Resolution")
    aspect = _get_data_field(data, "Aspect Ratio", "Aspect")

    display_parts = []
    if screen:
        display_parts.append(screen)
    if resolution:
        display_parts.append(resolution)
    if aspect:
        display_parts.append(f"({aspect})")

    if display_parts:
        embed.add_field(name="Display", value=" · ".join(display_parts), inline=False)

    # --- Row 3: Hardware ---
    soc = _get_data_field(data, "System On A Chip (SoC)", "SoC", "Chipset", "Processor")
    cpu = _get_data_field(data, "CPU", "Processor")
    gpu = _get_data_field(data, "GPU", "Graphics")
    ram = _get_data_field(data, "RAM", "Memory")

    hw_parts = []
    if soc:
        hw_parts.append(f"**SoC:** {soc}")
    elif cpu:
        hw_parts.append(f"**CPU:** {cpu}")
    if gpu:
        hw_parts.append(f"**GPU:** {gpu}")
    if ram:
        hw_parts.append(f"**RAM:** {ram}")

    if hw_parts:
        embed.add_field(name="Hardware", value="\n".join(hw_parts), inline=False)

    # --- Row 4: Battery & Storage ---
    battery = _get_data_field(data, "Battery", "Battery Capacity", "Battery (mAh)")
    storage = _get_data_field(data, "Storage", "Internal Storage")
    weight = _get_data_field(data, "Weight", "Weight (g)")

    if battery:
        embed.add_field(name="Battery", value=battery, inline=True)
    if storage:
        embed.add_field(name="Storage", value=storage, inline=True)
    if weight:
        embed.add_field(name="Weight", value=weight, inline=True)

    # --- Connectivity ---
    wifi = _get_data_field(data, "WiFi", "Wi-Fi", "Wireless")
    bt = _get_data_field(data, "Bluetooth", "BT")
    hdmi = _get_data_field(data, "HDMI", "Video Out", "HDMI Out")

    conn_parts = []
    if wifi and wifi.lower() not in ("no", "none", "n/a", "-"):
        conn_parts.append(f"WiFi: {wifi}")
    if bt and bt.lower() not in ("no", "none", "n/a", "-"):
        conn_parts.append(f"BT: {bt}")
    if hdmi and hdmi.lower() not in ("no", "none", "n/a", "-"):
        conn_parts.append(f"HDMI: {hdmi}")

    if conn_parts:
        embed.add_field(name="Connectivity", value=" · ".join(conn_parts), inline=False)

    # --- Vendor Link ---
    vendor = row.get("vendor_link")
    if vendor and vendor.startswith("http"):
        embed.add_field(name="Buy", value=f"[Vendor Link]({vendor})", inline=False)

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


    
    @app_commands.command(name="handheld_refresh", description="Manually refresh handheld data from spreadsheet")
    @app_commands.default_permissions(administrator=True)
    async def handheld_refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        sheet_id = getattr(config, "HANDHELDS_SHEET_ID", None)
        gid = getattr(config, "HANDHELDS_SHEET_GID", "0")

        if not sheet_id:
            return await interaction.followup.send("HANDHELDS_SHEET_ID not configured.")

        try:
            await db.init_db()
            changed, count = await ingest.refresh_from_sheet(sheet_id=sheet_id, gid=str(gid))
            if changed:
                await interaction.followup.send(f"Refreshed {count} handhelds from spreadsheet.")
            else:
                await interaction.followup.send("No changes detected (data already up to date).")
        except Exception as e:
            logger.exception("Manual handheld refresh failed: %s", e)
            await interaction.followup.send(f"Refresh failed: {e}")


def _seconds_until_next_monday_4am() -> float:
    """Calculate seconds until next Monday at 4:00 AM local time."""
    now = datetime.now()
    # Monday = 0
    days_until_monday = (0 - now.weekday()) % 7
    if days_until_monday == 0 and now.time() >= time(4, 0):
        # It's Monday but past 4am, wait until next Monday
        days_until_monday = 7

    next_monday_4am = datetime.combine(
        now.date() + timedelta(days=days_until_monday),
        time(4, 0)
    )
    return (next_monday_4am - now).total_seconds()


async def _refresh_loop(bot: commands.Bot):
    """Weekly refresh loop - runs every Monday at 4:00 AM."""
    sheet_id = getattr(config, "HANDHELDS_SHEET_ID", None)
    gid = getattr(config, "HANDHELDS_SHEET_GID", "0")
    if not sheet_id:
        logger.warning("HANDHELDS_SHEET_ID not set; handheld refresh loop disabled.")
        return

    # Skip initial refresh on startup - use /handheld_refresh for manual ingest
    # Loop weekly on Monday 4am
    while True:
        sleep_seconds = _seconds_until_next_monday_4am()
        logger.info(f"Handhelds: Next refresh in {sleep_seconds / 3600:.1f} hours (Monday 4:00 AM)")
        await asyncio.sleep(sleep_seconds)

        try:
            logger.info("Handhelds: Running scheduled weekly refresh...")
            await ingest.refresh_from_sheet(sheet_id=sheet_id, gid=str(gid))
        except Exception as e:
            logger.exception("Handheld weekly refresh failed: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(HandheldCommands(bot))

    # Background refresh task - runs Monday 4am weekly
    task = asyncio.create_task(_refresh_loop(bot))
    setattr(bot, "_handhelds_refresh_task", task)

    # Make sure it gets cancelled on shutdown
    if hasattr(bot, "add_shutdown_handler"):
        bot.add_shutdown_handler(lambda: task.cancel())
