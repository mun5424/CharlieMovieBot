import datetime
import pytz
import aiohttp
import logging
import discord
from discord.ext import tasks
import config

PACIFIC_TZ = pytz.timezone("America/Los_Angeles")
UTC_TZ = pytz.timezone("UTC")
STARTGG_API_URL = "https://api.start.gg/gql/alpha"
logger = logging.getLogger(__name__)

# Calculate what time 2 PM Pacific is in UTC
def get_utc_time_for_pacific_2pm():
    """Get the UTC time that corresponds to 2 PM Pacific"""
    # Create a Pacific time for 2 PM today
    pacific_now = datetime.datetime.now(PACIFIC_TZ)

    # update this to 2pm
    pacific_2pm = pacific_now.replace(hour=14, minute=0, second=0, microsecond=0)
    
    # Convert to UTC
    utc_2pm = pacific_2pm.astimezone(UTC_TZ)
    
    return utc_2pm.time()

# This will calculate the correct UTC time automatically
UTC_TIME_FOR_2PM_PACIFIC = get_utc_time_for_pacific_2pm()

# Tournament schedule by day of week
# Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
TOURNAMENT_SCHEDULE = {
    0: "Motivation Academy",     # Monday
    1: "Can Opener Series",      # Tuesday  
    2: "TNS Street Fighter 6",   # Wednesday
    3: "FlyQuest Fight Series",  # Thursday
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
- Ladder: 5-7 PM PST
- Main Bracket: 7:05 PM PST
- All matches will be best 3 out of 5 games (FT3)
- Separate ladder/bracket for West Coast and East Coast
- Top 8 players from each ladder will advance to the same main bracket
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
                                title=f"🥊 Online Tournament of the Day - **{name}**",
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


def setup_reminder(bot):
    global bot_instance
    bot_instance = bot
    check_todays_tournament.start()