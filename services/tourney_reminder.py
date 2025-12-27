import asyncio
import datetime
import pytz
import aiohttp
import logging
import discord
from discord.ext import tasks
from services.custom_reminder import CUSTOM_REMINDERS
import config

# Use zoneinfo for DST-aware scheduling (Python 3.9+)
try:
    from zoneinfo import ZoneInfo
    PACIFIC_TZ_SCHEDULE = ZoneInfo("America/Los_Angeles")
except ImportError:
    # Fallback for older Python - DST bug may still occur
    PACIFIC_TZ_SCHEDULE = None

PACIFIC_TZ = pytz.timezone("America/Los_Angeles")
UTC_TZ = pytz.timezone("UTC")
STARTGG_API_URL = "https://api.start.gg/gql/alpha"
logger = logging.getLogger(__name__)

# Shared timeout configuration - increased connect timeout for reliability
STARTGG_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)

# Retry configuration
MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds

# Module-level session (will be created on first use)
_session: aiohttp.ClientSession = None


async def get_session() -> aiohttp.ClientSession:
    """Get or create the shared aiohttp session"""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=STARTGG_TIMEOUT)
    return _session


async def close_session():
    """Close the shared session (call on shutdown)"""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None

def get_scheduled_time(hour: int, minute: int = 0) -> datetime.time:
    """
    Get a DST-aware time for task scheduling.
    Uses zoneinfo (Python 3.9+) for proper DST handling.
    Falls back to UTC calculation for older Python (may have DST issues).
    """
    if PACIFIC_TZ_SCHEDULE:
        # DST-aware scheduling - discord.py handles timezone correctly
        return datetime.time(hour=hour, minute=minute, tzinfo=PACIFIC_TZ_SCHEDULE)
    else:
        # Fallback: Calculate UTC time (not DST-aware at module load)
        pacific_now = datetime.datetime.now(PACIFIC_TZ)
        pacific_time = pacific_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        utc_time = pacific_time.astimezone(UTC_TZ)
        return utc_time.time()

# Scheduled times (DST-aware with Python 3.9+)
SCHEDULED_TIME_2PM = get_scheduled_time(14, 0)
SCHEDULED_TIME_1PM = get_scheduled_time(13, 0)
SCHEDULED_TIME_11AM = get_scheduled_time(11, 0)


# Tournament schedule by day of week
# Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
TOURNAMENT_SCHEDULE = {
    0: "Motivation Academy",     # Monday
    1: "Can Opener Series",      # Tuesday
    2: "TNS Street Fighter 6",   # Wednesday
    # 3: "ONi Arena: Street Fighter" # Thursday
    # 3: "FlyQuest Fight Series",  # Thursday - disabling flyquest as ONi is a guarenteed weekly.
}

TOURNAMENT_DESCRIPTION = {
    0: """
- Starts on **MONDAY at 5PM PACIFIC TIME**
- All matches will be best 3 out of 5 games (FT3)
- 10 min DQ timer for winners Round 1
- 5 min DQ timer for Round 2 onward/matches after
- IF you @ your opponent in DISCORD and the time has passed, @ a MOD and ask for a DQ

    """,
    1: """
- Starts on **TUESDAY at 3:30PM PACIFIC TIME**
- All matches will be best 3 out of 5 games (FT3)
- PLEASE MATCH YOUR STARTGG NAME TO YOUR DISCORD NAME OR ELSE YOU RUN THE RISK OF BEING DQ'd
    """,
    2: """
- **REGISTRATION WILL CLOSE AT 4:00PM PACIFIC TIME ON WEDNESDAY**
- Starts on **WEDNESDAY at 5PM PACIFIC TIME**
- All matches will be best 3 out of 5 games (FT3)
- Bracket is for PC/PS5/Switch 2
- Open to all players in the North America region (Canada, US, Mexico, DR, PR)
    """,
    3: """
- Starts on **THURSDAY at 5PM PACIFIC TIME**
- All matches will be best 3 out of 5 games (FT3)
- In the event of a tied Match declared by a "Double K.O." on the Game screen, the Match will not be scored and both Players will replay the tied Match with the same character selections and stage.
    """
}

bot_instance = None  # to be assigned in setup_reminder()

def get_day_and_today():
        # FOR TESTING - Manually set date (comment out for production)
    # test_date = datetime.datetime(2025, 7, 10, 14, 0, 0, tzinfo=PACIFIC_TZ)   # Thursday July 10, 2025
    # day = test_date.weekday()
    # today = test_date.date()

    # for prod
    day = datetime.datetime.now(PACIFIC_TZ).weekday()
    today = datetime.datetime.now(PACIFIC_TZ).date()
    return day, today



