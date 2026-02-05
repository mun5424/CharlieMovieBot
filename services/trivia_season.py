"""
Trivia season scheduler - automatic monthly resets and last-week reminders.

- Resets scores on the 7th of every month (archives to Hall of Fame first)
- Sends daily standings reminders during the 7 days before reset
"""

import datetime
import os
import logging
import discord
from discord.ext import tasks

try:
    from zoneinfo import ZoneInfo
    PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
except ImportError:
    import pytz
    PACIFIC_TZ = pytz.timezone("America/Los_Angeles")

logger = logging.getLogger(__name__)

SEASON_RESET_DAY = 7

bot_instance = None


def get_next_reset_date(from_date=None):
    """Get the next season reset date (7th of the month).

    If today is before the 7th, returns this month's 7th.
    If today is the 7th or later, returns next month's 7th.
    """
    if from_date is None:
        from_date = datetime.datetime.now(PACIFIC_TZ).date()

    if from_date.day < SEASON_RESET_DAY:
        return from_date.replace(day=SEASON_RESET_DAY)

    if from_date.month == 12:
        return from_date.replace(year=from_date.year + 1, month=1, day=SEASON_RESET_DAY)
    else:
        return from_date.replace(month=from_date.month + 1, day=SEASON_RESET_DAY)


def get_season_info():
    """Get current season info for milestone reminders.

    Returns (next_reset_date, days_until_reset).
    """
    today = datetime.datetime.now(PACIFIC_TZ).date()
    next_reset = get_next_reset_date(today)
    days_until = (next_reset - today).days
    return next_reset, days_until


def _get_season_name(data_manager, guild_id):
    """Generate a season name. Uses custom title if set, otherwise defaults to month/year."""
    hof_data = data_manager.get_hall_of_fame(guild_id)
    season_number = len(hof_data) + 1
    custom_title = data_manager.get_season_title(guild_id)
    if custom_title:
        return f"Season {season_number} - {custom_title}"
    now = datetime.datetime.now(PACIFIC_TZ)
    return f"Season {season_number} - {now.strftime('%B %Y')}"


def _get_trivia_channel_ids():
    """Get configured trivia channel IDs from config"""
    try:
        import config
        return getattr(config, 'TRIVIA_CHANNEL_IDS', [])
    except ImportError:
        return []


def _get_all_guild_ids_with_data(data_manager):
    """Get all guild IDs that have trivia data files on disk"""
    guild_ids = []
    data_dir = data_manager.data_directory
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            if f.startswith("server_") and f.endswith(".json"):
                guild_id = f[len("server_"):-len(".json")]
                guild_ids.append(guild_id)
    return guild_ids


def _build_standings_text(leaderboard, limit=5):
    """Build formatted standings text from leaderboard data"""
    medals = ["👑", "🥈", "🥉", "4️⃣", "5️⃣"]
    text = ""
    for i, (user_id, stats) in enumerate(leaderboard[:limit]):
        accuracy = (stats.correct_answers / stats.questions_answered * 100) if stats.questions_answered > 0 else 0
        medal = medals[i] if i < len(medals) else f"#{i+1}"
        text += f"{medal} **{stats.username}** - {stats.total_score:,} pts ({accuracy:.0f}%)\n"
    return text


def _get_scheduled_time(hour, minute=0):
    """Get a DST-aware time for task scheduling"""
    if isinstance(PACIFIC_TZ, ZoneInfo):
        return datetime.time(hour=hour, minute=minute, tzinfo=PACIFIC_TZ)
    else:
        now = datetime.datetime.now(PACIFIC_TZ)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        utc_target = target.astimezone(pytz.timezone("UTC"))
        return utc_target.time()


SCHEDULED_TIME_NOON = _get_scheduled_time(12, 0)


@tasks.loop(time=SCHEDULED_TIME_NOON)
async def check_trivia_season():
    """Daily check at noon PT for season reset or last-week reminders"""
    if bot_instance is None:
        return

    trivia_cog = bot_instance.get_cog('TriviaCog')
    if not trivia_cog:
        logger.warning("[Trivia Season] TriviaCog not found")
        return

    data_manager = trivia_cog.data_manager
    today = datetime.datetime.now(PACIFIC_TZ).date()

    logger.info(f"[Trivia Season] Daily check on {today}")

    if today.day == SEASON_RESET_DAY:
        await _perform_season_reset(data_manager, today)
    else:
        next_reset = get_next_reset_date(today)
        days_until = (next_reset - today).days
        if 1 <= days_until <= 7:
            await _send_season_reminder(data_manager, days_until)


