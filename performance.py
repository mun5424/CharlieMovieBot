"""
Performance monitoring and optimization utilities
"""

import asyncio
import gc
import logging
import os
import time
from typing import Optional, Dict, Any, Callable

import discord
from discord.ext import commands


class PerformanceMonitor:
    """Performance monitoring and optimization system"""
    
    def __init__(self, config_module=None):
        self.config = config_module
        self.logger = logging.getLogger(__name__)
        
        # Configuration
        self.memory_threshold = self._get_config_value('MEMORY_THRESHOLD', 80)
        self.cpu_threshold = self._get_config_value('CPU_THRESHOLD', 90)
        self.disk_threshold = self._get_config_value('DISK_THRESHOLD', 90)
        self.enable_monitoring = self._get_config_value('ENABLE_PERFORMANCE_MONITORING', True)
        self.monitoring_interval = self._get_config_value('MONITORING_INTERVAL', 60)
        self.stats_log_interval = self._get_config_value('STATS_LOG_INTERVAL', 600)  # 10 minutes
        
        # State
        self.start_time = None
        self.last_stats_log = 0
        self.psutil_available = False
        self.monitoring_task = None
        
        # Callbacks
        self.memory_callbacks = []
        self.cpu_callbacks = []
        self.disk_callbacks = []
    
    def _get_config_value(self, key: str, default):
        """Get configuration value with fallback to default"""
        if self.config and hasattr(self.config, key):
            return getattr(self.config, key)
        return default
    
    async def start_monitoring(self):
        """Start performance monitoring"""
        if not self.enable_monitoring:
            self.logger.info("Performance monitoring disabled")
            return
        
        self.start_time = time.time()
        
        # Check psutil availability
        try:
            import psutil
            self.psutil_available = True
            self.logger.info("✅ Performance monitoring enabled with psutil")
        except ImportError:
            self.psutil_available = False
            self.logger.info("⚠️ psutil not available, limited performance monitoring")
        
        # Start monitoring task
        self.monitoring_task = asyncio.create_task(self._monitor_loop())
    
    async def stop_monitoring(self):
        """Stop performance monitoring"""
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.logger.info("Performance monitoring stopped")
    
    async def _monitor_loop(self):
        """Background monitoring loop"""
        while True:
            try:
                await asyncio.sleep(self.monitoring_interval)
                await self.check_system_resources()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in performance monitoring: {e}")
    
    async def check_system_resources(self) -> Dict[str, float]:
        """Check system resources and trigger alerts"""
        if not self.psutil_available:
            return {}
        
        try:
            import psutil
            
            # Get system stats
            memory = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=1)
            disk = psutil.disk_usage('/')
            
            stats = {
                'memory_percent': memory.percent,
                'memory_available': memory.available,
                'memory_total': memory.total,
                'cpu_percent': cpu_percent,
                'disk_percent': disk.percent,
                'disk_free': disk.free,
                'disk_total': disk.total
            }
            
            # Check thresholds and trigger alerts
            await self._check_thresholds(stats)
            
            # Log periodic stats
            # await self._log_periodic_stats(stats)
            
            return stats
            
        except Exception as e:
            self.logger.error(f"Error checking system resources: {e}")
            return {}
    
    async def _check_thresholds(self, stats: Dict[str, float]):
        """Check resource thresholds and trigger alerts"""
        # Memory threshold
        if stats['memory_percent'] > self.memory_threshold:
            self.logger.warning(f"🔴 High memory usage: {stats['memory_percent']:.1f}%")
            await self._trigger_memory_optimization()
            
            # Trigger callbacks
            for callback in self.memory_callbacks:
                try:
                    await callback(stats)
                except Exception as e:
                    self.logger.error(f"Error in memory callback: {e}")
        
        # CPU threshold
        if stats['cpu_percent'] > self.cpu_threshold:
            self.logger.warning(f"🔴 High CPU usage: {stats['cpu_percent']:.1f}%")
            
            # Trigger callbacks
            for callback in self.cpu_callbacks:
                try:
                    await callback(stats)
                except Exception as e:
                    self.logger.error(f"Error in CPU callback: {e}")
        
        # Disk threshold
        if stats['disk_percent'] > self.disk_threshold:
            self.logger.warning(f"🔴 High disk usage: {stats['disk_percent']:.1f}%")
            
            # Trigger callbacks
            for callback in self.disk_callbacks:
                try:
                    await callback(stats)
                except Exception as e:
                    self.logger.error(f"Error in disk callback: {e}")
    
    async def _log_periodic_stats(self, stats: Dict[str, float]):
        """Log periodic performance statistics"""
        current_time = time.time()
        
        if current_time - self.last_stats_log >= self.stats_log_interval:
            self.logger.info(
                f"📊 System Stats - "
                f"Memory: {stats['memory_percent']:.1f}% "
                f"({stats['memory_available']/(1024**3):.1f}GB free), "
                f"CPU: {stats['cpu_percent']:.1f}%, "
                f"Disk: {stats['disk_percent']:.1f}% "
                f"({stats['disk_free']/(1024**3):.1f}GB free)"
            )
            self.last_stats_log = current_time
    
    async def _trigger_memory_optimization(self):
        """Trigger memory optimization"""
        self.logger.info("🧹 Performing memory optimization...")
        
        # Force garbage collection
        before_gc = len(gc.get_objects())
        collected = gc.collect()
        after_gc = len(gc.get_objects())
        
        self.logger.info(f"🗑️ Garbage collection: {before_gc} → {after_gc} objects ({collected} collected)")
        
        # Additional memory optimization can be added here
        # For example, clearing caches, closing unused connections, etc.
    
    def add_memory_callback(self, callback: Callable):
        """Add callback for high memory usage"""
        self.memory_callbacks.append(callback)
    
    def add_cpu_callback(self, callback: Callable):
        """Add callback for high CPU usage"""
        self.cpu_callbacks.append(callback)
    
    def add_disk_callback(self, callback: Callable):
        """Add callback for high disk usage"""
        self.disk_callbacks.append(callback)


