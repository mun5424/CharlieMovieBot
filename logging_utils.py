# logging_utils.py
"""
Modular logging setup with performance optimizations
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional


class LoggingConfig:
    """Configuration for logging system"""
    
    def __init__(self, config_module=None):
        self.config = config_module
        
        # Default settings
        self.log_level = self._get_config_value('LOG_LEVEL', logging.INFO)
        self.max_bytes = self._get_config_value('MAX_LOG_SIZE', 5*1024*1024)  # 5MB
        self.backup_count = self._get_config_value('LOG_BACKUP_COUNT', 3)
        self.logs_dir = self._get_config_value('LOGS_DIR', 'logs')
        self.log_file = self._get_config_value('LOG_FILE', 'bot.log')
        
        # Performance settings
        self.enable_file_logging = self._get_config_value('ENABLE_FILE_LOGGING', True)
        self.enable_console_logging = self._get_config_value('ENABLE_CONSOLE_LOGGING', True)
        self.detailed_file_logs = self._get_config_value('DETAILED_FILE_LOGS', True)
        
        # Third-party library log levels
        self.third_party_levels = {
            'discord': self._get_config_value('DISCORD_LOG_LEVEL', logging.WARNING),
            'discord.http': self._get_config_value('DISCORD_HTTP_LOG_LEVEL', logging.WARNING),
            'aiohttp': self._get_config_value('AIOHTTP_LOG_LEVEL', logging.WARNING),
            'urllib3': self._get_config_value('URLLIB3_LOG_LEVEL', logging.WARNING),
        }
    
    def _get_config_value(self, key: str, default):
        """Get configuration value with fallback to default"""
        if self.config and hasattr(self.config, key):
            return getattr(self.config, key)
        return default


class EnhancedLogger:
    """Enhanced logging setup with performance optimizations"""
    
    def __init__(self, config_module=None):
        self.config = LoggingConfig(config_module)
        self.logger = None
        self._setup_complete = False
    
    def setup_logging(self) -> logging.Logger:
        """Setup enhanced logging with file rotation and performance monitoring"""
        if self._setup_complete:
            return self.logger
        
        # Create logs directory
        if self.config.enable_file_logging:
            os.makedirs(self.config.logs_dir, exist_ok=True)
        
        # Configure formatters
        formatters = self._create_formatters()
        
        # Setup handlers
        handlers = []
        
        # File handler
        if self.config.enable_file_logging:
            file_handler = self._create_file_handler(formatters['detailed'])
            if file_handler:
                handlers.append(file_handler)
        
        # Console handler
        if self.config.enable_console_logging:
            console_handler = self._create_console_handler(formatters['simple'])
            handlers.append(console_handler)
        
        # Setup root logger
        self._setup_root_logger(handlers)
        
        # Configure third-party loggers
        self._configure_third_party_loggers()
        
        # Create main logger
        self.logger = logging.getLogger(__name__)
        self._setup_complete = True
        
        return self.logger
    
    def _create_formatters(self) -> dict:
        """Create logging formatters"""
        formatters = {
            'detailed': logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
            ),
            'simple': logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s'
            ),
            'minimal': logging.Formatter(
                '%(levelname)s - %(message)s'
            )
        }
        return formatters
    
    def _create_file_handler(self, formatter) -> Optional[RotatingFileHandler]:
        """Create file handler with rotation"""
        try:
            log_path = os.path.join(self.config.logs_dir, self.config.log_file)
            
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=self.config.max_bytes,
                backupCount=self.config.backup_count,
                encoding='utf-8'
            )
            
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.DEBUG if self.config.detailed_file_logs else self.config.log_level)
            
            return file_handler
            
        except Exception as e:
            print(f"Warning: Could not setup file logging: {e}")
            return None
    
    def _create_console_handler(self, formatter) -> logging.StreamHandler:
        """Create console handler"""
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(self.config.log_level)
        return console_handler
    
    def _setup_root_logger(self, handlers: list):
        """Setup root logger with handlers"""
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        
        # Clear existing handlers
        root_logger.handlers.clear()
        
        # Add new handlers
        for handler in handlers:
            root_logger.addHandler(handler)
    
    def _configure_third_party_loggers(self):
        """Configure third-party library loggers to reduce noise"""
        for logger_name, level in self.config.third_party_levels.items():
            logging.getLogger(logger_name).setLevel(level)
    
    def get_logger(self, name: str = None) -> logging.Logger:
        """Get a logger instance"""
        if not self._setup_complete:
            self.setup_logging()
        
        if name:
            return logging.getLogger(name)
        return self.logger
    
    def log_system_info(self, additional_info: dict = None):
        """Log system information for debugging"""
        if not self.logger:
            return
        
        import sys
        import platform
        
        self.logger.info("=" * 50)
        self.logger.info("SYSTEM INFORMATION")
        self.logger.info("=" * 50)
        self.logger.info(f"Python Version: {sys.version}")
        self.logger.info(f"Platform: {platform.platform()}")
        self.logger.info(f"Architecture: {platform.architecture()}")
        self.logger.info(f"Processor: {platform.processor()}")
        
        # Check if running on Raspberry Pi
        try:
            if os.path.exists("/proc/device-tree/model"):
                with open("/proc/device-tree/model", "r") as f:
                    model = f.read().strip()
                    self.logger.info(f"Device Model: {model}")
        except:
            pass
        
        # Log additional info if provided
        if additional_info:
            self.logger.info("-" * 30)
            for key, value in additional_info.items():
                self.logger.info(f"{key}: {value}")
        
        self.logger.info("=" * 50)
    
    def cleanup(self):
        """Cleanup logging resources"""
        if self.logger:
            # Close all handlers
            for handler in logging.getLogger().handlers[:]:
                handler.close()
                logging.getLogger().removeHandler(handler)
            
            self._setup_complete = False


# Convenience functions for easy use
def setup_logging(config_module=None) -> logging.Logger:
    """Setup logging and return main logger"""
    enhanced_logger = EnhancedLogger(config_module)
    return enhanced_logger.setup_logging()


def get_logger(name: str = None, config_module=None) -> logging.Logger:
    """Get a logger instance"""
    enhanced_logger = EnhancedLogger(config_module)
    return enhanced_logger.get_logger(name)


def log_system_info(logger: logging.Logger, additional_info: dict = None):
    """Log system information"""
    enhanced_logger = EnhancedLogger()
    enhanced_logger.logger = logger
    enhanced_logger.log_system_info(additional_info)


# Global logger instance for module-level use
_global_logger = None


def get_global_logger() -> logging.Logger:
    """Get the global logger instance"""
    global _global_logger
    if _global_logger is None:
        _global_logger = setup_logging()
    return _global_logger