def _matches_tournament_series(node_name: str, series_name: str) -> bool:
    """Check if a tournament name matches our series using keyword matching."""
    node_lower = node_name.lower()
    series_lower = series_name.lower()

    # Extract key identifiers from series name
    if "tns" in series_lower:
        return "tns" in node_lower
    elif "can opener" in series_lower:
        return "can opener" in node_lower
    elif "motivation academy" in series_lower:
        return "motivation academy" in node_lower
    elif "flyquest" in series_lower:
        return "flyquest" in node_lower
    elif "oni" in series_lower:
        return "oni" in node_lower

    # Fallback: check if first two words match
    keywords = series_lower.split()[:2]
    return all(kw in node_lower for kw in keywords)


async def _search_tournaments_with_retry(session, headers: dict, query: dict) -> list:
    """Execute tournament search with retry logic."""
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            async with session.post(STARTGG_API_URL, headers=headers, json=query) as resp:
                data = await resp.json()
                return data.get("data", {}).get("tournaments", {}).get("nodes", [])

        except asyncio.TimeoutError:
            last_error = "timeout"
            logger.warning(f"Attempt {attempt + 1}/{MAX_RETRIES}: start.gg timeout")
        except aiohttp.ClientError as e:
            last_error = str(e)
            logger.warning(f"Attempt {attempt + 1}/{MAX_RETRIES}: network error - {e}")

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAY)

    logger.error(f"All {MAX_RETRIES} attempts failed. Last error: {last_error}")
    return []


async def find_todays_tournament(tournament_name: str, today: datetime.date = None):
    """
    Find today's tournament for the given tournament series.
    Returns (slug, name) tuple or (None, None) if not found.
    """
    if today is None:
        _, today = get_day_and_today()

    logger.info(f"Searching for '{tournament_name}' on {today}")

    headers = {
        "Authorization": f"Bearer {config.STARTGG_TOKEN}",
        "Content-Type": "application/json"
    }

    # Query all upcoming SF6 tournaments
    query = {
        "query": """
        query FindTournaments($perPage: Int!) {
          tournaments(query: {
            perPage: $perPage,
            filter: {
              past: false,
              videogameIds: [43868]
            }
          }) {
            nodes {
              name
              slug
              startAt
            }
          }
        }
        """,
        "variables": {"perPage": 50}
    }

    try:
        session = await get_session()
        nodes = await _search_tournaments_with_retry(session, headers, query)

        if not nodes:
            logger.warning("No tournaments returned from start.gg")
            return None, None

        logger.debug(f"Retrieved {len(nodes)} SF6 tournaments")

        # Find matching tournaments for our series
        matching = [n for n in nodes if _matches_tournament_series(n["name"], tournament_name)]
        logger.debug(f"Found {len(matching)} matching '{tournament_name}' tournaments")

        # Look for today's tournament
        for node in matching:
            event_date = datetime.datetime.fromtimestamp(node["startAt"], tz=PACIFIC_TZ).date()
            if event_date == today:
                logger.info(f"Found today's tournament: {node['name']}")
                return node["slug"], node["name"]

        # Log next upcoming if no match today
        if matching:
            future = sorted(
                [n for n in matching if datetime.datetime.fromtimestamp(n["startAt"], tz=PACIFIC_TZ).date() >= today],
                key=lambda x: x["startAt"]
            )
            if future:
                next_date = datetime.datetime.fromtimestamp(future[0]["startAt"], tz=PACIFIC_TZ).date()
                logger.info(f"No tournament today. Next: {future[0]['name']} on {next_date}")

        # Fallback: name-based search (single attempt, not multiple terms)
        logger.debug("Trying name-based search fallback")
        fallback_query = {
            "query": """
            query FindTournaments($perPage: Int!, $query: String!) {
              tournaments(query: {
                perPage: $perPage,
                filter: {
                  past: false,
                  name: $query,
                  videogameIds: [43868]
                }
              }) {
                nodes {
                  name
                  slug
                  startAt
                }
              }
            }
            """,
            "variables": {"perPage": 15, "query": tournament_name}
        }

        fallback_nodes = await _search_tournaments_with_retry(session, headers, fallback_query)
        for node in fallback_nodes:
            event_date = datetime.datetime.fromtimestamp(node["startAt"], tz=PACIFIC_TZ).date()
            if event_date == today:
                logger.info(f"Found via fallback: {node['name']}")
                return node["slug"], node["name"]

    except Exception as e:
        logger.error(f"Error searching for '{tournament_name}': {e}")

    logger.warning(f"No tournament found for {tournament_name} on {today}")
    return None, None


