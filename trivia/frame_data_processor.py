"""
SF6 Frame Data Processing Utility - CORRECTED VERSION
Converts SF6 JSON frame data to SQLite database with proper move name handling
"""

import json
import sqlite3
import hashlib
import logging
import os
import re
import argparse
import sys
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class GameConfig:
    """Configuration for a specific fighting game"""
    game_name: str
    json_path: str
    db_path: str
    characters_key: str = "characters"  # Key in JSON where character data is stored
    
class FrameDataProcessor:
    """Generic processor for fighting game frame data JSON files"""
    
    def __init__(self):
        self.processors = {}
        
    def register_game(self, game_id: str, config: GameConfig, 
                     move_processor: Callable[[str, Dict], Optional[Tuple]]):
        """
        Register a game for processing
        
        Args:
            game_id: Unique identifier for the game (e.g., 'sf6', 'tekken8')
            config: GameConfig with file paths and structure info
            move_processor: Function that processes individual moves
        """
        self.processors[game_id] = {
            'config': config,
            'processor': move_processor
        }
    
    def process_game(self, game_id: str, force_rebuild: bool = False) -> bool:
        """
        Process a registered game's data
        
        Args:
            game_id: Game to process
            force_rebuild: Force rebuild even if DB is newer than JSON
            
        Returns:
            True if processing was successful, False otherwise
        """
        if game_id not in self.processors:
            logger.error(f"Game {game_id} not registered")
            return False
        
        game_info = self.processors[game_id]
        config = game_info['config']
        processor_func = game_info['processor']
        
        # Check if processing is needed
        if not force_rebuild and not self._needs_processing(config):
            logger.info(f"{config.game_name} database is up to date")
            return True
        
        logger.info(f"Processing {config.game_name} frame data...")
        
        try:
            # Initialize database
            self._init_database(config.db_path)
            
            # Load and process JSON
            with open(config.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Process moves
            self._process_moves(data, config, processor_func)
            
            logger.info(f"Successfully processed {config.game_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error processing {config.game_name}: {e}")
            return False
    
    def _needs_processing(self, config: GameConfig) -> bool:
        """Check if JSON file needs to be processed"""
        if not os.path.exists(config.db_path):
            return True
        
        if not os.path.exists(config.json_path):
            logger.error(f"JSON file not found: {config.json_path}")
            return False
        
        # Check modification times
        try:
            json_mtime = os.path.getmtime(config.json_path)
            db_mtime = os.path.getmtime(config.db_path)
            return json_mtime > db_mtime
        except OSError:
            return True
    
    def _init_database(self, db_path: str):
        """Initialize SQLite database with enhanced SF6 schema"""
        conn = sqlite3.connect(db_path)
        
        # Create moves table with all SF6 fields
        conn.execute('''
            CREATE TABLE IF NOT EXISTS moves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character TEXT NOT NULL,
                move_name TEXT NOT NULL,
                move_category TEXT,
                startup TEXT,
                active TEXT,
                recovery TEXT,
                onHit TEXT,
                onBlock TEXT,
                cancel TEXT,
                damage TEXT,
                scaling TEXT,
                driveGaugeGain TEXT,
                driveGaugeLoss TEXT,
                superGaugeGain TEXT,
                properties TEXT,
                notes TEXT,
                difficulty_tier INTEGER DEFAULT 1,
                move_hash TEXT UNIQUE,
                raw_data TEXT
            )
        ''')
        
        # Create metadata table for tracking
        conn.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Create indexes for all queryable fields
        conn.execute('CREATE INDEX IF NOT EXISTS idx_character ON moves(character)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_difficulty ON moves(difficulty_tier)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_category ON moves(move_category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_move_hash ON moves(move_hash)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_startup ON moves(startup)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_damage ON moves(damage)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_onblock ON moves(onBlock)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_drive_gain ON moves(driveGaugeGain)')
        
        conn.commit()
        conn.close()
    
    def _process_moves(self, data: Dict, config: GameConfig, 
                      processor_func: Callable[[str, Dict], Optional[Tuple]]):
        """Process moves using the provided processor function"""
        conn = sqlite3.connect(config.db_path)
        
        # Clear existing data
        conn.execute('DELETE FROM moves')
        conn.execute('DELETE FROM metadata')
        
        try:
            characters = data.get(config.characters_key, {})
            moves_processed = 0
            moves_batch = []
            
            for char_name, moves in characters.items():
                if not isinstance(moves, list):
                    continue
                
                logger.info(f"Processing {char_name}: {len(moves)} moves")
                
                for move in moves:
                    processed_move = processor_func(char_name, move)
                    if processed_move:
                        moves_batch.append(processed_move)
                        moves_processed += 1
                        
                        # Log first few moves for debugging
                        if moves_processed <= 5:
                            logger.debug(f"Processed move: {processed_move[1]} ({char_name})")
                        
                        # Insert in batches
                        if len(moves_batch) >= 100:
                            self._insert_moves_batch(conn, moves_batch)
                            moves_batch = []
            
            # Insert remaining moves
            if moves_batch:
                self._insert_moves_batch(conn, moves_batch)
            
            # Store metadata
            conn.execute('INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)',
                        ('total_moves', str(moves_processed)))
            conn.execute('INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)',
                        ('total_characters', str(len(characters))))
            conn.execute('INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)',
                        ('game_name', config.game_name))
            
            conn.commit()
            logger.info(f"Processed {moves_processed} moves from {len(characters)} characters")
            
        except Exception as e:
            logger.error(f"Error during processing: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _insert_moves_batch(self, conn: sqlite3.Connection, moves_batch: List[Tuple]):
        """Insert batch of moves into database with all SF6 fields"""
        conn.executemany('''
            INSERT OR REPLACE INTO moves 
            (character, move_name, move_category, startup, active, recovery, 
             onHit, onBlock, cancel, damage, scaling, driveGaugeGain, 
             driveGaugeLoss, superGaugeGain, properties, notes, 
             difficulty_tier, move_hash, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', moves_batch)

# Game-specific processors

def create_sf6_processor() -> Callable[[str, Dict], Optional[Tuple]]:
    """Create SF6-specific move processor with CORRECTED move name handling"""
    
    def clean_move_name(name: str) -> str:
        """Clean SF6 move names - preserve full move names, just clean up formatting"""
        if not name:
            return ""
        
        # Split by newlines and take the first part (the actual move name)
        parts = name.split('\n')
        main_name = parts[0].strip()
        
        # If the main name is empty, try the next non-empty part
        if not main_name and len(parts) > 1:
            for part in parts[1:]:
                if part.strip():
                    main_name = part.strip()
                    break
        
        # Remove any remaining newlines and clean up whitespace
        main_name = main_name.replace('\n', ' ').strip()
        
        # Return the full cleaned move name (no truncation, no prefix removal)
        if main_name:
            return main_name
        
        # Fallback: clean up the original name
        return name.replace('\n', ' ').strip()
    
    def clean_frame_data(data: str) -> Optional[str]:
        """Clean SF6 frame data"""
        if not data or data in ['', 'D', '*', '-', 'until landing', 'after landing']:
            return None
        
        if data.isdigit() or (data.startswith('-') and data[1:].isdigit()):
            return data
        
        if '-' in data and not data.startswith('-'):
            parts = data.split('-')
            if parts[0].strip().isdigit():
                return parts[0].strip()
        
        numbers = re.findall(r'-?\d+', data)
        return numbers[0] if numbers else None
    
    def clean_gauge_data(data: str) -> Optional[str]:
        """Clean SF6 gauge data (can be negative)"""
        if not data or data in ['', '*', '-']:
            return None
        
        # Handle negative values
        if data.startswith('-') and data[1:].isdigit():
            return data
        
        # Handle positive values
        if data.isdigit():
            return data
        
        # Extract first number found
        numbers = re.findall(r'-?\d+', data)
        return numbers[0] if numbers else None
    
    def determine_category(move_name: str, move_data: Dict) -> str:
        """Determine move category"""
        name_lower = move_name.lower()
        
        if any(x in name_lower for x in ['light', 'medium', 'heavy']):
            if any(x in name_lower for x in ['standing', 'crouching', 'jumping']):
                return 'normal'
        
        if any(x in name_lower for x in ['hadoken', 'shoryuken', 'tatsumaki', 'special']):
            return 'special'
        elif 'SA' in move_data.get('name', '') or 'super' in name_lower:
            return 'super'
        else:
            return 'unique'
    
    def calculate_difficulty(startup: Optional[str], on_block: Optional[str], 
                           move_name: str) -> int:
        """Calculate difficulty: 0=easy, 1=medium, 2=hard"""
        name_lower = move_name.lower()
        
        # Easy: Basic normals with fast startup
        if any(x in name_lower for x in ['standing light', 'crouching light']):
            if startup and startup.isdigit() and int(startup) <= 6:
                return 0
            return 1
        
        # Hard: Very negative on block or complex moves
        if on_block and on_block.startswith('-'):
            try:
                block_val = int(on_block)
                if block_val <= -10:
                    return 2
            except ValueError:
                pass
        
        # Hard: Special moves and supers
        if any(x in name_lower for x in ['hadoken', 'shoryuken', 'tatsumaki', 'super']):
            return 2
        
        return 1  # Medium by default
    
    def create_move_hash(character: str, move_name: str, startup: str, 
                        active: str, recovery: str) -> str:
        """Create unique hash"""
        content = f"{character}|{move_name}|{startup}|{active}|{recovery}"
        return hashlib.md5(content.encode()).hexdigest()[:12]
    
    def process_sf6_move(char_name: str, move: Dict) -> Optional[Tuple]:
        """Process a single SF6 move with all fields"""
        raw_name = move.get('name', '')
        move_name = clean_move_name(raw_name)
        
        if not move_name:
            logger.debug(f"Skipping move with empty name for {char_name}: {raw_name}")
            return None
        
        logger.debug(f"Processing {char_name}: '{raw_name}' -> '{move_name}'")
        
        # Clean all frame data fields
        startup = clean_frame_data(move.get('startup', ''))
        active = clean_frame_data(move.get('active', ''))
        recovery = clean_frame_data(move.get('recovery', ''))
        on_hit = clean_frame_data(move.get('onHit', ''))
        on_block = clean_frame_data(move.get('onBlock', ''))
        damage = clean_frame_data(move.get('damage', ''))
        
        # Clean gauge data (these can be negative, so handle differently)
        drive_gauge_gain = clean_gauge_data(move.get('driveGaugeGain', ''))
        drive_gauge_loss = clean_gauge_data(move.get('driveGaugeLoss', ''))
        super_gauge_gain = clean_gauge_data(move.get('superGaugeGain', ''))
        
        # Get other fields directly
        cancel = move.get('cancel', '') or None
        scaling = move.get('scaling', '') or None
        properties = move.get('properties', '') or None
        notes = move.get('notes', '') or None
        
        # Skip moves without any usable data
        usable_fields = [startup, active, recovery, on_hit, on_block, damage, 
                        drive_gauge_gain, drive_gauge_loss, super_gauge_gain]
        if not any(usable_fields):
            logger.debug(f"Skipping {char_name} {move_name}: no usable frame data")
            return None
        
        category = determine_category(move_name, move)
        difficulty_tier = calculate_difficulty(startup, on_block, move_name)
        move_hash = create_move_hash(char_name, move_name, startup or '', 
                                   active or '', recovery or '')
        
        # Store raw data as JSON for future use
        raw_data = json.dumps(move, separators=(',', ':'))
        
        return (
            char_name,           # character
            move_name,           # move_name - NOW PRESERVES FULL NAMES!
            category,            # move_category
            startup,             # startup
            active,              # active
            recovery,            # recovery
            on_hit,              # onHit
            on_block,            # onBlock
            cancel,              # cancel
            damage,              # damage
            scaling,             # scaling
            drive_gauge_gain,    # driveGaugeGain
            drive_gauge_loss,    # driveGaugeLoss
            super_gauge_gain,    # superGaugeGain
            properties,          # properties
            notes,               # notes
            difficulty_tier,     # difficulty_tier
            move_hash,           # move_hash
            raw_data             # raw_data
        )
    
    return process_sf6_move

# Usage example and setup helper
def setup_frame_data_processing():
    """Setup function to register all games and process data"""
    processor = FrameDataProcessor()
    
    # Register SF6 with correct paths for your structure
    sf6_config = GameConfig(
        game_name="Street Fighter 6",
        json_path="data/sf6/sf6_framedata.json",
        db_path="data/sf6/sf6_trivia.db",
        characters_key="characters"
    )
    processor.register_game('sf6', sf6_config, create_sf6_processor())
    
    # Process SF6 data
    success = processor.process_game('sf6', force_rebuild=True)  # Force rebuild to fix names
    
    if success:
        logger.info("Frame data processing completed successfully")
        return processor
    else:
        logger.error("Frame data processing failed")
        return None

# CLI runner for processing data
if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='Process SF6 frame data with corrected move names')
    parser.add_argument('--force', action='store_true', default=True,
                       help='Force rebuild to fix move names (default: True)')
    parser.add_argument('--stats', action='store_true', default=True,
                       help='Show statistics after processing (default: True)')
    parser.add_argument('--json-path', default="data/sf6/sf6_framedata.json",
                       help='Path to SF6 JSON file')
    parser.add_argument('--db-path', default="data/sf6/sf6_trivia.db",
                       help='Path to output database')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug logging to see move name processing')
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Setup processor with custom paths
    processor = FrameDataProcessor()
    
    sf6_config = GameConfig(
        game_name="Street Fighter 6",
        json_path=args.json_path,
        db_path=args.db_path,
        characters_key="characters"
    )
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(args.db_path), exist_ok=True)
    
    processor.register_game('sf6', sf6_config, create_sf6_processor())
    
    print(f"Processing SF6 data from {args.json_path} to {args.db_path}")
    print("This will preserve full move names like 'Standing Light Punch'")
    print()
    
    # Process the data
    success = processor.process_game('sf6', force_rebuild=args.force)
    
    if success:
        print("Processing completed successfully!")
        
        if args.stats:
            # Quick verification of move names
            import sqlite3
            conn = sqlite3.connect(args.db_path)
            
            print("\nSample of processed move names:")
            cursor = conn.execute("""
                SELECT character, move_name 
                FROM moves 
                WHERE move_name LIKE '%Standing%' OR move_name LIKE '%Crouching%'
                ORDER BY character, move_name
                LIMIT 10
            """)
            
            for char, move_name in cursor.fetchall():
                print(f"  {char}: '{move_name}'")
            
            # Show totals
            cursor = conn.execute("SELECT COUNT(*) FROM moves")
            total_moves = cursor.fetchone()[0]
            
            cursor = conn.execute("SELECT COUNT(DISTINCT character) FROM moves")
            total_chars = cursor.fetchone()[0]
            
            print(f"\nTotal: {total_moves} moves from {total_chars} characters")
            
            conn.close()
    else:
        print("Processing failed!")
        sys.exit(1)