class PerformanceOptimizer:
    """Performance optimization utilities"""
    
    def __init__(self, config_module=None):
        self.config = config_module
        self.logger = logging.getLogger(__name__)
        self.is_raspberry_pi = self._detect_raspberry_pi()
    
    def _detect_raspberry_pi(self) -> bool:
        """Detect if running on Raspberry Pi"""
        try:
            # Check environment variable first
            if os.getenv("RASPBERRY_PI", "false").lower() == "true":
                return True
            
            # Check device tree model file (Linux only)
            if os.path.exists("/proc/device-tree/model"):
                with open("/proc/device-tree/model", "r") as f:
                    model = f.read().strip()
                    if "Raspberry Pi" in model:
                        return True
        except:
            # Fallback to False on any error (e.g., Windows)
            pass
        
        return False
    
    async def apply_optimizations(self):
        """Apply performance optimizations based on environment"""
        if self.is_raspberry_pi:
            await self._apply_pi_optimizations()
        else:
            await self._apply_desktop_optimizations()
    
    async def _apply_pi_optimizations(self):
        """Apply Raspberry Pi specific optimizations"""
        self.logger.info("🥧 Raspberry Pi detected - applying optimizations")
        
        # More aggressive garbage collection for limited memory
        gc.set_threshold(500, 8, 8)
        self.logger.info("🗑️ Set aggressive garbage collection thresholds")
        
        # Try to use uvloop for better performance on ARM
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            self.logger.info("🚀 Using uvloop for better performance")
        except ImportError:
            self.logger.info("⚠️ uvloop not available, using default event loop")
        
        # Additional Pi-specific optimizations
        self._optimize_for_limited_memory()
        self._optimize_for_arm_processor()
    
    async def _apply_desktop_optimizations(self):
        """Apply desktop/server optimizations"""
        self.logger.info("🖥️ Desktop/Server environment detected")
        
        # Standard garbage collection settings
        gc.set_threshold(700, 10, 10)
        self.logger.info("🗑️ Set standard garbage collection thresholds")
        
        # Try uvloop (works on most Linux systems)
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            self.logger.info("🚀 Using uvloop for better performance")
        except ImportError:
            self.logger.info("⚠️ uvloop not available, using default event loop")
    
    def _optimize_for_limited_memory(self):
        """Optimizations for limited memory environments"""
        # Force initial garbage collection
        gc.collect()
        
        # Enable garbage collection debugging (if needed)
        # gc.set_debug(gc.DEBUG_STATS)
        
        self.logger.info("🧠 Applied memory optimizations")
    
    def _optimize_for_arm_processor(self):
        """Optimizations for ARM processors"""
        # ARM-specific optimizations can be added here
        # For example, CPU affinity, thread pool settings, etc.
        
        self.logger.info("💪 Applied ARM processor optimizations")
    
    def get_optimization_info(self) -> Dict[str, Any]:
        """Get information about applied optimizations"""
        info = {
            'is_raspberry_pi': self.is_raspberry_pi,
            'gc_thresholds': gc.get_threshold(),
            'gc_counts': gc.get_count(),
            'event_loop_policy': type(asyncio.get_event_loop_policy()).__name__,
        }
        
        # Add psutil info if available
        try:
            import psutil
            info['psutil_available'] = True
            info['cpu_count'] = psutil.cpu_count()
            info['memory_total'] = psutil.virtual_memory().total
        except ImportError:
            info['psutil_available'] = False
        
        return info


