from __future__ import annotations

import datetime
import logging
import os

import aiohttp
import discord
from discord.ext import tasks

import config
from services.tourney_reminder import PACIFIC_TZ, get_scheduled_time, get_session

logger = logging.getLogger(__name__)

API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

# Scheduled check time (DST-aware, matches the Dodgers/Habit pattern)
SCHEDULED_TIME_1050AM = get_scheduled_time(10, 50)

bot_instance = None  # to be assigned in setup_reminder()

_lafc_team_id: int | None = None  # resolved once via the API and cached


def _api_football_headers() -> dict:
    return {"x-apisports-key": API_FOOTBALL_KEY}


async def _resolve_lafc_team_id() -> int | None:
    """Resolve and cache LAFC's API-Football team id via a name search."""
    global _lafc_team_id
    if _lafc_team_id is not None:
        return _lafc_team_id

    session = await get_session()
    try:
        async with session.get(
            f"{API_FOOTBALL_BASE_URL}/teams",
            headers=_api_football_headers(),
            params={"search": "LAFC"},
        ) as response:
            if response.status != 200:
                logger.error(f"[LAFC] HTTP {response.status} resolving team id")
                return None
            data = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"[LAFC] Network error resolving team id: {e}")
        return None

    for entry in data.get("response", []):
        team = entry.get("team", {})
        if "los angeles fc" in str(team.get("name", "")).lower():
            _lafc_team_id = team.get("id")
            logger.info(f"[LAFC] Resolved team id: {_lafc_team_id}")
            return _lafc_team_id

    logger.error("[LAFC] Could not find LAFC in API-Football team search results")
    return None


async def _first_goal_of_fixture(fixture_id: int) -> tuple[int | None, int | None]:
    """Return (scoring_team_id, minute_elapsed) for the fixture's first goal, or (None, None)."""
    session = await get_session()
    try:
        async with session.get(
            f"{API_FOOTBALL_BASE_URL}/fixtures/events",
            headers=_api_football_headers(),
            params={"fixture": fixture_id},
        ) as response:
            if response.status != 200:
                logger.error(f"[LAFC] HTTP {response.status} fetching events for fixture {fixture_id}")
                return None, None
            events_data = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"[LAFC] Network error fetching events for fixture {fixture_id}: {e}")
        return None, None

    goals = [e for e in events_data.get("response", []) if e.get("type") == "Goal"]
    if not goals:
        return None, None

    goals.sort(key=lambda e: (e.get("time", {}).get("elapsed") or 0, e.get("time", {}).get("extra") or 0))
    first = goals[0]
    return first.get("team", {}).get("id"), first.get("time", {}).get("elapsed")


