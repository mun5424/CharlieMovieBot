# food/commands.py - Discord commands for food nutrition lookup
import logging
from typing import Optional

import discord
from discord import app_commands

from food.db import search_food, get_random_food, get_food_by_id, get_vendors

logger = logging.getLogger(__name__)

# Constants
AUTOCOMPLETE_LIMIT = 25

# FDA Daily Values for percentage calculations
DAILY_VALUES = {
    "calories": 2000,
    "total_fat_g": 78,
    "sat_fat_g": 20,
    "cholesterol_mg": 300,
    "sodium_mg": 2300,
    "carbs_g": 275,
    "protein_g": 50,
}


def calc_dv(value: Optional[float], nutrient: str) -> str:
    """Calculate % daily value"""
    if value is None or nutrient not in DAILY_VALUES:
        return ""
    pct = round((value / DAILY_VALUES[nutrient]) * 100)
    return f"({pct}%)"


def format_nutrient(value: Optional[float], unit: str = "", nutrient: str = "") -> str:
    """Format a nutrient value with optional DV%"""
    if value is None:
        return "—"
    val_str = f"{int(value)}" if value == int(value) else f"{value:.1f}"
    dv = calc_dv(value, nutrient)
    if dv:
        return f"{val_str}{unit} {dv}"
    return f"{val_str}{unit}"


def get_calorie_color(calories: Optional[float]) -> int:
    """Get embed color based on calories"""
    if calories is None:
        return 0x808080  # Gray
    elif calories < 300:
        return 0x2ECC71  # Green
    elif calories < 600:
        return 0xF1C40F  # Yellow
    elif calories < 900:
        return 0xE67E22  # Orange
    else:
        return 0xE74C3C  # Red


def create_food_embed(food: dict) -> discord.Embed:
    """Create a compact embed for food nutrition info with DV%"""

    vendor = food.get("vendor", "Unknown")
    name = food.get("name", "Unknown Item")
    calories = food.get("calories")
    serving = food.get("serving_size") or "1 serving"
    category = food.get("food_category")

    # Build description with category, vendor, serving, and all nutrition
    desc_parts = []
    if category:
        desc_parts.append(f"*{category}*")
    desc_parts.append(f"**{vendor}** • {serving}")

    embed = discord.Embed(
        title=f"{name}",
        description="\n".join(desc_parts),
        color=get_calorie_color(calories)
    )

    # Build 2-column layout with inline fields
    cal_dv = calc_dv(calories, "calories")
    cal_val = int(calories) if calories and calories == int(calories) else calories

    # Row 1: Calories | Protein
    embed.add_field(name="Calories 🔥", value=f"**{cal_val or '—'}** {cal_dv}".strip(), inline=True)
    embed.add_field(name="Protein 🥩", value=format_nutrient(food.get('protein_g'), 'g', 'protein_g'), inline=True)

    # Row 2: Carbs | Fat
    embed.add_field(name="Carbs 🍞", value=format_nutrient(food.get('carbs_g'), 'g', 'carbs_g'), inline=True)

    total_fat = food.get("total_fat_g")
    sat_fat = food.get("sat_fat_g")
    fat_dv = calc_dv(total_fat, "total_fat_g")

    if total_fat is not None:
        fat_val = int(total_fat) if total_fat == int(total_fat) else f"{total_fat:.1f}"
        if sat_fat is not None:
            sat_val = int(sat_fat) if sat_fat == int(sat_fat) else f"{sat_fat:.1f}"
            fat_display = f"{fat_val}g/{sat_val}g {fat_dv}".strip()
        else:
            fat_display = f"{fat_val}g {fat_dv}".strip()
    else:
        fat_display = "—"

    embed.add_field(name="Fat/Sat 🧈", value=fat_display, inline=True)

    # Row 3: Cholesterol | Sodium
    embed.add_field(name="Cholesterol 💊", value=format_nutrient(food.get('cholesterol_mg'), 'mg', 'cholesterol_mg'), inline=True)
    embed.add_field(name="Sodium 🧂", value=format_nutrient(food.get('sodium_mg'), 'mg', 'sodium_mg'), inline=True)

    # Add logo if available
    logo_url = food.get("logo_url")
    if logo_url:
        embed.set_thumbnail(url=logo_url)

    return embed