@tasks.loop(time=SCHEDULED_TIME_2PM)
async def check_todays_tournament(manual=False):
    """Check for today's tournament and send reminder to configured channels."""
    if bot_instance is None:
        logger.warning("Bot instance not set. Cannot send tournament reminder.")
        return

    day, today = get_day_and_today()
    logger.info(f"Tournament check for {today} (day {day})")

    # Check if there's a tournament scheduled for today
    if day not in TOURNAMENT_SCHEDULE:
        logger.debug(f"No tournament scheduled for day {day}")
        return

    tournament_name = TOURNAMENT_SCHEDULE[day]

    # Find today's specific tournament (already verified it's today)
    slug, name = await find_todays_tournament(tournament_name, today)

    if not slug or not name:
        logger.warning(f"No tournament found for {tournament_name} on {today}")
        return

    # Build the registration URL
    url = f"https://start.gg/{slug}"
    logger.info(f"Sending reminder for: {name}")

    # Send to all configured channels
    for channel_id in config.TOURNEY_CHANNEL_IDS:
        channel = bot_instance.get_channel(channel_id)
        if not channel:
            logger.warning(f"Channel {channel_id} not found")
            continue

        try:
            embed = discord.Embed(
                title=f"🥊 Tournament of the Day - **{name}**",
                description=f"Alright you gooners, **{name}** is happening TODAY! GO SIGN UP.",
                color=0xFF6B35,
                url=url
            )
            embed.add_field(
                name="🔥 Click This Link to Sign Up!",
                value=url,
                inline=False
            )
            embed.add_field(
                name="🚨 Tournament Rules",
                value=TOURNAMENT_DESCRIPTION.get(day, "See tournament page for rules."),
                inline=False
            )
            embed.set_footer(text="Click the title or use the link to register!")

            # Get role to ping (skip if manual test)
            role_id = config.TOURNEY_CHANNEL_ROLES.get(channel_id)
            role_mention = f"<@&{role_id}>" if role_id and not manual else ""

            await channel.send(content=role_mention, embed=embed)
            logger.info(f"Reminder sent to channel {channel_id}")

        except Exception as e:
            logger.error(f"Error sending to channel {channel_id}: {e}")


@tasks.loop(time=SCHEDULED_TIME_1PM)
async def check_custom_reminders():
    if bot_instance is None:
        logger.warning("Bot instance not set. Cannot run custom reminders.")
        return

    today = datetime.datetime.now(PACIFIC_TZ).date()
    logger.info(f"[Custom Reminder] Running at 1 PM PT on {today}")

    for reminder in CUSTOM_REMINDERS:
        event_date = reminder["date"]
        days_until = (event_date - today).days

        # for testing
        # days_until = 0

        if days_until == 3 or days_until == 0:
            logger.info(f"[Reminder] Sending for '{reminder['name']}' (in {days_until} days)")

            embed = discord.Embed(
                title=f"📣 Upcoming Tournament: {reminder['name']}",
                description=reminder["description"],
                color=0x800080,
                url=reminder["link"]
            )

            embed.add_field(name="📅 **Date:** " , value=event_date.strftime("%A, %B %d, %Y"), inline=True)
            embed.add_field(name="🔗 **Sign Up Link:** ", value=reminder["link"], inline=False)

            if days_until == 3:
                embed.set_footer(text="**TODAY WILL BE THE LAST DAY TO SIGN UP!** ")

            if days_until == 0:
                embed.set_footer(text="**TOURNAMENT WILL START AT 7PM TODAY. LOCK IN 🔒** ")

            for channel_id in config.TOURNEY_CHANNEL_IDS:
                channel = bot_instance.get_channel(channel_id)
                if channel:
                    role_id = config.TOURNEY_CHANNEL_ROLES.get(channel_id)
                    role_mention = f"<@&{role_id}>" if role_id else ""
                    try:
                        await channel.send(content=role_mention, embed=embed)
                        logger.info(f"Sent custom reminder to channel {channel_id}")
                    except Exception as e:
                        logger.error(f"Failed to send reminder to {channel_id}: {e}")
                else:
                    logger.warning(f"Channel {channel_id} not found.")