async def _perform_season_reset(data_manager, today):
    """Perform the monthly season reset for all servers with trivia data"""
    guild_ids = _get_all_guild_ids_with_data(data_manager)
    channel_ids = _get_trivia_channel_ids()

    if not guild_ids:
        logger.info("[Trivia Season] No guild data found, skipping reset")
        return

    for guild_id in guild_ids:
        try:
            server_data = data_manager.load_server_data(guild_id)
            if not server_data:
                continue

            guild = bot_instance.get_guild(int(guild_id))
            server_name = guild.name if guild else f"Server {guild_id}"

            # 1. Create and save season snapshot (before reset)
            season_name = _get_season_name(data_manager, guild_id)
            snapshot = data_manager.create_season_snapshot(guild_id, season_name, server_name)
            data_manager.save_season_snapshot(guild_id, snapshot)

            # 2. Build announcement embed (before reset, so standings are still available)
            leaderboard = data_manager.get_server_leaderboard(guild_id, 5)

            embed = discord.Embed(
                title="🏆 SEASON COMPLETE! 🏆",
                description=f"**{season_name}** has concluded!\n"
                            f"All scores have been archived to the Hall of Fame.",
                color=discord.Color.gold()
            )

            if leaderboard:
                standings_text = _build_standings_text(leaderboard)
                embed.add_field(name="🏅 Final Standings", value=standings_text, inline=False)

            next_reset = get_next_reset_date(today)
            embed.add_field(
                name="🆕 New Season",
                value="A new season has begun! All scores have been reset.\n"
                      "Use `/trivia` to start building your new legacy!",
                inline=False
            )
            embed.set_footer(
                text=f"View past seasons with /hall_of_fame | Next reset: {next_reset.strftime('%B %d, %Y')}"
            )

            # 3. Reset scores and clear used season title
            data_manager.reset_server_scores(guild_id)
            data_manager.clear_season_title(guild_id)

            # 4. Send announcements to configured channels in this guild
            for channel_id in channel_ids:
                channel = bot_instance.get_channel(channel_id)
                if channel and str(channel.guild.id) == guild_id:
                    try:
                        await channel.send(embed=embed)
                        logger.info(f"[Trivia Season] Reset announcement sent to channel {channel_id}")
                    except Exception as e:
                        logger.error(f"[Trivia Season] Error sending to channel {channel_id}: {e}")

            logger.info(f"[Trivia Season] Season reset complete for guild {guild_id}: {season_name}")

        except Exception as e:
            logger.error(f"[Trivia Season] Error resetting guild {guild_id}: {e}")


async def _send_season_reminder(data_manager, days_left):
    """Send daily standings reminder during the last week before reset"""
    channel_ids = _get_trivia_channel_ids()

    if not channel_ids:
        return

    for channel_id in channel_ids:
        channel = bot_instance.get_channel(channel_id)
        if not channel:
            continue

        guild_id = str(channel.guild.id)

        try:
            leaderboard = data_manager.get_server_leaderboard(guild_id, 5)
            if not leaderboard:
                continue

            server_name = channel.guild.name

            if days_left == 1:
                title = "⚠️ LAST DAY! Season Resets Tomorrow!"
                description = f"**{server_name}** - This is your FINAL chance to climb the rankings!"
                color = discord.Color.red()
            elif days_left <= 3:
                title = f"🔥 {days_left} Days Left in the Season!"
                description = f"**{server_name}** - The clock is ticking! Make every question count!"
                color = discord.Color.orange()
            else:
                title = f"📊 {days_left} Days Left in the Season"
                description = f"**{server_name}** - Here's where everyone stands:"
                color = discord.Color.blue()

            embed = discord.Embed(title=title, description=description, color=color)

            standings_text = _build_standings_text(leaderboard)
            embed.add_field(name="🏆 Current Standings", value=standings_text, inline=False)

            next_reset = get_next_reset_date()
            embed.set_footer(
                text=f"Season resets on {next_reset.strftime('%B %d, %Y')} | Use /trivia to play!"
            )

            await channel.send(embed=embed)
            logger.info(f"[Trivia Season] Reminder sent to channel {channel_id} ({days_left} days left)")

        except Exception as e:
            logger.error(f"[Trivia Season] Error sending reminder to {channel_id}: {e}")


def setup_trivia_season(bot):
    """Initialize the trivia season scheduler"""
    global bot_instance
    bot_instance = bot
    check_trivia_season.start()
    logger.info("[Trivia Season] Season scheduler started (daily at noon PT)")
