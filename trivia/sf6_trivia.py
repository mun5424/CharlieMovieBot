import sqlite3
import random
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class SF6Question:
    question: str
    correct_answer: str
    incorrect_answers: List[str]
    difficulty: str
    category: str
    move_name: str
    character: str
    explanation: str = ""
    question_type: str = ""

class SF6TriviaManager:
    """Enhanced SF6 Trivia Manager with comprehensive debug logging"""
    
    def __init__(self, db_path: str = "data/sf6/sf6_trivia.db"):
        self.db_path = db_path
        logger.info(f"Initializing SF6TriviaManager with database path: {db_path}")
        self.available = self._check_availability()
        
        # Common move types for comparative questions
        self.common_moves = [
            "Standing Light Punch", "Standing Light Kick", 
            "Standing Medium Punch", "Standing Medium Kick",
            "Standing Heavy Punch", "Standing Heavy Kick",
            "Crouching Light Punch", "Crouching Light Kick", 
            "Crouching Medium Punch", "Crouching Medium Kick",
            "Crouching Heavy Punch", "Crouching Heavy Kick"
        ]
        
        # Properties suitable for comparative questions
        self.comparable_properties = {
            "startup": {"display": "startup frames", "order": "asc"},
            "recovery": {"display": "recovery frames", "order": "desc"}, 
            "damage": {"display": "damage", "order": "desc"},
            "driveGaugeGain": {"display": "Drive Gauge gain", "order": "desc"},
            "driveGaugeLoss": {"display": "Drive Gauge loss", "order": "asc"},
            "superGaugeGain": {"display": "Super Gauge gain", "order": "desc"},
            "onHit": {"display": "on hit advantage", "order": "desc"},
            "onBlock": {"display": "on block advantage", "order": "desc"}
        }
        
        if self.available:
            logger.info("Enhanced SF6 Trivia Manager initialized successfully")
            # Log some basic stats
            stats = self.get_statistics()
            logger.info(f"SF6 Database loaded: {stats.get('total_moves', 0)} moves, {stats.get('total_characters', 0)} characters")
        else:
            logger.warning("SF6 database not available - SF6 trivia disabled")
    
    def _check_availability(self) -> bool:
        """Check if SF6 database is available and has data"""
        try:
            logger.debug(f"Checking SF6 database availability at: {self.db_path}")
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("SELECT COUNT(*) FROM moves")
            count = cursor.fetchone()[0]
            conn.close()
            logger.info(f"SF6 database check successful: {count} moves found")
            return count > 0
        except Exception as e:
            logger.error(f"SF6 database check failed: {e}")
            return False
    
    def generate_question(self, difficulty: Optional[str] = None, 
                         character: Optional[str] = None) -> Optional[SF6Question]:
        """Generate an enhanced SF6 trivia question with comprehensive logging"""
        logger.info(f"Generating SF6 question: difficulty={difficulty}, character={character}")
        
        if not self.available:
            logger.error("SF6 database not available for question generation")
            return None
        
        try:
            # Determine question type based on difficulty
            question_types = self._get_question_types(difficulty)
            question_type = random.choice(question_types)
            
            logger.info(f"Selected question type: {question_type} from available types: {question_types}")
            
            # Generate question based on type
            if question_type == "comparative":
                result = self._create_comparative_question(character)
            elif question_type == "extreme_value":
                result = self._create_extreme_value_question(character)
            elif question_type == "frame_trap":
                result = self._create_frame_trap_question(character)
            elif question_type == "gauge_efficiency": 
                result = self._create_gauge_efficiency_question(character)
            elif question_type == "special_property":
                result = self._create_special_property_question(character)
            else:
                # Fall back to original question generation
                result = self._create_standard_question(character, difficulty)
            
            if result:
                logger.info(f"Successfully generated {question_type} SF6 question: '{result.question[:50]}...'")
                logger.debug(f"Question details - Character: {result.character}, Move: {result.move_name}")
                logger.debug(f"Correct answer: {result.correct_answer}")
                logger.debug(f"Wrong answers: {result.incorrect_answers}")
            else:
                logger.warning(f"Failed to generate {question_type} SF6 question")
            
            return result
            
        except Exception as e:
            logger.error(f"Error generating enhanced SF6 question: {e}", exc_info=True)
            return None
    
    def _get_question_types(self, difficulty: Optional[str]) -> List[str]:
        """Get available question types based on difficulty"""
        if difficulty == "easy":
            types = ["standard", "comparative"]
        elif difficulty == "medium":
            types = ["comparative", "extreme_value", "standard", "special_property"]
        elif difficulty == "hard":
            types = ["extreme_value", "frame_trap", "gauge_efficiency", "comparative", "special_property"]
        else:
            # Random difficulty
            types = ["comparative", "extreme_value", "frame_trap", "gauge_efficiency", "special_property", "standard"]
        
        logger.debug(f"Available question types for difficulty '{difficulty}': {types}")
        return types
    
    def _create_comparative_question(self, character_filter: Optional[str] = None) -> Optional[SF6Question]:
        """Create questions like 'Which character's Crouching Light Punch has the slowest startup?' - WITH LOGGING"""
        logger.info(f"Creating comparative question for character: {character_filter}")
        conn = sqlite3.connect(self.db_path)
        
        try:
            # Pick a random common move and property
            move_type = random.choice(self.common_moves)
            property_name = random.choice(list(self.comparable_properties.keys()))
            prop_info = self.comparable_properties[property_name]
            
            logger.debug(f"Selected move type: {move_type}, property: {property_name}")
            
            # Get all characters' data for this move type
            query = """
                SELECT character, move_name, startup, recovery, damage, driveGaugeGain, 
                       driveGaugeLoss, superGaugeGain, onHit, onBlock
                FROM moves 
                WHERE move_name LIKE ? AND {} IS NOT NULL AND {} != '' 
            """.format(property_name, property_name)
            
            params = [f"%{move_type}%"]
            if character_filter:
                query += " AND character = ? COLLATE NOCASE"
                params.append(character_filter)
            
            logger.debug(f"Executing comparative query: {query}")
            logger.debug(f"Query params: {params}")
            
            cursor = conn.execute(query, params)
            results = cursor.fetchall()
            
            logger.info(f"Found {len(results)} moves matching '{move_type}' with valid {property_name}")
            
            if len(results) < 3:
                logger.warning(f"Not enough results for comparative question: need 3+, got {len(results)}")
                return None
            
            # Log some sample results for debugging
            for i, result in enumerate(results[:5]):
                logger.debug(f"  Result {i+1}: {result[0]} - {str(result[1])[:30]} - {property_name}={result[2] if property_name == 'startup' else 'varies'}")
            
            # Convert to list of dicts
            columns = ["character", "move_name", "startup", "recovery", "damage", 
                      "driveGaugeGain", "driveGaugeLoss", "superGaugeGain", "onHit", "onBlock"]
            move_data = [dict(zip(columns, row)) for row in results]
            
            # Sort by the property value
            def safe_int(val):
                try:
                    # Handle negative values and complex strings
                    if isinstance(val, str):
                        val = val.replace('-', '').replace('+', '')
                        if val.isdigit():
                            return int(val)
                    return int(val) if val else 0
                except:
                    return 0
            
            logger.debug(f"Sorting by property '{property_name}' in {prop_info['order']} order")
            
            move_data.sort(key=lambda x: safe_int(x[property_name]), 
                          reverse=(prop_info["order"] == "desc"))
            
            # Log sorted results
            logger.debug(f"Top 3 after sorting:")
            for i, data in enumerate(move_data[:3]):
                logger.debug(f"  #{i+1}: {data['character']} - {property_name}={data[property_name]}")
            
            # Create question based on whether we want highest/lowest/fastest/slowest
            if property_name == "startup":
                if prop_info["order"] == "asc":  # Fastest startup (lowest number)
                    question_stem = f"Which character's {move_type} has the fastest startup?"
                    correct_char = move_data[0]["character"]
                    correct_value = move_data[0][property_name]
                else:  # Slowest startup (highest number)
                    question_stem = f"Which character's {move_type} has the slowest startup?"
                    correct_char = move_data[-1]["character"] 
                    correct_value = move_data[-1][property_name]
            elif property_name in ["damage", "driveGaugeGain"]:
                question_stem = f"Which character's {move_type} has the highest {prop_info['display']}?"
                correct_char = move_data[0]["character"]
                correct_value = move_data[0][property_name]
            elif property_name in ["recovery", "driveGaugeLoss"]:
                question_stem = f"Which character's {move_type} has the lowest {prop_info['display']}?"
                correct_char = move_data[0]["character"]
                correct_value = move_data[0][property_name]
            else:
                question_stem = f"Which character's {move_type} has the best {prop_info['display']}?"
                correct_char = move_data[0]["character"]
                correct_value = move_data[0][property_name]
            
            logger.info(f"Generated question: '{question_stem}' - Answer: {correct_char} ({correct_value})")
            
            # Generate wrong answers from other characters
            wrong_chars = [data["character"] for data in move_data[1:4]]
            if len(wrong_chars) < 3:
                logger.debug(f"Only {len(wrong_chars)} wrong answers from move_data, filling with random characters")
                # Fill with random characters if needed
                all_chars = list(set(data["character"] for data in move_data))
                while len(wrong_chars) < 3 and len(all_chars) > len(wrong_chars) + 1:
                    char = random.choice(all_chars)
                    if char != correct_char and char not in wrong_chars:
                        wrong_chars.append(char)
                        logger.debug(f"Added random character: {char}")
            
            logger.debug(f"Wrong answer characters: {wrong_chars[:3]}")
            
            # Validate we have enough wrong answers
            if len(wrong_chars) < 3:
                logger.warning(f"Could not generate 3 wrong answers, only have {len(wrong_chars)}")
                return None
            
            # Create explanation with actual frame data
            explanation = f"{correct_char}'s {move_type} has {correct_value} {prop_info['display']}"
            if len(move_data) > 1:
                explanation += f", compared to {move_data[1]['character']}'s {move_data[1][property_name]}"
            
            logger.info(f"Successfully created comparative SF6 question")
            return SF6Question(
                question=question_stem,
                correct_answer=correct_char,
                incorrect_answers=wrong_chars[:3],
                difficulty="medium",
                category="Street Fighter 6",
                move_name=move_type,
                character=correct_char,
                explanation=explanation,
                question_type="comparative"
            )
            
        except Exception as e:
            logger.error(f"Error creating comparative question: {e}", exc_info=True)
            return None
        finally:
            conn.close()
    
    def _create_extreme_value_question(self, character_filter: Optional[str] = None) -> Optional[SF6Question]:
        """Create questions like 'Which character's Standing Light Punch has the fastest startup?' - WITH LOGGING"""
        logger.info(f"Creating extreme value question for character: {character_filter}")
        conn = sqlite3.connect(self.db_path)
        
        try:
            # Pick a common move type and property for comparison
            move_type = random.choice(self.common_moves)
            
            property_extremes = {
                "startup": {"query": "MIN", "description": "fastest startup"},
                "recovery": {"query": "MAX", "description": "slowest recovery"}, 
                "damage": {"query": "MAX", "description": "highest damage"},
                "driveGaugeGain": {"query": "MAX", "description": "highest Drive Gauge gain"},
            }
            
            prop_name = random.choice(list(property_extremes.keys()))
            prop_info = property_extremes[prop_name]
            
            logger.debug(f"Selected move type: {move_type}, property: {prop_name} ({prop_info['description']})")
            
            # Find the extreme value for this common move type
            query = f"""
                SELECT character, move_name, {prop_name}
                FROM moves 
                WHERE move_name LIKE ? AND {prop_name} IS NOT NULL AND {prop_name} != ''
                ORDER BY CAST({prop_name} AS INTEGER) {"ASC" if prop_info["query"] == "MIN" else "DESC"}
                LIMIT 1
            """
            
            logger.debug(f"Executing extreme value query: {query}")
            cursor = conn.execute(query, (f"%{move_type}%",))
            result = cursor.fetchone()
            
            if not result:
                logger.warning(f"No results found for extreme value question")
                return None
            
            character, move_name, extreme_value = result
            logger.info(f"Found extreme value: {character} - {str(move_name)[:30]} - {prop_name}={extreme_value}")
            
            # Create question focusing on the character with extreme value for this common move
            question_text = f"Which character's {move_type} has the {prop_info['description']}?"
            
            # Get other characters who have this move type for wrong answers
            cursor = conn.execute(f"""
                SELECT DISTINCT character FROM moves 
                WHERE move_name LIKE ? AND character != ? AND {prop_name} IS NOT NULL
                ORDER BY RANDOM() LIMIT 10
            """, (f"%{move_type}%", character))
            
            wrong_answers = [row[0] for row in cursor.fetchall()[:3]]
            logger.debug(f"Generated wrong answers: {wrong_answers}")
            
            if len(wrong_answers) < 3:
                logger.warning(f"Could not generate enough wrong answers: only {len(wrong_answers)}")
                return None
            
            # Clean up move name for display
            clean_move_name = move_name.replace('\n', ' ').strip()
            
            explanation = f"{character}'s {move_type} has {extreme_value} {prop_name}, making it the {prop_info['description']} among all {move_type}s"
            
            logger.info(f"Successfully created extreme value SF6 question")
            return SF6Question(
                question=question_text,
                correct_answer=character,
                incorrect_answers=wrong_answers,
                difficulty="hard",
                category="Street Fighter 6",
                move_name=move_type,
                character=character,
                explanation=explanation,
                question_type="extreme_value"
            )
            
        except Exception as e:
            logger.error(f"Error creating extreme value question: {e}", exc_info=True)
            return None
        finally:
            conn.close()
    
    def _create_frame_trap_question(self, character_filter: Optional[str] = None) -> Optional[SF6Question]:
        """Create questions about frame traps and frame advantage - WITH LOGGING"""
        logger.info(f"Creating frame trap question for character: {character_filter}")
        conn = sqlite3.connect(self.db_path)
        
        try:
            # Find moves that are plus on block (good for frame traps)
            query = """
                SELECT character, move_name, onBlock, startup
                FROM moves 
                WHERE onBlock IS NOT NULL AND CAST(onBlock AS INTEGER) > 0
                ORDER BY CAST(onBlock AS INTEGER) DESC
                LIMIT 20
            """
            
            logger.debug(f"Executing frame trap query: {query}")
            cursor = conn.execute(query)
            results = cursor.fetchall()
            
            logger.info(f"Found {len(results)} moves that are plus on block")
            
            if not results:
                logger.warning("No plus-on-block moves found for frame trap question")
                return None
            
            # Pick a random plus move
            character, move_name, on_block, startup = random.choice(results)
            logger.info(f"Selected plus move: {character} - {str(move_name)[:30]} (+{on_block})")
            
            # Create frame trap scenario question
            question_templates = [
                f"If {character}'s {move_name.replace(chr(10), ' ').strip()} is blocked, how much frame advantage do they have?",
                f"What is {character}'s {move_name.replace(chr(10), ' ').strip()} on block?",
                f"{character}'s {move_name.replace(chr(10), ' ').strip()} leaves them at what advantage when blocked?"
            ]
            
            question_text = random.choice(question_templates)
            logger.debug(f"Generated question: {question_text}")
            
            # Generate wrong answers around the correct value
            correct_val = int(on_block)
            wrong_answers = []
            for offset in [-3, -2, -1, 1, 2, 3]:
                wrong_val = correct_val + offset
                wrong_answers.append(f"+{wrong_val}" if wrong_val > 0 else str(wrong_val))
            
            # Select 3 random wrong answers
            random.shuffle(wrong_answers)
            final_wrong_answers = wrong_answers[:3]
            
            correct_answer = f"+{on_block}" if int(on_block) > 0 else str(on_block)
            logger.debug(f"Correct answer: {correct_answer}, Wrong answers: {final_wrong_answers}")
            
            explanation = f"{character}'s {move_name.replace(chr(10), ' ').strip()} is {correct_answer} on block, making it good for frame traps"
            
            logger.info(f"Successfully created frame trap SF6 question")
            return SF6Question(
                question=question_text,
                correct_answer=correct_answer,
                incorrect_answers=final_wrong_answers,
                difficulty="hard",
                category="Street Fighter 6",
                move_name=move_name.replace(chr(10), ' ').strip(),
                character=character,
                explanation=explanation,
                question_type="frame_trap"
            )
            
        except Exception as e:
            logger.error(f"Error creating frame trap question: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    def _create_gauge_efficiency_question(self, character_filter: Optional[str] = None) -> Optional[SF6Question]:
        """Create questions about Drive Gauge and Super Gauge efficiency - WITH LOGGING"""
        logger.info(f"Creating gauge efficiency question for character: {character_filter}")
        conn = sqlite3.connect(self.db_path)
        
        try:
            # Find moves with meaningful gauge properties (not 0 or null)
            gauge_types = [
                ("driveGaugeGain", "Drive Gauge gain", "DESC"),
                ("driveGaugeLoss", "Drive Gauge loss", "ASC"), 
                ("superGaugeGain", "Super Gauge gain", "DESC")
            ]
            
            gauge_prop, gauge_display, sort_order = random.choice(gauge_types)
            logger.debug(f"Selected gauge property: {gauge_prop} ({gauge_display})")
            
            # Better filtering to exclude 0 values and ensure meaningful data
            query = f"""
                SELECT character, move_name, {gauge_prop}, damage
                FROM moves 
                WHERE {gauge_prop} IS NOT NULL 
                AND {gauge_prop} != '' 
                AND {gauge_prop} != '0'
                AND CAST({gauge_prop} AS INTEGER) != 0
                AND move_name IS NOT NULL
                AND move_name != ''
                ORDER BY ABS(CAST({gauge_prop} AS INTEGER)) {"DESC" if sort_order == "DESC" else "ASC"}
                LIMIT 10
            """
            
            logger.debug(f"Executing gauge query: {query}")
            cursor = conn.execute(query)
            results = cursor.fetchall()
            
            logger.info(f"Found {len(results)} moves with meaningful {gauge_prop} values")
            if len(results) == 0:
                logger.warning(f"No moves found with meaningful {gauge_prop} values")
                return None
            
            # Log the found moves for debugging
            for i, (char, move, gauge_val, dmg) in enumerate(results[:3]):
                logger.debug(f"  Option {i+1}: {char} - {str(move)[:30]} - {gauge_prop}={gauge_val}")
            
            character, move_name, gauge_value, damage = random.choice(results[:3])
            logger.info(f"Selected move: {character} - {str(move_name)[:30]} (gauge={gauge_value})")
            
            # Clean up move name and validate data
            clean_move_name = str(move_name).replace('\n', ' ').replace('\r', '').strip()
            logger.debug(f"Cleaned move name: '{clean_move_name}' (length: {len(clean_move_name)})")
            
            if not clean_move_name or len(clean_move_name) < 3:
                logger.warning(f"Move name too short or empty: '{clean_move_name}'")
                return None
                
            # Validate gauge_value is actually meaningful
            try:
                gauge_int = int(gauge_value)
                logger.debug(f"Parsed gauge value: {gauge_int}")
                if gauge_int == 0:
                    logger.warning(f"Gauge value is 0 after parsing: {gauge_value}")
                    return None
            except (ValueError, TypeError) as e:
                logger.error(f"Could not parse gauge value '{gauge_value}': {e}")
                return None
            
            # Better question templates
            question_templates = [
                f"How much {gauge_display} does {character}'s {clean_move_name} have?",
                f"What is the {gauge_display} for {character}'s {clean_move_name}?",
            ]
            
            question_text = random.choice(question_templates)
            logger.debug(f"Generated question: {question_text}")
            
            # Generate more realistic wrong answers
            base_val = abs(gauge_int)
            wrong_answers = []
            
            logger.debug(f"Generating wrong answers for base value: {base_val}")
            
            # Use different strategies based on the magnitude of the value
            if base_val >= 1000:
                logger.debug("Using large value strategy (>=1000)")
                # Large gauge values - use percentage-based alternatives
                multipliers = [0.5, 0.75, 1.25, 1.5, 2.0]
                for mult in multipliers:
                    alt_val = int(base_val * mult)
                    # Round to nearest 250 for realism
                    alt_val = round(alt_val / 250) * 250
                    if alt_val > 0 and alt_val != base_val:
                        if gauge_int < 0:
                            wrong_answers.append(f"-{alt_val}")
                        else:
                            wrong_answers.append(str(alt_val))
            else:
                logger.debug("Using small value strategy (<1000)")
                # Smaller values - use fixed offsets
                offsets = [-200, -100, 100, 200, 300, 500]
                for offset in offsets:
                    alt_val = base_val + offset
                    if alt_val > 0 and alt_val != base_val:
                        if gauge_int < 0:
                            wrong_answers.append(f"-{alt_val}")
                        else:
                            wrong_answers.append(str(alt_val))
            
            # Remove duplicates and ensure we have at least 3 unique wrong answers
            wrong_answers = list(set(wrong_answers))
            logger.debug(f"Generated {len(wrong_answers)} unique wrong answers: {wrong_answers}")
            
            if len(wrong_answers) < 3:
                logger.warning(f"Only generated {len(wrong_answers)} wrong answers, using fallback")
                # Fallback: generate simple alternatives
                for i in range(1, 6):
                    fallback_val = base_val + (i * 100)
                    if gauge_int < 0:
                        wrong_answers.append(f"-{fallback_val}")
                    else:
                        wrong_answers.append(str(fallback_val))
                wrong_answers = list(set(wrong_answers))
            
            # Select 3 wrong answers
            random.shuffle(wrong_answers)
            final_wrong_answers = wrong_answers[:3]
            
            logger.info(f"Final wrong answers: {final_wrong_answers}")
            
            # Validate we have enough unique answers
            all_answers = [str(gauge_value)] + final_wrong_answers
            if len(set(all_answers)) < 4:
                logger.error(f"Not enough unique answers: {all_answers}")
                return None
            
            # Better explanation with validation
            damage_text = f" and deals {damage} damage" if damage and str(damage) != '0' else ""
            explanation = f"{character}'s {clean_move_name} has {gauge_value} {gauge_display}{damage_text}"
            
            logger.info(f"Successfully created gauge efficiency question")
            return SF6Question(
                question=question_text,
                correct_answer=str(gauge_value),
                incorrect_answers=final_wrong_answers,
                difficulty="hard",
                category="Street Fighter 6", 
                move_name=clean_move_name,
                character=character,
                explanation=explanation,
                question_type="gauge_efficiency"
            )
            
        except Exception as e:
            logger.error(f"Error creating gauge efficiency question: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    def _create_special_property_question(self, character_filter: Optional[str] = None) -> Optional[SF6Question]:
        """Create questions about special move properties and cancels - WITH LOGGING"""
        logger.info(f"Creating special property question for character: {character_filter}")
        conn = sqlite3.connect(self.db_path)
        
        try:
            # Find moves with interesting properties
            query = """
                SELECT character, move_name, cancel, scaling
                FROM moves 
                WHERE (cancel IS NOT NULL AND cancel != '' AND cancel != 'C') OR 
                      (scaling IS NOT NULL AND scaling != '')
                ORDER BY RANDOM()
                LIMIT 20
            """
            
            logger.debug(f"Executing special property query: {query}")
            cursor = conn.execute(query)
            results = cursor.fetchall()
            
            logger.info(f"Found {len(results)} moves with special properties")
            
            if not results:
                logger.warning("No moves with special properties found")
                return None
            
            # Pick a move with interesting properties
            character, move_name, cancel, scaling = random.choice(results)
            logger.info(f"Selected move: {character} - {str(move_name)[:30]}")
            logger.debug(f"Properties: cancel={cancel}, scaling={scaling}")
            
            # Determine question type based on available data
            if cancel and cancel not in ['', 'C']:
                # Special cancel properties
                question_text = f"What special cancel property does {character}'s {move_name.replace(chr(10), ' ').strip()} have?"
                correct_answer = cancel
                wrong_answers = ["C", "SA1", "SA2", "SA3", "*", "None"]
                wrong_answers = [ans for ans in wrong_answers if ans != correct_answer][:3]
                explanation = f"{character}'s {move_name.replace(chr(10), ' ').strip()} can cancel into {cancel}"
                logger.debug(f"Cancel question: {correct_answer} vs {wrong_answers}")
                
            elif scaling and "scaling" in scaling.lower():
                # Scaling properties
                question_text = f"What scaling property does {character}'s {move_name.replace(chr(10), ' ').strip()} have?"
                if "20%" in scaling:
                    correct_answer = "20% scaling"
                    wrong_answers = ["10% scaling", "30% scaling", "No scaling"]
                else:
                    correct_answer = scaling.split('\n')[0] if '\n' in scaling else scaling
                    wrong_answers = ["20% starter scaling", "No scaling", "Immediate scaling"]
                explanation = f"{character}'s {move_name.replace(chr(10), ' ').strip()} has {scaling}"
                logger.debug(f"Scaling question: {correct_answer} vs {wrong_answers}")
                
            else:
                logger.warning("No suitable properties found for selected move")
                return None  # Skip if no suitable property
            
            logger.info(f"Successfully created special property SF6 question")
            return SF6Question(
                question=question_text,
                correct_answer=correct_answer,
                incorrect_answers=wrong_answers[:3],
                difficulty="medium",
                category="Street Fighter 6",
                move_name=move_name.replace(chr(10), ' ').strip(),
                character=character,
                explanation=explanation,
                question_type="special_property"
            )
            
        except Exception as e:
            logger.error(f"Error creating special property question: {e}", exc_info=True)
            return None
        finally:
            conn.close()
    
    def _create_standard_question(self, character_filter: Optional[str], 
                                 requested_difficulty: Optional[str]) -> Optional[SF6Question]:
        """Create standard single-move questions - WITH LOGGING"""
        logger.info(f"Creating standard SF6 question: character={character_filter}, difficulty={requested_difficulty}")
        
        # Get a suitable move from database
        move_data = self._get_random_move(requested_difficulty, character_filter)
        if not move_data:
            logger.warning("No suitable move data found for standard question")
            return None
        
        # Generate question from move data
        return self._create_question_from_move(move_data, requested_difficulty)

    def _get_random_move(self, difficulty: Optional[str] = None, 
                        character: Optional[str] = None) -> Optional[Dict]:
        """Get a random move that has usable data - WITH LOGGING"""
        logger.info(f"Getting random move: difficulty={difficulty}, character={character}")
        conn = sqlite3.connect(self.db_path)
        
        try:
            # Better query with proper data validation
            query = """
                SELECT * FROM moves 
                WHERE move_name IS NOT NULL 
                AND move_name != ''
                AND character IS NOT NULL 
                AND character != ''
                AND (
                    (startup IS NOT NULL AND startup != '' AND startup != 'None' AND CAST(startup AS INTEGER) > 0) OR
                    (onBlock IS NOT NULL AND onBlock != '' AND onBlock != 'None') OR
                    (active IS NOT NULL AND active != '' AND active != 'None' AND CAST(active AS INTEGER) > 0) OR
                    (recovery IS NOT NULL AND recovery != '' AND recovery != 'None' AND CAST(recovery AS INTEGER) > 0) OR
                    (damage IS NOT NULL AND damage != '' AND damage != 'None' AND CAST(damage AS INTEGER) > 0)
                )
            """
            params = []
            
            # Add character filter
            if character:
                query += " AND character = ? COLLATE NOCASE"
                params.append(character)
                logger.debug(f"Added character filter: {character}")
            
            query += " ORDER BY RANDOM() LIMIT 100"
            
            logger.debug(f"Executing move query with {len(params)} parameters")
            cursor = conn.execute(query, params)
            moves = cursor.fetchall()
            
            logger.info(f"Found {len(moves)} moves matching criteria")
            
            if not moves:
                logger.warning("No moves found with usable data")
                return None
            
            # Convert to dictionary
            columns = [desc[0] for desc in cursor.description]
            move_dicts = [dict(zip(columns, row)) for row in moves]
            
            # More strict validation of move data
            suitable_moves = []
            for i, move in enumerate(move_dicts):
                if self._has_valid_question_data(move):
                    suitable_moves.append(move)
                    if i < 5:  # Log first few for debugging
                        logger.debug(f"Valid move {i+1}: {move.get('character')} - {str(move.get('move_name', ''))[:30]}")
            
            logger.info(f"{len(suitable_moves)} moves passed validation out of {len(move_dicts)}")
            
            if not suitable_moves:
                logger.warning("No moves passed validation for question data")
                return None
                
            selected_move = random.choice(suitable_moves)
            logger.info(f"Selected move: {selected_move.get('character')} - {str(selected_move.get('move_name', ''))[:30]}")
            return selected_move
            
        except Exception as e:
            logger.error(f"Error getting random move: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    def _has_valid_question_data(self, move: Dict) -> bool:
        """Check if move has valid data suitable for questions - WITH LOGGING"""
        # More thorough validation
        question_fields = [
            ('startup', lambda x: x and str(x).isdigit() and int(x) > 0),
            ('onBlock', lambda x: x and str(x) not in ['', 'None', None]),
            ('active', lambda x: x and str(x).isdigit() and int(x) > 0),
            ('recovery', lambda x: x and str(x).isdigit() and int(x) > 0),
            ('damage', lambda x: x and str(x).isdigit() and int(x) > 0)
        ]
        
        # Must have clean move name
        move_name = move.get('move_name', '')
        if not move_name or len(str(move_name).strip()) < 3:
            logger.debug(f"Move rejected: invalid name '{str(move_name)[:20]}'")
            return False
        
        # Must have at least one valid field
        valid_fields = 0
        field_status = {}
        for field_name, validator in question_fields:
            field_value = move.get(field_name)
            try:
                is_valid = validator(field_value)
                field_status[field_name] = is_valid
                if is_valid:
                    valid_fields += 1
            except Exception as e:
                field_status[field_name] = False
                logger.debug(f"Field {field_name} validation error: {e}")
        
        is_valid = valid_fields > 0
        if not is_valid:
            logger.debug(f"Move rejected: {move.get('character')} - {str(move_name)[:20]} - no valid fields")
            logger.debug(f"Field status: {field_status}")
        
        return is_valid

    def _create_question_from_move(self, move_data: Dict, 
                                  requested_difficulty: Optional[str]) -> Optional[SF6Question]:
        """Create a trivia question from move data - WITH LOGGING"""
        logger.info(f"Creating question from move: {move_data.get('character')} - {str(move_data.get('move_name', ''))[:30]}")
        
        try:
            # Validate move data first
            if not move_data.get('character') or not move_data.get('move_name'):
                logger.warning("Invalid move data: missing character or move_name")
                return None
                
            clean_move_name = str(move_data['move_name']).replace('\n', ' ').replace('\r', '').strip()
            if len(clean_move_name) < 3:
                logger.warning(f"Move name too short: '{clean_move_name}'")
                return None
            
            logger.debug(f"Processing move: {move_data['character']} - {clean_move_name}")
            
            # Better question type selection with validation
            question_options = []
            
            question_templates = {
                'startup': [
                    "What is the startup frames of {character}'s {move_name}?",
                    "How many startup frames does {character}'s {move_name} have?"
                ],
                'onBlock': [
                    "What is {character}'s {move_name} on block?",
                    "What is the block advantage of {character}'s {move_name}?"
                ],
                'active': [
                    "How many active frames does {character}'s {move_name} have?",
                    "What are the active frames for {character}'s {move_name}?"
                ],
                'recovery': [
                    "What is the recovery frames of {character}'s {move_name}?",
                    "How many recovery frames does {character}'s {move_name} have?"
                ],
                'damage': [
                    "How much damage does {character}'s {move_name} deal?",
                    "What is the damage value of {character}'s {move_name}?"
                ]
            }
            
            # Stricter validation of available question types
            for field, templates in question_templates.items():
                value = move_data.get(field)
                if self._is_valid_field_value(field, value):
                    question_options.append((field, templates, value))
                    logger.debug(f"Valid question option: {field} = {value}")
                else:
                    logger.debug(f"Invalid question option: {field} = {value}")
            
            if not question_options:
                logger.warning(f"No valid question options for move: {clean_move_name}")
                return None
            
            # Select random question type
            question_type, templates, correct_answer = random.choice(question_options)
            template = random.choice(templates)
            
            logger.info(f"Selected question type: {question_type}, correct answer: {correct_answer}")
            
            # Format question
            question_text = template.format(
                character=move_data['character'],
                move_name=clean_move_name
            )
            
            logger.debug(f"Generated question text: {question_text}")
            
            # Generate wrong answers with better validation
            incorrect_answers = self._generate_wrong_answers(str(correct_answer), question_type)
            
            logger.debug(f"Generated wrong answers: {incorrect_answers}")
            
            # Ensure we have 3 unique wrong answers
            if len(set(incorrect_answers)) < 3:
                logger.warning(f"Could not generate enough unique wrong answers for {question_type}")
                return None
            
            # Create detailed explanation
            explanation = f"{move_data['character']}'s {clean_move_name}: "
            frame_details = []
            
            detail_fields = [
                ('startup', 'Startup'),
                ('active', 'Active'), 
                ('recovery', 'Recovery'),
                ('onHit', 'On Hit'),
                ('onBlock', 'On Block'),
                ('damage', 'Damage')
            ]
            
            for field_name, display_name in detail_fields:
                value = move_data.get(field_name)
                if self._is_valid_field_value(field_name, value):
                    frame_details.append(f"{display_name}: {value}")
            
            explanation += ", ".join(frame_details) if frame_details else f"{question_type}: {correct_answer}"
            
            logger.debug(f"Generated explanation: {explanation}")
            
            # Determine difficulty
            difficulty = requested_difficulty or "medium"
            
            logger.info(f"Successfully created standard SF6 question")
            return SF6Question(
                question=question_text,
                correct_answer=str(correct_answer),
                incorrect_answers=incorrect_answers[:3],
                difficulty=difficulty,
                category="Street Fighter 6",
                move_name=clean_move_name,
                character=move_data['character'],
                explanation=explanation,
                question_type="standard"
            )
            
        except Exception as e:
            logger.error(f"Error creating question from move data: {e}", exc_info=True)
            return None

    def _is_valid_field_value(self, field_name: str, value) -> bool:
        """Check if a field value is valid for question generation"""
        if value is None or value == '' or str(value).lower() == 'none':
            return False
        
        value_str = str(value).strip()
        if not value_str:
            return False
        
        # Special validation for numeric fields
        if field_name in ['startup', 'active', 'recovery', 'damage']:
            try:
                num_val = int(value_str)
                return num_val > 0  # Must be positive
            except ValueError:
                return False
        
        # onBlock can be negative, zero, or positive
        if field_name in ['onBlock', 'onHit']:
            try:
                int(value_str)  # Just check if it's a valid integer
                return True
            except ValueError:
                # Handle special values like 'D' for down
                return value_str in ['D', 'KD']  # Known special values
        
        # For gauge fields, allow negative values but not zero
        if field_name in ['driveGaugeGain', 'driveGaugeLoss', 'superGaugeGain']:
            try:
                num_val = int(value_str)
                return num_val != 0  # Allow negative but not zero
            except ValueError:
                return False
        
        return len(value_str) > 0
    
    def _generate_wrong_answers(self, correct: str, question_type: str) -> List[str]:
        """Generate much more challenging wrong answers that are harder to guess"""
        try:
            correct_num = int(correct)
        except (ValueError, TypeError):
            # Handle special values
            if str(correct) == 'D':
                return ['+3', '+1', '0']
            return ['--', '+1', '-1']
        
        if question_type == 'startup':
            # Make startup wrong answers much trickier - use actual common startup values from SF6
            # but ensure they're plausible for the move type being asked about
            sf6_startup_values = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 26]
            
            # Filter to values that are within a reasonable range of the correct answer
            min_val = max(3, correct_num - 6)
            max_val = min(30, correct_num + 6)
            candidates = [x for x in sf6_startup_values if min_val <= x <= max_val and x != correct_num]
            
            # If we don't have enough candidates, expand the range slightly
            if len(candidates) < 3:
                candidates = [x for x in sf6_startup_values if x != correct_num]
            
            selected = random.sample(candidates, min(3, len(candidates)))
            return [str(x) for x in selected]
        
        elif question_type == 'onBlock':
            # Frame advantage - use more realistic SF6 on-block values
            sf6_block_values = [-15, -14, -13, -12, -11, -10, -9, -8, -7, -6, -5, -4, -3, -2, -1, 
                               0, 1, 2, 3, 4, 5, 6, 7, 8]
            
            # Get values that are close enough to be plausible but not too close
            candidates = []
            for val in sf6_block_values:
                diff = abs(val - correct_num)
                if 2 <= diff <= 6:  # Must be 2-6 frames different (not too obvious)
                    candidates.append(val)
            
            # If still not enough, relax the constraints slightly
            if len(candidates) < 3:
                candidates = [x for x in sf6_block_values if 1 <= abs(x - correct_num) <= 8 and x != correct_num]
            
            selected = random.sample(candidates, min(3, len(candidates)))
            return [str(x) for x in selected]
        
        elif question_type == 'damage':
            # Damage values - use realistic SF6 damage scaling
            base = (correct_num // 100) * 100  # Round to nearest 100
            
            # Create more realistic damage alternatives based on SF6 damage scaling
            multipliers = [0.6, 0.7, 0.8, 1.2, 1.3, 1.4, 1.6, 1.8]
            candidates = []
            
            for mult in multipliers:
                alt_damage = int(base * mult)
                # Round to realistic SF6 damage values (multiples of 50 usually)
                alt_damage = (alt_damage // 50) * 50
                if alt_damage > 0 and alt_damage != correct_num:
                    candidates.append(alt_damage)
            
            # Remove duplicates and select
            candidates = list(set(candidates))
            selected = random.sample(candidates, min(3, len(candidates)))
            return [str(x) for x in selected]
        
        elif question_type in ['driveGaugeGain', 'driveGaugeLoss', 'superGaugeGain']:
            # Gauge values - these vary significantly, so use more sophisticated generation
            base_val = abs(correct_num)
            
            # Create alternatives based on common gauge value patterns in SF6
            gauge_multipliers = [0.5, 0.6, 0.75, 1.25, 1.5, 2.0]
            candidates = []
            
            for mult in gauge_multipliers:
                alt_val = int(base_val * mult)
                # Round to common gauge increments (usually multiples of 250 or 500)
                if alt_val >= 1000:
                    alt_val = (alt_val // 500) * 500
                else:
                    alt_val = (alt_val // 250) * 250
                
                # Maintain the sign if original was negative
                if correct_num < 0:
                    alt_val = -alt_val
                    
                if alt_val != 0 and alt_val != correct_num:
                    candidates.append(alt_val)
            
            candidates = list(set(candidates))
            selected = random.sample(candidates, min(3, len(candidates)))
            return [str(x) for x in selected]
        
        elif question_type == 'active':
            # Active frames - usually low numbers, be more precise
            sf6_active_values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20]
            candidates = [x for x in sf6_active_values if x != correct_num and abs(x - correct_num) >= 1]
            selected = random.sample(candidates, min(3, len(candidates)))
            return [str(x) for x in selected]
        
        elif question_type == 'recovery':
            # Recovery frames - wide range, but use realistic SF6 values
            sf6_recovery_values = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 28, 30, 32, 35]
            
            # Select values that are plausibly close but not too close
            candidates = []
            for val in sf6_recovery_values:
                diff = abs(val - correct_num)
                if 2 <= diff <= 8:  # Reasonable difference range
                    candidates.append(val)
            
            if len(candidates) < 3:
                candidates = [x for x in sf6_recovery_values if x != correct_num]
            
            selected = random.sample(candidates, min(3, len(candidates)))
            return [str(x) for x in selected]
        
        # Enhanced generic fallback for any other properties
        # Create more sophisticated alternatives based on the magnitude of the number
        if abs(correct_num) >= 1000:
            # Large numbers (gauge values) - use percentage-based differences
            offsets = [-30, -20, -15, 15, 20, 30]  # Percentage differences
            candidates = [correct_num + int(correct_num * offset / 100) for offset in offsets]
        elif abs(correct_num) >= 100:
            # Medium numbers (damage) - use fixed offsets
            offsets = [-150, -100, -50, 50, 100, 150]
            candidates = [correct_num + offset for offset in offsets]
        else:
            # Small numbers (frame data) - use small fixed offsets
            offsets = [-4, -3, -2, 2, 3, 4]
            candidates = [correct_num + offset for offset in offsets]
        
        # Filter out negative results for properties that shouldn't be negative
        candidates = [x for x in candidates if x > 0 and x != correct_num]
        
        if len(candidates) < 3:
            # Final fallback - just use simple offsets
            candidates = [correct_num + offset for offset in [-2, -1, 1, 2, 3] if correct_num + offset > 0]
        
        return [str(x) for x in candidates[:3]]
    
    def get_statistics(self) -> Dict:
        """Get SF6 trivia statistics"""
        if not self.available:
            return {}
        
        conn = sqlite3.connect(self.db_path)
        
        try:
            stats = {}
            
            # Basic counts
            cursor = conn.execute("SELECT COUNT(*) FROM moves")
            stats['total_moves'] = cursor.fetchone()[0]
            
            cursor = conn.execute("SELECT COUNT(DISTINCT character) FROM moves")
            stats['total_characters'] = cursor.fetchone()[0]
            
            # Question type availability
            question_fields = ['startup', 'onBlock', 'active', 'recovery', 'damage']
            for field in question_fields:
                cursor = conn.execute(f"""
                    SELECT COUNT(*) FROM moves 
                    WHERE {field} IS NOT NULL AND {field} != '' AND {field} != 'None'
                """)
                stats[f'{field}_available'] = cursor.fetchone()[0]
            
            logger.debug(f"SF6 Statistics: {stats}")
            return stats
            
        finally:
            conn.close()
    
    def get_characters(self) -> List[str]:
        """Get list of available characters"""
        if not self.available:
            return []
        
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("SELECT DISTINCT character FROM moves ORDER BY character")
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def to_standard_format(self, sf6_question: SF6Question) -> Dict:
        """Convert SF6Question to standard trivia question format"""
        result = {
            "question": sf6_question.question,
            "correct_answer": sf6_question.correct_answer,
            "incorrect_answers": sf6_question.incorrect_answers,
            "category": sf6_question.category,
            "difficulty": sf6_question.difficulty,
            "type": "multiple",
            "explanation": sf6_question.explanation,
            "question_type": sf6_question.question_type,
            "character": sf6_question.character,
            "move_name": sf6_question.move_name
        }
        
        logger.debug(f"Converted SF6Question to standard format: {result['question'][:50]}...")
        return result