async def check_dodgers_game():
    """Check if Dodgers won a home game yesterday using MLB API"""
    try:
        # Get yesterday's date in Pacific time
        yesterday = datetime.datetime.now(PACIFIC_TZ) - datetime.timedelta(days=1)
        date_str = yesterday.strftime('%Y-%m-%d')

        # MLB Stats API endpoint for Dodgers games (Team ID: 119)
        url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId=119&startDate={date_str}&endDate={date_str}"

        session = await get_session()
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()

                if data.get('dates'):
                    games = data['dates'][0].get('games', [])
                    home_wins = []

                    for game in games:
                        # Check if game is finished
                        if game.get('status', {}).get('detailedState') == 'Final':
                            teams = game.get('teams', {})

                            # Only check if Dodgers are the HOME team
                            if teams.get('home', {}).get('team', {}).get('id') == 119:
                                home_score = teams.get('home', {}).get('score', 0)
                                away_score = teams.get('away', {}).get('score', 0)
                                is_winner = home_score > away_score

                                home_wins.append(is_winner)

                                if is_winner:
                                    logger.info(f"[Dodgers] Won home game on {date_str} ({home_score}-{away_score})")
                                else:
                                    logger.info(f"[Dodgers] Lost home game on {date_str} ({home_score}-{away_score})")

                    if home_wins:
                        return any(home_wins)
                    else:
                        logger.info(f"[Dodgers] No home games on {date_str}")
                        return None

                logger.info(f"[Dodgers] No games found for {date_str}")
                return None
            else:
                logger.error(f"[Dodgers] HTTP {response.status} from MLB API")
                return False

    except aiohttp.ClientError as e:
        logger.error(f"[Dodgers] Network error checking game: {e}")
        return None
    except Exception as e:
        logger.error(f"[Dodgers] Unexpected error checking game: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(f"[Dodgers] Full traceback: {traceback.format_exc()}")
        return None  # Fixed the return value


@tasks.loop(time=SCHEDULED_TIME_11AM)
async def check_dodgers_and_notify():
    """Check if Dodgers won a home game yesterday and send Panda Express notification at 11 AM PT"""
    if bot_instance is None:
        logger.warning("[Dodgers] Bot instance not set. Cannot send notifications.")
        return

    today = datetime.datetime.now(PACIFIC_TZ).date()
    logger.info(f"[Dodgers] Running daily check at 11 AM PT on {today}")

    # Check if Dodgers won a home game yesterday
    dodgers_result = await check_dodgers_game()

    if dodgers_result is True:
        logger.info("[Dodgers] Home victory confirmed! Sending Panda Express notifications")

        embed = discord.Embed(
            title="🐼 PANDA EXPRESS $7 DEAL TODAY! 🐼",
            description="**The Dodgers won at home last night!** Head to Panda Express for your victory meal! ⚾",
            color=0x005A9C  # Dodgers blue
        )

        embed.add_field(
            name="💰 **Deal Details**",
            value=(
                "• **Price:** $6.59 for a 2-Entree Plate \n"
                "• **Valid:** Today only\n"
                "• **Location:** All SoCal Panda Express locations\n"
                "• **How:** Use DODGERSWIN on your Panda Express App Order Checkout!"
            ),
            inline=False
        )

        embed.set_footer(text="This deal happens every day after a Dodgers HOME win! 💙")
        embed.set_thumbnail(url="https://content.sportslogos.net/logos/54/63/full/los_angeles_dodgers_logo_primary_2024_sportslogosnet-6270.png")

        # Send to all Panda Express channels with role pings
        for channel_id in config.PANDA_CHANNEL_IDS:
            channel = bot_instance.get_channel(channel_id)
            if channel:
                try:
                    # Get the role to ping for this channel
                    role_id = config.PANDA_CHANNEL_ROLES.get(channel_id)
                    role_mention = f"<@&{role_id}>" if role_id else ""

                    await channel.send(content=role_mention, embed=embed)
                    logger.info(f"[Dodgers] Panda Express notification sent to channel {channel_id}")
                except Exception as e:
                    logger.error(f"[Dodgers] Error sending to channel {channel_id}: {e}")
            else:
                logger.warning(f"[Dodgers] Channel {channel_id} not found")

    elif dodgers_result is False:
        logger.info("[Dodgers] Dodgers lost home game(s) yesterday. No Panda Express deal.")
    else:  # dodgers_result is None
        logger.info("[Dodgers] No home games yesterday. No Panda Express deal to check.")


def setup_reminder(bot):
    global bot_instance
    bot_instance = bot
    check_todays_tournament.start()  # runs daily at 2 PM PT
    check_custom_reminders.start()   # runs daily at 1 PM PT
    # check_dodgers_and_notify.start()  # runs daily at 11 AM PT

    # Register cleanup handler for the shared session
    if hasattr(bot, 'add_shutdown_handler'):
        bot.add_shutdown_handler(close_session)
