import datetime
import pytz
import aiohttp
import logging
import discord
from discord.ext import tasks
from custom_reminder import CUSTOM_REMINDERS
import config

PACIFIC_TZ = pytz.timezone("America/Los_Angeles")
UTC_TZ = pytz.timezone("UTC")
STARTGG_API_URL = "https://api.start.gg/gql/alpha"
logger = logging.getLogger(__name__)

# Calculate what time 2 PM Pacific is in UTC
def get_utc_time_for_pacific(hour=14,minute=0):
    """Get the UTC time that corresponds to 2 PM Pacific"""
    # Create a Pacific time for 2 PM today
    pacific_now = datetime.datetime.now(PACIFIC_TZ)

    # update this to 2pm
    pacific_2pm = pacific_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Convert to UTC
    utc_2pm = pacific_2pm.astimezone(UTC_TZ)
    
    return utc_2pm.time()

# This will calculate the correct UTC time automatically
UTC_TIME_FOR_2PM_PACIFIC = get_utc_time_for_pacific()
UTC_TIME_FOR_1PM_PACIFIC = get_utc_time_for_pacific(13)


# Tournament schedule by day of week
# Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
TOURNAMENT_SCHEDULE = {
    0: "Motivation Academy",     # Monday
    1: "Can Opener Series",      # Tuesday  
    2: "TNS Street Fighter 6",   # Wednesday
    3: "ONi Arena: Street Fighter" # Thursday
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
- In the event of a tied Match declared by a “Double K.O.” on the Game screen, the Match will not be scored and both Players will replay the tied Match with the same character selections and stage.
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



async def find_todays_tournament(tournament_name):
    """Find today's tournament for the given tournament series"""
    logger.info(f"Searching for today's tournament: '{tournament_name}'")
    
    _ , today = get_day_and_today() 

    # Strategy 1: Search all SF6 tournaments and filter by name
    logger.info("Strategy 1: Searching all Street Fighter 6 tournaments")
    
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
        "variables": {"perPage": 50}  # Increased to catch more tournaments
    }

    headers = {
        "Authorization": f"Bearer {config.STARTGG_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(STARTGG_API_URL, headers=headers, json=query) as resp:
                data = await resp.json()
                logger.info(f"Found {len(data.get('data', {}).get('tournaments', {}).get('nodes', []))} total SF6 tournaments")

        nodes = data.get("data", {}).get("tournaments", {}).get("nodes", [])
        
        # Filter tournaments that match our tournament series
        matching_tournaments = []
        tournament_name_lower = tournament_name.lower()
        
        for node in nodes:
            node_name_lower = node["name"].lower()
            
            # Check if this tournament belongs to our series
            is_match = False
            if "tns" in tournament_name_lower and "tns" in node_name_lower:
                is_match = True
            elif "can opener" in tournament_name_lower and "can opener" in node_name_lower:
                is_match = True
            elif "motivation academy" in tournament_name_lower and "motivation academy" in node_name_lower:
                is_match = True
            elif "flyquest" in tournament_name_lower and "flyquest" in node_name_lower:
                is_match = True       
            
            if is_match:
                matching_tournaments.append(node)
                logger.info(f"Found matching tournament: {node['name']} (slug: {node['slug']})")
        
        logger.info(f"Found {len(matching_tournaments)} matching tournaments for '{tournament_name}'")
        
        # Look for today's tournament
        for node in matching_tournaments:
            event_date = datetime.datetime.fromtimestamp(node["startAt"], tz=PACIFIC_TZ).date()
            logger.info(f"Tournament: {node['name']} on {event_date} (today: {today})")
            if event_date == today:
                logger.info(f"Found today's tournament: {node['name']} -> {node['slug']}")
                return node["slug"], node["name"]
        
        # If no tournament found for today, log upcoming tournaments for debugging
        if matching_tournaments:
            logger.info(f"No tournament for today, but found {len(matching_tournaments)} upcoming tournaments:")
            future_tournaments = [node for node in matching_tournaments 
                                if datetime.datetime.fromtimestamp(node["startAt"], tz=PACIFIC_TZ).date() >= today]
            
            # Sort by date
            future_tournaments.sort(key=lambda x: datetime.datetime.fromtimestamp(x["startAt"], tz=PACIFIC_TZ).date())
            
            for node in future_tournaments[:5]:  # Show next 5
                event_date = datetime.datetime.fromtimestamp(node["startAt"], tz=PACIFIC_TZ).date()
                logger.info(f"  - {node['name']} on {event_date}")
        
        # Strategy 2: Try name-based search as fallback
        logger.info("Strategy 2: Trying name-based search as fallback")
        
        search_terms = [tournament_name]
        if "TNS" in tournament_name:
            search_terms = ["TNS", "TNS Street Fighter 6", tournament_name]
        elif "Can Opener" in tournament_name:
            search_terms = ["Can Opener", "Can Opener Series", tournament_name]
        elif "Motivation Academy" in tournament_name:
            search_terms = ["Motivation Academy", tournament_name]
        elif "FlyQuest" in tournament_name:
            search_terms = ["FlyQuest", "FlyQuest Fight Series", tournament_name]
        elif "ONi" in tournament_name:
            search_terms = ["ONi", "ONi Arena", tournament_name]
        
        for search_term in search_terms:
            logger.info(f"Trying name-based search term: '{search_term}'")
            
            query = {
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
                "variables": {"perPage": 15, "query": search_term}
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(STARTGG_API_URL, headers=headers, json=query) as resp:
                    data = await resp.json()
                    logger.info(f"Name-based search response for '{search_term}': Found {len(data.get('data', {}).get('tournaments', {}).get('nodes', []))} tournaments")

            nodes = data.get("data", {}).get("tournaments", {}).get("nodes", [])
            
            # Look for today's tournament
            for node in nodes:
                event_date = datetime.datetime.fromtimestamp(node["startAt"], tz=PACIFIC_TZ).date()
                logger.info(f"Name-based result: {node['name']} on {event_date}")
                if event_date == today:
                    logger.info(f"Found today's tournament via name search: {node['name']} -> {node['slug']}")
                    return node["slug"], node["name"]
                
    except Exception as e:
        logger.error(f"Error searching for tournament '{tournament_name}': {e}")
    
    logger.warning(f"No tournament found for today ({today}) for series '{tournament_name}'")
    return None, None


@tasks.loop(time=UTC_TIME_FOR_2PM_PACIFIC)
async def check_todays_tournament(manual=False):
    if bot_instance is None:
        logger.warning("Bot instance is not set. Cannot send tournament reminder.")
        return

    day, today = get_day_and_today() 

    logger.info(f"Checking tournaments for day {day} ({today})")
    
    # Check if there's a tournament scheduled for today
    if day not in TOURNAMENT_SCHEDULE:
        logger.info(f"No tournament scheduled for day {day}")
        return
    
    tournament_name = TOURNAMENT_SCHEDULE[day]
    logger.info(f"Today's tournament series: {tournament_name}")
    
    # Find today's specific tournament
    slug, name = await find_todays_tournament(tournament_name)
    
    if not slug:
        logger.warning(f"No tournament found for {tournament_name} on {today}")
        return

    # Final validation before API call
    if not name:
        logger.error(f"Tournament name is None after processing. Cannot proceed.")
        return

    logger.info(f"Proceeding with tournament check: {name} (slug: {slug})")
    url = f"https://start.gg/{slug}"

    headers = {
        "Authorization": f"Bearer {config.STARTGG_TOKEN}",
        "Content-Type": "application/json"
    }

    query = {
        "query": """
        query TournamentQuery($slug: String!) {
          tournament(slug: $slug) {
            events {
              name
              startAt
            }
          }
        }
        """,
        "variables": {"slug": slug}
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(STARTGG_API_URL, headers=headers, json=query) as resp:
                data = await resp.json()
                logger.info(f"Tournament query response: {data}")
                
                if "errors" in data:
                    logger.error(f"Start.gg API error: {data['errors']}")
                    return
    
        # fetch tournament data
        tournament_data = data.get("data", {}).get("tournament")
        if not tournament_data or "events" not in tournament_data:
            logger.warning("Tournament data missing or malformed in API response.")
            return

        events = tournament_data["events"]
        logger.info(f"Found {len(events)} events in tournament")

        for event in events:
            event_date = datetime.datetime.fromtimestamp(event["startAt"], tz=PACIFIC_TZ).date()
            logger.info(f"Event: {event['name']} on {event_date}")
            if event_date == today:
                logger.info(f"Found today's event: {event['name']}")
                
                # Send to all channels
                for channel_id in config.TOURNEY_CHANNEL_IDS:
                    channel = bot_instance.get_channel(channel_id)
                    if channel:
                        try:
                            embed = discord.Embed(
                                title=f"🥊 Tournament of the Day - **{name}**",
                                description=f"Alright you gooners, **{name}** is happening TODAY! GO SIGN UP.",
                                color=0xFF6B35,  # Orange color
                                url=url
                            )
                            embed.add_field(
                                name="🔥 Click This Link to Sign Up!",
                                value=f"{url}",
                                inline=False
                            )
                            embed.add_field(
                                name="🚨 Tournament Rules",
                                value=f"{TOURNAMENT_DESCRIPTION[day]}",
                                inline=False
                            )

                            embed.set_footer(text="Click the title or use the link to register!")
                            
                            role_id = config.TOURNEY_CHANNEL_ROLES.get(channel_id)
                            if role_id and not manual:
                                role_mention = f"<@&{role_id}>"
                            else:
                                role_mention = ""  # No role to ping for this channel

                            await channel.send(content=role_mention, embed=embed)
                            logger.info(f"Tournament reminder sent successfully to channel {channel_id}")
                            
                        except Exception as e:
                            logger.error(f"Error sending message to channel {channel_id}: {e}")
                            
                    else:
                        logger.warning(f"Channel {channel_id} not found. Check your config.TOURNEY_CHANNEL_IDS.")
                
                return

        logger.info("No event scheduled for today. Time for Distraction Hour.")
        
    except Exception as e:
        logger.error(f"Error processing tournament data: {e}")


@tasks.loop(time=UTC_TIME_FOR_1PM_PACIFIC)
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


# Use your existing function to calculate UTC time for 11 AM Pacific
UTC_TIME_FOR_11AM_PACIFIC = get_utc_time_for_pacific(hour=11, minute=26)

async def check_dodgers_game():
    """Check if Dodgers won a home game yesterday using MLB API"""
    try:
        # Get yesterday's date in Pacific time
        yesterday = datetime.datetime.now(PACIFIC_TZ) - datetime.timedelta(days=1)
        date_str = yesterday.strftime('%Y-%m-%d')

        # MLB Stats API endpoint for Dodgers games (Team ID: 119)
        url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId=119&startDate={date_str}&endDate={date_str}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
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


@tasks.loop(time=UTC_TIME_FOR_11AM_PACIFIC)
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
    check_todays_tournament.start() # ⏰ now runs daily at 2 PM PST
    check_custom_reminders.start() # checks daily at 1PM PST
    check_dodgers_and_notify.start() # ⚾ checks daily at 11 AM PST