def setup(bot):
    logger.info("Setting up food commands...")

    # Cache for restaurant list
    _vendor_cache = []

    async def get_vendor_list():
        nonlocal _vendor_cache
        if not _vendor_cache:
            _vendor_cache = await get_vendors()
        return _vendor_cache

    # Autocomplete for restaurant/vendor
    async def restaurant_autocomplete(interaction: discord.Interaction, current: str):
        try:
            vendors = await get_vendor_list()
            current_lower = current.lower()

            # Filter and sort by match quality
            matches = []
            for v in vendors:
                if current_lower in v.lower():
                    # Prioritize starts-with matches
                    priority = 0 if v.lower().startswith(current_lower) else 1
                    matches.append((priority, v))

            matches.sort()
            return [
                app_commands.Choice(name=v, value=v)
                for _, v in matches[:AUTOCOMPLETE_LIMIT]
            ]
        except Exception as e:
            logger.error(f"Restaurant autocomplete error: {e}")
            return []

    # Autocomplete for food search
    async def food_autocomplete(interaction: discord.Interaction, current: str):
        if not current or len(current) < 2:
            return []

        try:
            # Check if restaurant is already selected
            restaurant = None
            if hasattr(interaction, 'namespace') and hasattr(interaction.namespace, 'restaurant'):
                restaurant = interaction.namespace.restaurant

            results = await search_food(current, limit=AUTOCOMPLETE_LIMIT, vendor=restaurant)

            choices = []
            for item in results:
                vendor = item.get("vendor", "")
                name = item.get("name", "")
                food_id = item.get("id")

                # Format: "Item Name (Vendor)" - truncate if needed
                display = f"{name} ({vendor})"
                if len(display) > 100:
                    display = display[:97] + "..."

                choices.append(
                    app_commands.Choice(name=display, value=str(food_id))
                )

            return choices

        except Exception as e:
            logger.error(f"Food autocomplete error: {e}")
            return []

    @bot.tree.command(name="food", description="Look up nutrition info for restaurant foods")
    @app_commands.describe(
        name="Search for a food item (leave empty for random)",
        restaurant="Filter by restaurant (leave empty for all)"
    )
    @app_commands.autocomplete(name=food_autocomplete, restaurant=restaurant_autocomplete)
    async def food_cmd(interaction: discord.Interaction, name: Optional[str] = None, restaurant: Optional[str] = None):
        await interaction.response.defer()

        try:
            if name is None:
                # Random food (optionally from specific restaurant)
                food = await get_random_food(vendor=restaurant)
                if not food:
                    if restaurant:
                        return await interaction.followup.send(
                            f"❌ No food items found for **{restaurant}** with >300 calories."
                        )
                    return await interaction.followup.send(
                        "❌ No food data available. Run the food loader first!"
                    )
                embed = create_food_embed(food)
                if restaurant:
                    embed.title = f"🎲 Random from {restaurant}: {food.get('name', 'Unknown')}"
                else:
                    embed.title = f"🎲 Random: {food.get('name', 'Unknown')}"
                await interaction.followup.send(embed=embed)

            elif name.isdigit():
                # Lookup by ID (from autocomplete)
                food = await get_food_by_id(int(name))
                if not food:
                    return await interaction.followup.send("❌ Food item not found.")
                embed = create_food_embed(food)
                await interaction.followup.send(embed=embed)

            else:
                # Search by text (optionally filtered by restaurant)
                results = await search_food(name, limit=1, vendor=restaurant)
                if not results:
                    if restaurant:
                        return await interaction.followup.send(
                            f"❌ No results for **{name}** at **{restaurant}**."
                        )
                    return await interaction.followup.send(
                        f"❌ No results found for **{name}**. Try a different search!"
                    )
                embed = create_food_embed(results[0])
                await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Food command error: {e}")
            await interaction.followup.send("❌ An error occurred looking up food info.")
