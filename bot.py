import asyncio
import signal
import sys
from typing import Optional

import discord
from discord.ext import commands

# Local imports
import config
from logging_utils import setup_logging, log_system_info
from performance import OptimizedBot


class BotManager:
    """Manages bot lifecycle and command loading"""
    
    def __init__(self):
        self.logger = setup_logging(config)
        self.bot: Optional[OptimizedBot] = None
        self.setup_complete = False
        
        # Log system information
        log_system_info(self.logger, {
            'Bot Version': getattr(config, 'BOT_VERSION', '1.0.0'),
            'Environment': 'Production' if not getattr(config, 'DEBUG', False) else 'Development'
        })
    
    async def initialize_bot(self) -> bool:
        """Initialize the bot with all components"""
        try:
            # Create optimized bot instance
            self.bot = OptimizedBot(config)
            
            # Load commands
            if not await self._load_commands():
                return False
            
            # Setup signal handlers
            self._setup_signal_handlers()
            
            self.setup_complete = True
            self.logger.info("🚀 Bot initialization complete")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Bot initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def _load_commands(self) -> bool:
        """Load all command modules"""
        try:
            # Import command modules
            from commands import general, watchlist, anime, gamelog
            from trivia.trivia import TriviaCog

            # Load traditional commands
            general.setup(self.bot)
            watchlist.setup(self.bot)
            anime.setup(self.bot)
            gamelog.setup(self.bot)
            self.logger.info("✅ Commands loaded (general, watchlist, anime, gamelog)")
            
            # Load trivia cog
            trivia_cog = TriviaCog(self.bot)
            await self.bot.add_cog(trivia_cog)
            self.logger.info("✅ Trivia cog loaded")
            
            # Add shutdown handler for trivia data
            if hasattr(trivia_cog, 'data_manager'):
                self.bot.add_shutdown_handler(
                    lambda: trivia_cog.data_manager.force_save_all()
                )
            
            # Log registered commands
            registered_commands = [cmd.name for cmd in self.bot.tree.get_commands()]
            self.logger.info(f"📋 Registered commands: {registered_commands}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Error loading commands: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def sync_commands(self):
        """Sync slash commands with Discord"""
        try:
            if hasattr(config, 'GUILD_IDS_TEST') and config.GUILD_IDS_TEST:
                # Guild-specific sync (faster for testing)
                self.logger.info("🔄 Syncing commands to test guilds...")
                
                for guild_id in config.GUILD_IDS_TEST:
                    guild = discord.Object(id=guild_id)
                    
                    # Copy global commands to guild and sync
                    self.bot.tree.copy_global_to(guild=guild)
                    synced = await self.bot.tree.sync(guild=guild)
                    
                    command_names = [cmd.name for cmd in synced]
                    self.logger.info(f"✅ Guild {guild_id} synced {len(synced)} commands: {command_names}")
                    
            else:
                # Global sync (takes up to 1 hour to update)
                self.logger.info("🔄 Syncing commands globally...")
                synced = await self.bot.tree.sync()
                
                command_names = [cmd.name for cmd in synced]
                self.logger.info(f"✅ Globally synced {len(synced)} commands: {command_names}")
                
        except Exception as e:
            self.logger.error(f"❌ Failed to sync commands: {e}")
    
    async def load_additional_components(self):
        """Load additional bot components"""
        try:
            # Initialize SQLite database
            try:
                from db import get_db, close_db
                await get_db()
                # Register cleanup on shutdown
                self.bot.add_shutdown_handler(close_db)
                self.logger.info("✅ SQLite database initialized")
            except Exception as e:
                self.logger.error(f"❌ Failed to initialize SQLite database: {e}")

            # Pre-warm TMDB session to avoid first-request latency
            try:
                from clients.tmdb import warmup_session, close_session
                await warmup_session()
                # Register cleanup on shutdown
                self.bot.add_shutdown_handler(close_session)
                self.logger.info("✅ TMDB session pre-warmed")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to pre-warm TMDB session: {e}")

            # Pre-warm Jikan session for anime commands
            try:
                from clients.jikan import warmup_session as jikan_warmup, close_session as jikan_close
                await jikan_warmup()
                # Register cleanup on shutdown
                self.bot.add_shutdown_handler(jikan_close)
                self.logger.info("✅ Jikan session pre-warmed")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to pre-warm Jikan session: {e}")

            # Pre-warm IGDB session for gamelog commands
            try:
                from clients.igdb import warmup_session as igdb_warmup, close_session as igdb_close
                await igdb_warmup()
                # Register cleanup on shutdown
                self.bot.add_shutdown_handler(igdb_close)
                self.logger.info("✅ IGDB session pre-warmed")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to pre-warm IGDB session: {e}")

            # Load tournament reminder if available
            try:
                from services.tourney_reminder import setup_reminder
                setup_reminder(self.bot)
                self.logger.info("✅ Tournament reminder loaded")
            except ImportError:
                self.logger.info("ℹ️ Tournament reminder not available")
            except Exception as e:
                self.logger.error(f"❌ Failed to load tournament reminder: {e}")

            # Add other components here as needed

        except Exception as e:
            self.logger.error(f"❌ Error loading additional components: {e}")
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            self.logger.info(f"📡 Received signal {signum}, initiating shutdown...")
            
            # Create shutdown task
            async def shutdown():
                if self.bot:
                    await self.bot.close()
            
            # Schedule shutdown
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(shutdown())
            except RuntimeError:
                # No event loop running, exit immediately
                sys.exit(0)
        
        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def start_bot(self) -> bool:
        """Start the bot with retry logic"""
        if not self.setup_complete:
            self.logger.error("❌ Bot not initialized properly")
            return False
        
        # Validate token
        if not hasattr(config, 'DISCORD_TOKEN') or not config.DISCORD_TOKEN:
            self.logger.error("❌ DISCORD_TOKEN not found in config!")
            return False
        
        # Start bot with retry logic
        max_retries = getattr(config, 'MAX_STARTUP_RETRIES', 3)
        retry_delay = getattr(config, 'STARTUP_RETRY_DELAY', 5)
        
        for attempt in range(max_retries):
            try:
                self.logger.info(f"🚀 Starting bot (attempt {attempt + 1}/{max_retries})...")
                
                # Add custom ready event for command syncing
                @self.bot.event
                async def on_ready():
                    # Call the original on_ready from OptimizedBot
                    await OptimizedBot.on_ready(self.bot)
                    
                    # Sync commands
                    await self.sync_commands()
                    
                    # Load additional components
                    await self.load_additional_components()
                    
                    self.logger.info("🎉 Bot is ready and operational!")
                
                # Start the bot
                await self.bot.start(config.DISCORD_TOKEN)
                return True  # Success
                
            except discord.LoginFailure as e:
                self.logger.error(f"❌ Invalid Discord token: {e}")
                return False  # Don't retry on auth failures
                
            except Exception as e:
                self.logger.error(f"❌ Bot startup failed (attempt {attempt + 1}/{max_retries}): {e}")
                
                if attempt < max_retries - 1:
                    self.logger.info(f"⏳ Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    self.logger.error("❌ Max retries reached. Bot startup failed.")
                    return False
        
        return False


async def main():
    """Main function - entry point"""
    bot_manager = BotManager()
    
    try:
        # Initialize bot
        if not await bot_manager.initialize_bot():
            bot_manager.logger.error("❌ Failed to initialize bot. Exiting.")
            return
        
        # Start bot
        success = await bot_manager.start_bot()
        
        if not success:
            bot_manager.logger.error("❌ Failed to start bot. Exiting.")
            return
            
    except KeyboardInterrupt:
        bot_manager.logger.info("⌨️ Received keyboard interrupt")
    except Exception as e:
        bot_manager.logger.error(f"💥 Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Ensure cleanup
        if bot_manager.bot:
            try:
                await bot_manager.bot.close()
            except:
                pass
        
        bot_manager.logger.info("🔚 Bot process ended")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\\nBot interrupted by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