class OptimizedBot(commands.Bot):
    """Enhanced bot class with performance features"""
    
    def __init__(self, config_module=None):
        # Store config first
        self.config = config_module
        self.logger = logging.getLogger(__name__)
        
        # Get performance settings
        max_messages = self._get_config_value('MAX_MESSAGES', 500)
        chunk_guilds = self._get_config_value('CHUNK_GUILDS_AT_STARTUP', False)
        
        # Discord intents
        intents = discord.Intents.default()
        intents.message_content = True
        
        # Initialize bot with performance optimizations
        super().__init__(
            command_prefix="!",
            help_command=None,
            intents=intents,
            chunk_guilds_at_startup=chunk_guilds,
            max_messages=max_messages,
        )
        
        # Performance components (initialize after super().__init__)
        self.performance_monitor = PerformanceMonitor(config_module)
        self.performance_optimizer = PerformanceOptimizer(config_module)
        
        # State
        self.startup_time = None
        self.shutdown_handlers = []
        
        # Setup performance callbacks
        self.performance_monitor.add_memory_callback(self._handle_high_memory)
        self.performance_monitor.add_cpu_callback(self._handle_high_cpu)
        self.performance_monitor.add_disk_callback(self._handle_high_disk)
    
    def _get_config_value(self, key: str, default):
        """Get configuration value with fallback to default"""
        if self.config and hasattr(self.config, key):
            return getattr(self.config, key)
        return default
    
    async def setup_hook(self):
        """Called when the bot is starting up"""
        self.startup_time = time.time()
        
        # Apply performance optimizations
        await self.performance_optimizer.apply_optimizations()
        
        # Start performance monitoring
        await self.performance_monitor.start_monitoring()
        
        # Log optimization info
        opt_info = self.performance_optimizer.get_optimization_info()
        self.logger.info(f"🚀 Performance optimizations applied: {opt_info}")
    
    async def on_ready(self):
        """Enhanced ready event with performance metrics"""
        startup_duration = time.time() - self.startup_time
        
        self.logger.info(f"✅ Logged in as {self.user} (ID: {self.user.id})")
        self.logger.info(f"🏁 Startup completed in {startup_duration:.2f} seconds")
        self.logger.info(f"📊 Connected to {len(self.guilds)} guild(s)")
        
        try:
            total_users = sum(guild.member_count for guild in self.guilds)
            self.logger.info(f"👥 Serving {total_users} users")
        except:
            self.logger.info("👥 User count unavailable")
    
    async def on_command_error(self, ctx, error):
        """Enhanced error handling with performance considerations"""
        if isinstance(error, commands.CommandNotFound):
            return  # Silently ignore unknown commands
        
        # Log error with context
        guild_name = ctx.guild.name if ctx.guild else 'DM'
        self.logger.error(f"Command error in {guild_name}: {error}")
        
        # Don't spam error messages, just log them
        if not isinstance(error, (commands.CheckFailure, commands.DisabledCommand)):
            try:
                await ctx.send("❌ An error occurred. Please try again later.", delete_after=10)
            except:
                pass  # Ignore if we can't send the message
    
    async def close(self):
        """Enhanced cleanup with graceful shutdown"""
        self.logger.info("🔄 Initiating graceful shutdown...")
        
        # Stop performance monitoring
        await self.performance_monitor.stop_monitoring()
        
        # Run shutdown handlers
        for handler in self.shutdown_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler()
                else:
                    handler()
            except Exception as e:
                self.logger.error(f"Error in shutdown handler: {e}")
        
        # Force save any pending data from cogs
        for cog in self.cogs.values():
            if hasattr(cog, 'data_manager') and hasattr(cog.data_manager, 'save_data'):
                try:
                    cog.data_manager.save_data()
                    self.logger.info(f"💾 Saved data for {cog.__class__.__name__}")
                except Exception as e:
                    self.logger.error(f"Error saving data for {cog.__class__.__name__}: {e}")
        
        # Final cleanup
        gc.collect()
        
        await super().close()
        self.logger.info("✅ Shutdown complete")
    
    def add_shutdown_handler(self, handler):
        """Add a function to be called during shutdown"""
        self.shutdown_handlers.append(handler)
    
    async def _handle_high_memory(self, stats: Dict[str, float]):
        """Handle high memory usage"""
        self.logger.warning(f"🔴 High memory usage detected: {stats['memory_percent']:.1f}%")
        
        # Trigger additional cleanup in cogs
        for cog in self.cogs.values():
            if hasattr(cog, 'cleanup_memory'):
                try:
                    await cog.cleanup_memory()
                except Exception as e:
                    self.logger.error(f"Error in cog memory cleanup: {e}")
    
    async def _handle_high_cpu(self, stats: Dict[str, float]):
        """Handle high CPU usage"""
        self.logger.warning(f"🔴 High CPU usage detected: {stats['cpu_percent']:.1f}%")
        # CPU-specific optimizations can be added here
    
    async def _handle_high_disk(self, stats: Dict[str, float]):
        """Handle high disk usage"""
        self.logger.warning(f"🔴 High disk usage detected: {stats['disk_percent']:.1f}%")
        # Disk cleanup can be triggered here