async def check_lafc_first_half_opener() -> bool | None:
    """
    Check if LAFC played a home match yesterday and scored that match's
    opening goal within the first half.

    Returns:
        True: LAFC had a finished home match yesterday and scored the first goal in the 1st half
        False: LAFC had a finished home match yesterday, but didn't score the first goal in the 1st half
        None: no qualifying home match yesterday / unable to verify
    """
    if not API_FOOTBALL_KEY:
        logger.error("[LAFC] API_FOOTBALL_KEY not set; skipping check")
        return None

    team_id = await _resolve_lafc_team_id()
    if not team_id:
        return None

    yesterday = datetime.datetime.now(PACIFIC_TZ) - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")

    session = await get_session()
    try:
        async with session.get(
            f"{API_FOOTBALL_BASE_URL}/fixtures",
            headers=_api_football_headers(),
            params={"team": team_id, "date": date_str},
        ) as response:
            if response.status != 200:
                logger.error(f"[LAFC] HTTP {response.status} fetching fixtures for {date_str}")
                return None
            fixtures_data = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"[LAFC] Network error fetching fixtures: {e}")
        return None

    fixtures = fixtures_data.get("response", [])
    if not fixtures:
        logger.info(f"[LAFC] No fixtures found for {date_str}")
        return None

    for fixture in fixtures:
        teams = fixture.get("teams", {})
        if teams.get("home", {}).get("id") != team_id:
            continue  # only care about home matches

        status_short = fixture.get("fixture", {}).get("status", {}).get("short")
        if status_short not in ("FT", "AET", "PEN"):
            logger.info(f"[LAFC] Home match on {date_str} not finished yet (status={status_short})")
            continue

        fixture_id = fixture.get("fixture", {}).get("id")
        if not fixture_id:
            continue

        first_goal_team_id, first_goal_elapsed = await _first_goal_of_fixture(fixture_id)

        if first_goal_team_id is None:
            logger.info(f"[LAFC] Home match on {date_str} (fixture {fixture_id}) had no goals scored")
            return False

        scored_first = first_goal_team_id == team_id
        in_first_half = first_goal_elapsed is not None and first_goal_elapsed <= 45
        qualifies = scored_first and in_first_half

        logger.info(
            f"[LAFC] Home match on {date_str}: first goal by team {first_goal_team_id} "
            f"at minute {first_goal_elapsed} (qualifies={qualifies})"
        )

        return qualifies

    logger.info(f"[LAFC] No qualifying LAFC home match on {date_str}")
    return None


@tasks.loop(time=SCHEDULED_TIME_1050AM)
async def check_lafc_and_notify():
    """Check yesterday's LAFC home match and send the Ono Hawaiian BBQ notification at 10:50 AM PT."""
    if bot_instance is None:
        logger.warning("[LAFC] Bot instance not set. Cannot send notifications.")
        return

    today = datetime.datetime.now(PACIFIC_TZ).date()
    logger.info(f"[LAFC] Running daily check at 10:50 AM PT on {today}")

    result = await check_lafc_first_half_opener()

    if result is True:
        logger.info("[LAFC] First-half opener confirmed! Sending Ono Hawaiian BBQ notifications")

        embed = discord.Embed(
            title="🍗 ONO HAWAIIAN BBQ LAFC DEAL TODAY! 🍗",
            description=(
                "**LAFC scored the opening goal in the first half of their home match last night!**\n"
                "Registered users get a Chicken Plate Lunch for $5.99 online. ⚽"
            ),
            color=0xC39E6D,
        )

        embed.add_field(
            name="🎟️ **How to Redeem**",
            value=(
                "Use code `LAFCSCORES` at checkout online.\n\n"
                "Valid today only for registered Ono Hawaiian BBQ online ordering accounts."
            ),
            inline=False,
        )

        embed.set_footer(text="Deal triggers whenever LAFC scores first in the 1st half of a HOME match!")

        lafc_channel_ids = getattr(config, "LAFC_CHANNEL_IDS", config.PANDA_CHANNEL_IDS)
        lafc_channel_roles = getattr(config, "LAFC_CHANNEL_ROLES", config.PANDA_CHANNEL_ROLES)

        for channel_id in lafc_channel_ids:
            channel = bot_instance.get_channel(channel_id)
            if channel:
                try:
                    role_id = lafc_channel_roles.get(channel_id)
                    role_mention = f"<@&{role_id}>" if role_id else ""

                    await channel.send(content=role_mention, embed=embed)
                    logger.info(f"[LAFC] Notification sent to channel {channel_id}")
                except Exception as e:
                    logger.error(f"[LAFC] Error sending to channel {channel_id}: {e}")
            else:
                logger.warning(f"[LAFC] Channel {channel_id} not found")

    elif result is False:
        logger.info("[LAFC] LAFC home match yesterday didn't qualify. No Ono Hawaiian BBQ deal.")
    else:
        logger.info("[LAFC] No qualifying LAFC home match yesterday. No deal to check.")


def setup_reminder(bot):
    global bot_instance
    bot_instance = bot
    check_lafc_and_notify.start()  # runs daily at 10:50 AM PT
