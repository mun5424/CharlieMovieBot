"""
Discord commands for price checker
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from price_checker import db
from price_checker.scoring import (
    calculate_deal_score,
    get_deal_emoji,
    get_deal_color,
    format_deal_embed_fields,
    DEAL_CLASS_GREAT,
    DEAL_CLASS_INSANE,
)

logger = logging.getLogger(__name__)

# Categories for products
PRODUCT_CATEGORIES = [
    "CPU",
    "Video Card",
    "Motherboard",
    "RAM",
    "SSD",
    "HDD",
    "PSU",
    "Case",
    "Cooler",
    "Monitor",
    "Keyboard",
    "Mouse",
    "Headset",
    "Other",
]


def setup(bot):
    """Setup price checker commands"""

    # Admin-only permission decorator
    admin_only = app_commands.default_permissions(administrator=True)

    # ============== Product Commands ==============

    @bot.tree.command(name="price_add", description="Add a product to track")
    @admin_only
    @app_commands.describe(
        name="Product name (e.g., 'RTX 4070 Super')",
        category="Product category",
        brand="Brand (e.g., 'NVIDIA', 'AMD')",
        asin="Amazon ASIN",
        upc="Universal Product Code",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name=cat, value=cat) for cat in PRODUCT_CATEGORIES
    ])
    async def price_add_cmd(
        interaction: discord.Interaction,
        name: str,
        category: str,
        brand: str = None,
        asin: str = None,
        upc: str = None,
    ):
        await interaction.response.defer()

        # Check if product already exists
        if asin:
            existing = await db.get_product_by_identifier(asin=asin)
            if existing:
                return await interaction.followup.send(
                    f"⚠️ Product already exists: **{existing['name']}** (ID: {existing['id']})"
                )
        if upc:
            existing = await db.get_product_by_identifier(upc=upc)
            if existing:
                return await interaction.followup.send(
                    f"⚠️ Product already exists: **{existing['name']}** (ID: {existing['id']})"
                )

        product_id = await db.add_product(
            category=category,
            name=name,
            brand=brand,
            asin=asin,
            upc=upc,
        )

        embed = discord.Embed(
            title="✅ Product Added",
            description=f"**{name}**",
            color=0x2ECC71
        )
        embed.add_field(name="ID", value=str(product_id), inline=True)
        embed.add_field(name="Category", value=category, inline=True)
        if brand:
            embed.add_field(name="Brand", value=brand, inline=True)
        if asin:
            embed.add_field(name="ASIN", value=asin, inline=True)

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="price_search", description="Search tracked products")
    @app_commands.describe(
        query="Search query",
        category="Filter by category",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name=cat, value=cat) for cat in PRODUCT_CATEGORIES
    ])
    async def price_search_cmd(
        interaction: discord.Interaction,
        query: str,
        category: str = None,
    ):
        await interaction.response.defer()

        products = await db.search_products(query, category=category, limit=10)

        if not products:
            return await interaction.followup.send(f"❌ No products found for '{query}'")

        embed = discord.Embed(
            title=f"🔍 Products matching '{query}'",
            color=0x3498DB
        )

        for p in products[:10]:
            embed.add_field(
                name=f"{p['name']}",
                value=f"ID: {p['id']} | {p['category']} | {p.get('brand', 'N/A')}",
                inline=False
            )

        await interaction.followup.send(embed=embed)

    # ============== Offer Commands ==============

    @bot.tree.command(name="price_log", description="Log a price observation")
    @admin_only
    @app_commands.describe(
        product_id="Product ID",
        price="Price (USD)",
        source="Store/source (e.g., 'amazon', 'bestbuy')",
        condition="Product condition",
        seller_tier="Seller trust tier",
        url="Link to the offer",
    )
    @app_commands.choices(
        condition=[
            app_commands.Choice(name="New", value="new"),
            app_commands.Choice(name="Refurbished", value="refurb"),
            app_commands.Choice(name="Used", value="used"),
        ],
        seller_tier=[
            app_commands.Choice(name="First Party (Official Store)", value="first_party"),
            app_commands.Choice(name="Fulfilled (e.g., FBA)", value="fulfilled"),
            app_commands.Choice(name="Marketplace - Good Seller", value="marketplace_good"),
            app_commands.Choice(name="Marketplace - Unknown", value="marketplace_unknown"),
        ]
    )
    async def price_log_cmd(
        interaction: discord.Interaction,
        product_id: int,
        price: float,
        source: str,
        condition: str = "new",
        seller_tier: str = "first_party",
        url: str = None,
    ):
        await interaction.response.defer()

        # Verify product exists
        product = await db.get_product(product_id)
        if not product:
            return await interaction.followup.send(f"❌ Product ID {product_id} not found")

        # Add offer
        offer_id = await db.add_offer(
            product_id=product_id,
            source=source,
            price=price,
            condition=condition,
            seller_tier=seller_tier,
            url=url,
        )

        # Update daily snapshot
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        await db.update_daily_snapshot(
            product_id=product_id,
            condition=condition,
            day_utc=today,
            best_price=price,
            best_source=source,
            best_offer_id=offer_id,
        )

        embed = discord.Embed(
            title="📝 Price Logged",
            description=f"**{product['name']}**",
            color=0x3498DB
        )
        embed.add_field(name="Price", value=f"${price:.2f}", inline=True)
        embed.add_field(name="Source", value=source.title(), inline=True)
        embed.add_field(name="Condition", value=condition.title(), inline=True)
        if url:
            embed.add_field(name="Link", value=f"[View]({url})", inline=False)

        await interaction.followup.send(embed=embed)

    # ============== Deal Commands ==============

    @bot.tree.command(name="price_check", description="Check current deal score for a product")
    @app_commands.describe(
        product_id="Product ID",
        condition="Product condition",
    )
    @app_commands.choices(condition=[
        app_commands.Choice(name="New", value="new"),
        app_commands.Choice(name="Refurbished", value="refurb"),
        app_commands.Choice(name="Used", value="used"),
    ])
    async def price_check_cmd(
        interaction: discord.Interaction,
        product_id: int,
        condition: str = "new",
    ):
        await interaction.response.defer()

        product = await db.get_product(product_id)
        if not product:
            return await interaction.followup.send(f"❌ Product ID {product_id} not found")

        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        # Get or compute baseline
        baseline = await db.get_baseline(product_id, condition, today)
        if not baseline:
            baseline = await db.compute_baseline(product_id, condition, today)
            if not baseline:
                return await interaction.followup.send(
                    f"❌ Not enough price history for **{product['name']}** ({condition}). "
                    f"Need at least a few days of data."
                )

        # Get today's best price
        offers = await db.get_offers_for_product(product_id, condition=condition, limit=1)
        if not offers:
            return await interaction.followup.send(
                f"❌ No recent offers found for **{product['name']}** ({condition})"
            )

        latest = offers[0]
        price = latest['price']

        # Calculate score
        score, deal_class, details = calculate_deal_score(
            price=price,
            median_price=baseline['median_price'],
            mad_price=baseline['mad_price'],
            seller_tier=latest['seller_tier'],
            return_ok=bool(latest.get('return_ok', 1)),
            flags=latest.get('flags'),
        )

        emoji = get_deal_emoji(score, deal_class)
        color = get_deal_color(score, deal_class)
        fields = format_deal_embed_fields(
            price, baseline['median_price'], score, details, condition
        )

        embed = discord.Embed(
            title=f"{emoji} {product['name']}",
            color=color
        )
        embed.add_field(name="Current Price", value=fields['price'], inline=True)
        embed.add_field(name="Median (60d)", value=fields['median'], inline=True)
        embed.add_field(name="Savings", value=fields['savings'], inline=True)
        embed.add_field(name="Deal Score", value=fields['score'], inline=True)
        embed.add_field(name="Condition", value=fields['condition'], inline=True)
        embed.add_field(name="Data Points", value=str(baseline['n_days']), inline=True)

        if deal_class == DEAL_CLASS_INSANE:
            embed.set_footer(text="🔥 INSANE DEAL - Buy immediately!")
        elif deal_class == DEAL_CLASS_GREAT:
            embed.set_footer(text="💰 Great deal - Worth buying!")
        elif score >= 60:
            embed.set_footer(text="✨ Decent deal - Consider it")

        if latest.get('url'):
            embed.add_field(name="Link", value=f"[View Offer]({latest['url']})", inline=False)

        await interaction.followup.send(embed=embed)

    # ============== Watchlist Commands ==============

    @bot.tree.command(name="price_watch", description="Set up deal alerts for this channel")
    @admin_only
    @app_commands.describe(
        min_score="Minimum deal score to alert (default: 80)",
        max_per_day="Max alerts per day (default: 10)",
        category="Filter to specific category",
        condition="Filter to specific condition",
        role="Role to ping for alerts",
    )
    @app_commands.choices(
        category=[app_commands.Choice(name=cat, value=cat) for cat in PRODUCT_CATEGORIES],
        condition=[
            app_commands.Choice(name="New", value="new"),
            app_commands.Choice(name="Refurbished", value="refurb"),
            app_commands.Choice(name="Used", value="used"),
        ]
    )
    async def price_watch_cmd(
        interaction: discord.Interaction,
        min_score: int = 80,
        max_per_day: int = 10,
        category: str = None,
        condition: str = None,
        role: discord.Role = None,
    ):
        await interaction.response.defer()

        await db.add_watchlist(
            guild_id=str(interaction.guild_id),
            channel_id=str(interaction.channel_id),
            category=category or '',
            condition=condition or '',
            min_score=min_score,
            max_items_per_day=max_per_day,
            role_id_to_ping=str(role.id) if role else None,
        )

        embed = discord.Embed(
            title="✅ Deal Alerts Configured",
            description=f"This channel will receive deal alerts.",
            color=0x2ECC71
        )
        embed.add_field(name="Min Score", value=str(min_score), inline=True)
        embed.add_field(name="Max/Day", value=str(max_per_day), inline=True)
        embed.add_field(name="Category", value=category or "All", inline=True)
        embed.add_field(name="Condition", value=(condition or "All").title(), inline=True)
        if role:
            embed.add_field(name="Ping Role", value=role.mention, inline=True)

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="price_deals", description="Show today's deals")
    async def price_deals_cmd(interaction: discord.Interaction):
        await interaction.response.defer()

        watchlist = await db.get_watchlist(
            str(interaction.guild_id),
            str(interaction.channel_id)
        )

        if not watchlist:
            return await interaction.followup.send(
                "❌ No watchlist configured for this channel. Use `/price_watch` first."
            )

        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        deals = await db.get_deals_for_guild(
            str(interaction.guild_id),
            str(interaction.channel_id),
            today
        )

        if not deals:
            return await interaction.followup.send("📭 No deals found today matching your criteria.")

        embed = discord.Embed(
            title=f"🔥 Today's Deals ({len(deals)})",
            color=0xFF4500
        )

        for deal in deals[:10]:
            emoji = get_deal_emoji(deal['score'])
            discount_pct = deal['discount'] * 100
            embed.add_field(
                name=f"{emoji} {deal['name']} - Score: {deal['score']}",
                value=(
                    f"${deal['price']:.2f} (was ~${deal['median_price']:.2f}) "
                    f"| {discount_pct:.0f}% off | {deal['condition'].title()}"
                ),
                inline=False
            )

        await interaction.followup.send(embed=embed)

    logger.info("✅ Price checker commands loaded")
