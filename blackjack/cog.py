from __future__ import annotations

import asyncio
import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from .cards import card_from_code, card_to_code
from .db import BlackjackDB
from .game import BlackjackGame
from .renderer import CardRenderer, money
from .shoe import MIN_CARDS_TO_START_HAND, RESHUFFLE_AT_REMAINING_CARDS, SingleDeckShoe

MIN_BET_CENTS = 1_000
DAILY_BONUS_CENTS = 10_000
DEFAULT_DB_PATH = "bot.db"
TIMEZONE = "America/Los_Angeles"
LUCKY_HOURS_PER_DAY = 2
PLAYER_ACTION_TIMEOUT_SECONDS = 30
INSURANCE_TIMEOUT_SECONDS = 10

# Exact-message shortcuts for active blackjack hands.
# These only work when the user types exactly one of these values in the same channel
# where they started their hand.
ACTION_ALIASES: dict[str, str] = {
    "h": "hit",
    "hit": "hit",
    "s": "stand",
    "stand": "stand",
    "d": "double",
    "dd": "double",
    "double": "double",
    "double down": "double",
    "y": "split",
    "p": "split",
    "split": "split",
    # Optional insurance shortcuts. Buttons still remain the primary insurance UI.
    "i": "insurance",
    "insurance": "insurance",
    "n": "no_insurance",
    "no": "no_insurance",
    "no insurance": "no_insurance",
}

SHORTCUT_HELP = "Shortcuts: H=Hit • S=Stand • D=Double • Y/P=Split • I=Insurance • N=No Insurance"
RULES_TEXT = "Single Deck • Dealer hits soft 17 • Insurance pays 2:1 • $10 minimum"


def today_key() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def current_hour() -> int:
    return datetime.now(ZoneInfo(TIMEZONE)).hour


def lucky_hours_for_day(day: str) -> list[int]:
    rng = random.Random(f"blackjack-lucky-hours:{day}")
    return sorted(rng.sample(range(24), k=LUCKY_HOURS_PER_DAY))


def timeout_for_game(game: BlackjackGame | None) -> int:
    if game and game.phase == "insurance":
        return INSURANCE_TIMEOUT_SECONDS
    return PLAYER_ACTION_TIMEOUT_SECONDS


def timeout_text_for_game(game: BlackjackGame) -> str:
    if game.phase == "insurance":
        return f"Auto-skips insurance in {INSURANCE_TIMEOUT_SECONDS} seconds."
    return f"Auto-stands in {PLAYER_ACTION_TIMEOUT_SECONDS} seconds."


class BlackjackView(discord.ui.View):
    def __init__(self, cog: "BlackjackCog", key: tuple[int, int], balance_cents: int, version: int):
        game = cog.games.get(key)
        super().__init__(timeout=timeout_for_game(game))
        self.cog = cog
        self.key = key
        self.version = version
        self.owner_id = key[0]
        self.message: discord.Message | None = None
        self.sync_buttons(balance_cents)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This blackjack hand belongs to someone else.", ephemeral=True)
            return False
        return True

    def sync_buttons(self, balance_cents: int) -> None:
        game = self.cog.games.get(self.key)
        if not game or game.phase == "finished":
            for child in self.children:
                child.disabled = True
            return

        for child in self.children:
            child.disabled = True

        if game.phase == "insurance":
            self.insurance_button.disabled = balance_cents < game.insurance_max_cents
            self.insurance_button.label = f"Insurance {money(game.insurance_max_cents)}"
            self.skip_insurance_button.disabled = False
            return

        hand = game.active_hand
        self.hit_button.disabled = False
        self.stand_button.disabled = False
        self.double_button.disabled = not hand.can_double or balance_cents < hand.bet_cents
        self.split_button.disabled = game.did_split or not hand.can_split or balance_cents < hand.bet_cents

    async def on_timeout(self) -> None:
        # Timeout means: no player response, so either skip insurance or stand the active hand.
        # The version guard prevents old/replaced views from timing out a newer turn.
        if self.message is None:
            return
        await self.cog.handle_timeout(self.key, self.message, self.version)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, row=0)
    async def hit_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_action(interaction, self.key, "hit")

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, row=0)
    async def stand_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_action(interaction, self.key, "stand")

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.success, row=0)
    async def double_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_action(interaction, self.key, "double")

    @discord.ui.button(label="Split", style=discord.ButtonStyle.success, row=0)
    async def split_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_action(interaction, self.key, "split")

    @discord.ui.button(label="Insurance", style=discord.ButtonStyle.danger, row=1)
    async def insurance_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_action(interaction, self.key, "insurance")

    @discord.ui.button(label="No Insurance", style=discord.ButtonStyle.secondary, row=1)
    async def skip_insurance_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_action(interaction, self.key, "no_insurance")


class BlackjackCog(commands.Cog):
    def __init__(self, bot: commands.Bot, *, db_path: str = DEFAULT_DB_PATH):
        self.bot = bot
        self.db = BlackjackDB(db_path)
        self.renderer = CardRenderer(Path(__file__).parent / "assets" / "cards")
        self.games: dict[tuple[int, int], BlackjackGame] = {}
        self.locks: dict[tuple[int, int], asyncio.Lock] = {}
        self.game_messages: dict[tuple[int, int], discord.Message] = {}
        self.view_versions: dict[tuple[int, int], int] = {}
        self.shoes: dict[int, SingleDeckShoe] = {}

    async def cog_load(self) -> None:
        await self.db.init()

    def key_for(self, interaction: discord.Interaction) -> tuple[int, int]:
        # One active blackjack hand per user globally. The game itself still records
        # the channel so typed shortcuts only work in the original channel.
        return (interaction.user.id, 0)

    def key_for_message(self, message: discord.Message) -> tuple[int, int]:
        return (message.author.id, 0)

    def lock_for(self, key: tuple[int, int]) -> asyncio.Lock:
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()
        return self.locks[key]

    def bump_view_version(self, key: tuple[int, int]) -> int:
        version = self.view_versions.get(key, 0) + 1
        self.view_versions[key] = version
        return version

    def cleanup_game(self, key: tuple[int, int]) -> None:
        self.games.pop(key, None)
        self.game_messages.pop(key, None)
        self.view_versions.pop(key, None)

    async def shoe_for_user(self, user_id: int) -> SingleDeckShoe:
        shoe = self.shoes.get(user_id)
        if shoe is not None:
            return shoe

        state = await self.db.get_shoe_state(user_id)
        if state is None:
            shoe = SingleDeckShoe.fresh("new shoe")
        else:
            try:
                shoe = SingleDeckShoe(
                    deck=[card_from_code(code) for code in state["deck"]],
                    discard=[card_from_code(code) for code in state["discard"]],
                    hands_played=int(state["hands_played"]),
                    last_shuffle_reason=str(state["last_shuffle_reason"]),
                )
            except Exception:
                # If old/corrupt shoe data ever exists, fail safe with a fresh deck.
                shoe = SingleDeckShoe.fresh("recovered from invalid shoe state")

        self.shoes[user_id] = shoe
        return shoe

    async def save_shoe_for_user(self, user_id: int) -> None:
        shoe = await self.shoe_for_user(user_id)
        await self.db.save_shoe_state(
            user_id=user_id,
            deck_codes=[card_to_code(card) for card in shoe.deck],
            discard_codes=[card_to_code(card) for card in shoe.discard],
            hands_played=shoe.hands_played,
            last_shuffle_reason=shoe.last_shuffle_reason,
        )

    @app_commands.command(name="blackjack", description="Play persistent single-deck blackjack. Minimum bet is $10.")
    @app_commands.describe(bet="Bet amount in dollars. Minimum $10.")
    async def blackjack(self, interaction: discord.Interaction, bet: app_commands.Range[int, 10, 1_000_000] = 10):
        key = self.key_for(interaction)
        async with self.lock_for(key):
            if key in self.games and self.games[key].phase != "finished":
                active_channel = self.games[key].channel_id
                if active_channel:
                    await interaction.response.send_message(
                        f"You already have an active blackjack hand in <#{active_channel}>.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message("You already have an active blackjack hand.", ephemeral=True)
                return

            day = today_key()
            bonus_claimed = await self.db.try_claim_daily_bonus(interaction.user.id, day, DAILY_BONUS_CENTS)

            bet_cents = bet * 100
            if bet_cents < MIN_BET_CENTS:
                await interaction.response.send_message("Minimum blackjack bet is $10.", ephemeral=True)
                return

            balance = await self.db.get_balance(interaction.user.id)
            if balance < bet_cents:
                msg = f"You only have {money(balance)}. The minimum bet is $10."
                if bonus_claimed:
                    msg += " I did apply your daily +$100 first-play bonus."
                await interaction.response.send_message(msg, ephemeral=True)
                return

            shoe = await self.shoe_for_user(interaction.user.id)
            shuffled_before_hand = shoe.prepare_for_new_hand()

            await self.db.add_balance(interaction.user.id, -bet_cents, "blackjack_bet")

            lucky = current_hour() in lucky_hours_for_day(day)
            player_label = (
                getattr(interaction.user, "display_name", None)
                or getattr(interaction.user, "global_name", None)
                or interaction.user.name
                or "Player"
            )
            game = BlackjackGame.start(
                user_id=interaction.user.id,
                channel_id=interaction.channel_id or 0,
                bet_cents=bet_cents,
                lucky_blackjack=lucky,
                deck=shoe.deck,
                player_label=player_label,
            )
            self.games[key] = game

            note_parts: list[str] = []
            if shuffled_before_hand:
                note_parts.append("Fresh single deck shuffled.")

            if game.phase == "insurance":
                note_parts.append(f"Dealer shows Ace. Take insurance or skip. Auto-skips in {INSURANCE_TIMEOUT_SECONDS} seconds.")
            elif game.phase == "finished":
                note_parts.append(await self.settle_finished_game(interaction.user.id, game))
            else:
                note_parts.append(f"Choose an action. Auto-stands in {PLAYER_ACTION_TIMEOUT_SECONDS} seconds.")

            embed, file, view = await self.build_response(key, note=" ".join(note_parts))
            if bonus_claimed:
                bonus_embed = self.build_daily_bonus_embed(DAILY_BONUS_CENTS)
                await interaction.response.send_message(embed=bonus_embed)
                msg = await interaction.followup.send(embed=embed, file=file, view=view, wait=True)
            else:
                await interaction.response.send_message(embed=embed, file=file, view=view)
                msg = await interaction.original_response()

            if view:
                view.message = msg
                self.game_messages[key] = msg

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Allow exact-message blackjack shortcuts like H, S, D, and Y/P."""
        if message.author.bot:
            return

        content = message.content.strip().lower()
        action = ACTION_ALIASES.get(content)
        if action is None:
            return

        key = self.key_for_message(message)
        game = self.games.get(key)
        if not game or game.phase == "finished":
            return

        # Shortcuts only work in the channel where the hand was started.
        if game.channel_id and game.channel_id != message.channel.id:
            return

        # Keep the game channel clean. Missing Manage Messages permission is fine.
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        await self.handle_shortcut_action(message, key, action)

    async def handle_action(self, interaction: discord.Interaction, key: tuple[int, int], action: str) -> None:
        async with self.lock_for(key):
            game = self.games.get(key)
            if not game or game.phase == "finished":
                await interaction.response.send_message("That blackjack hand is already over.", ephemeral=True)
                return

            success, note = await self.apply_action_locked(key, interaction.user.id, action)
            if not success:
                await interaction.response.send_message(note, ephemeral=True)
                return

            embed, file, view = await self.build_response(key, note=note)
            await interaction.response.edit_message(embed=embed, attachments=[file], view=view)
            if view and interaction.message:
                view.message = interaction.message
                self.game_messages[key] = interaction.message

    async def handle_shortcut_action(self, message: discord.Message, key: tuple[int, int], action: str) -> None:
        async with self.lock_for(key):
            game = self.games.get(key)
            if not game or game.phase == "finished":
                return

            success, note = await self.apply_action_locked(key, message.author.id, action)
            if not success:
                await self.send_shortcut_error(message, note)
                return

            table_message = self.game_messages.get(key)
            if table_message is None:
                await self.send_shortcut_error(message, "I could not find the blackjack table message to update.")
                return

            embed, file, view = await self.build_response(key, note=note)
            await table_message.edit(embed=embed, attachments=[file], view=view)
            if view:
                view.message = table_message
                self.game_messages[key] = table_message

    async def apply_action_locked(self, key: tuple[int, int], user_id: int, action: str) -> tuple[bool, str]:
        """Apply a blackjack action while the caller already holds this game's lock."""
        game = self.games.get(key)
        if not game or game.phase == "finished":
            return False, "That blackjack hand is already over."

        note = ""
        balance = await self.db.get_balance(user_id)

        if game.phase == "insurance":
            if action == "insurance":
                if balance < game.insurance_max_cents:
                    return False, "You do not have enough money for insurance."
                await self.db.add_balance(user_id, -game.insurance_max_cents, "blackjack_insurance")
                game.resolve_insurance(take=True)
                note = f"Insurance taken for {money(game.insurance_max_cents)}."
            elif action == "no_insurance":
                game.resolve_insurance(take=False)
                note = "Insurance skipped."
            else:
                return False, "Resolve insurance first. Use the buttons, I for insurance, or N for no insurance."

        elif game.phase == "player":
            hand = game.active_hand
            if action == "hit":
                game.hit()
                note = "Hit."
            elif action == "stand":
                game.stand()
                note = "Stand."
            elif action == "double":
                if not hand.can_double:
                    return False, "You can only double down on your first two cards."
                if balance < hand.bet_cents:
                    return False, "You do not have enough money to double down."
                await self.db.add_balance(user_id, -hand.bet_cents, "blackjack_double_down")
                game.double()
                note = "Double down. One card dealt, then stand."
            elif action == "split":
                if game.did_split or not hand.can_split:
                    return False, "You can only split once, and only on matching ranks."
                if balance < hand.bet_cents:
                    return False, "You do not have enough money to split."
                await self.db.add_balance(user_id, -hand.bet_cents, "blackjack_split")
                game.split()
                note = "Split. Playing one hand at a time."
            else:
                return False, "Unknown blackjack action."

        if game.phase == "finished":
            note += " " + await self.settle_finished_game(user_id, game)
        else:
            note += " " + timeout_text_for_game(game)

        return True, note

    async def send_shortcut_error(self, message: discord.Message, text: str) -> None:
        try:
            await message.channel.send(f"{message.author.mention} {text}", delete_after=6)
        except discord.HTTPException:
            pass

    async def handle_timeout(self, key: tuple[int, int], message: discord.Message, version: int) -> None:
        async with self.lock_for(key):
            # Ignore stale timeouts from an old View that was replaced after a player action.
            if self.view_versions.get(key) != version:
                return

            game = self.games.get(key)
            if not game or game.phase == "finished":
                return

            if game.phase == "insurance":
                game.resolve_insurance(take=False)
                note = "Timed out: insurance skipped."
            else:
                game.stand()
                note = "Timed out: auto-stand."

            if game.phase == "finished":
                note += " " + await self.settle_finished_game(game.user_id, game)
            else:
                note += " " + timeout_text_for_game(game)

            embed, file, view = await self.build_response(key, note=note)
            await message.edit(embed=embed, attachments=[file], view=view)
            if view:
                view.message = message
                self.game_messages[key] = message

    async def settle_finished_game(self, user_id: int, game: BlackjackGame) -> str:
        first_settlement = not game.settled
        credited = game.settle()
        if credited:
            balance = await self.db.add_balance(user_id, credited, "blackjack_settlement")
        else:
            balance = await self.db.get_balance(user_id)

        deck_note = ""
        if first_settlement:
            shoe = await self.shoe_for_user(user_id)
            reshuffled = shoe.finish_hand(game.cards_in_play())
            await self.save_shoe_for_user(user_id)
            if reshuffled:
                deck_note = "\n\n🔄 Deck reached the cut card and was reshuffled for the next hand."

        return self.format_finished_result(game, balance, deck_note=deck_note)

    def build_daily_bonus_embed(self, amount_cents: int) -> discord.Embed:
        embed = discord.Embed(
            title="🎁 Your first Blackjack game today!",
            description=f"You have been awarded **{money(amount_cents)}** before the cards are dealt.",
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Daily bonus resets at midnight PT.")
        return embed

    def format_finished_result(self, game: BlackjackGame, balance: int, *, deck_note: str = "") -> str:
        net = game.settlement_net_cents
        net_text = money(abs(net))
        player_blackjack = any(hand.is_blackjack for hand in game.hands)
        dealer_bust = game.dealer_value > 21 and any(not hand.busted and hand.value <= 21 for hand in game.hands)
        all_player_hands_busted = all(hand.busted or hand.value > 21 for hand in game.hands)

        if net == 0:
            lines = ["🤝 Push.", "Your bet has been returned."]
        elif player_blackjack and net > 0:
            lines = ["♠️ Blackjack! ♠️", f"You win {net_text}."]
        elif all_player_hands_busted:
            lines = ["💀 Bust!", f"You lose {net_text}."]
        elif dealer_bust and net > 0:
            lines = ["💥 Dealer busts!", f"You win {net_text}."]
        elif net > 0:
            lines = ["🏆 You won!", f"You win {net_text}."]
        else:
            lines = ["💀 Dealer wins.", f"You lose {net_text}."]

        details = self.format_hand_details(game)
        if details:
            lines.extend(["", details])

        lines.append(f"Balance: {money(balance)}")
        if deck_note:
            lines.append(deck_note)
        return "\n".join(lines)

    def format_hand_details(self, game: BlackjackGame) -> str:
        if len(game.hands) <= 1 and not game.insurance_bet_cents:
            return ""

        details: list[str] = []
        if game.insurance_bet_cents:
            insurance_result = "Insurance won" if game.dealer_has_blackjack else "Insurance lost"
            details.append(insurance_result)

        if len(game.hands) > 1:
            dealer_total = game.dealer_value
            dealer_bust = dealer_total > 21
            for idx, hand in enumerate(game.hands, start=1):
                if hand.is_blackjack and game.dealer_has_blackjack:
                    result = "blackjack push"
                elif hand.is_blackjack:
                    result = "blackjack"
                elif hand.busted or hand.value > 21:
                    result = "bust"
                elif game.dealer_has_blackjack:
                    result = "dealer blackjack"
                elif dealer_bust:
                    result = "win"
                elif hand.value > dealer_total:
                    result = "win"
                elif hand.value < dealer_total:
                    result = "lose"
                else:
                    result = "push"
                details.append(f"Hand {idx}: {result}")

        return " • ".join(details)

    def display_name_for_game(self, game: BlackjackGame) -> str:
        if getattr(game, "player_label", None):
            return str(game.player_label)

        channel = self.bot.get_channel(game.channel_id) if game.channel_id else None
        guild = getattr(channel, "guild", None)
        if guild is not None:
            member = guild.get_member(game.user_id)
            if member is not None:
                return member.display_name

        user = self.bot.get_user(game.user_id)
        if user is not None:
            return getattr(user, "display_name", None) or getattr(user, "global_name", None) or user.name

        return "Player"

    def finished_title_for_game(self, game: BlackjackGame) -> str:
        net = game.settlement_net_cents
        net_text = money(abs(net))
        player_blackjack = any(hand.is_blackjack for hand in game.hands)
        dealer_bust = game.dealer_value > 21 and any(not hand.busted and hand.value <= 21 for hand in game.hands)
        all_player_hands_busted = all(hand.busted or hand.value > 21 for hand in game.hands)

        if net == 0:
            return "🤝 PUSH"
        if player_blackjack and net > 0:
            return "♠️ BLACKJACK! ♠️"
        if all_player_hands_busted:
            return f"💀 BUST — LOSE {net_text}"
        if dealer_bust and net > 0:
            return f"💥 DEALER BUSTS — WIN {net_text}"
        if net > 0:
            return f"🏆 YOU WIN {net_text}"
        return f"💀 DEALER WINS — LOSE {net_text}"

    def finished_description_for_game(self, game: BlackjackGame, *, balance_cents: int | None = None, deck_note: str = "",) -> str:
        net = game.settlement_net_cents
        net_text = money(abs(net))
        player_blackjack = any(hand.is_blackjack for hand in game.hands)
        dealer_bust = game.dealer_value > 21 and any(not hand.busted and hand.value <= 21 for hand in game.hands)
        all_player_hands_busted = all(hand.busted or hand.value > 21 for hand in game.hands)

        if net == 0:
            lines = ["Your bet has been returned."]
        elif player_blackjack and net > 0:
            lines = [f"Natural 21 on the deal. You win {net_text}."]
        elif all_player_hands_busted:
            lines = [f"You went over 21 and lose {net_text}."]
        elif dealer_bust and net > 0:
            lines = [f"The dealer busted out. You win {net_text}."]
        elif net > 0:
            lines = [f"Your hand beats the dealer. You win {net_text}."]
        else:
            lines = [f"The dealer wins this hand. You lose {net_text}."]

        details = self.format_hand_details(game)
        if details:
            lines.extend(["", details])

        if balance_cents is not None:
            lines.extend(["", f"Balance: **{money(balance_cents)}**"])

        if deck_note:
            lines.extend(["", deck_note])

        return "\n".join(lines)
        
    def active_title_for_game(self, game: BlackjackGame) -> str:
        if game.phase == "insurance":
            return "🛡️ Insurance"
        return "🃏 Blackjack"

    def active_description_for_game(self, game: BlackjackGame) -> str:
        if game.phase == "insurance":
            return "Dealer shows an Ace. Would you like insurance?"
        if len(game.hands) > 1:
            return f"Your move — playing Hand {game.active_hand_index + 1} of {len(game.hands)}."
        return "Your move."

    def active_actions_text_for_game(self, game: BlackjackGame) -> str:
        if game.phase == "insurance":
            return "Buttons: **Insurance** or **No Insurance** • Shortcuts: **I** / **N**"
        hand = game.active_hand
        actions = ["**H** Hit", "**S** Stand"]
        if hand.can_double:
            actions.append("**D** Double")
        if not game.did_split and hand.can_split:
            actions.append("**Y/P** Split")
        return " • ".join(actions)

    def active_status_text(self, note: str, game: BlackjackGame) -> str:
        text = (note or "").strip()
        auto_line = timeout_text_for_game(game)
        text = text.replace(auto_line, "").strip()
        text = text.replace("  ", " ")
        return text or ("Waiting for your decision." if game.phase != "insurance" else "Insurance decision pending.")

    def embed_color_for_game(self, game: BlackjackGame) -> discord.Color:
        if game.phase == "insurance":
            return discord.Color.orange()
        if game.phase != "finished":
            return discord.Color.blue()

        net = game.settlement_net_cents
        player_blackjack = any(hand.is_blackjack for hand in game.hands)
        dealer_bust = game.dealer_value > 21 and any(not hand.busted and hand.value <= 21 for hand in game.hands)

        if net == 0:
            return discord.Color.light_grey()
        if player_blackjack and net > 0:
            return discord.Color.gold()
        if dealer_bust and net > 0:
            return discord.Color.orange()
        if net > 0:
            return discord.Color.green()
        return discord.Color.red()

    async def build_response(self, key: tuple[int, int], *, note: str) -> tuple[discord.Embed, discord.File, BlackjackView | None]:
        game = self.games[key]
        balance = await self.db.get_balance(game.user_id)
        shoe = await self.shoe_for_user(game.user_id)
        player_name = self.display_name_for_game(game)

        image = self.renderer.render_png(game, note=note, shoe=shoe, player_name=player_name)
        file = discord.File(image, filename="blackjack_table.png")

        if game.phase == "finished":
            title = self.finished_title_for_game(game)
            description = self.finished_description_for_game(game, balance_cents=balance)
        else:
            title = self.active_title_for_game(game)
            description = self.active_description_for_game(game)

        embed = discord.Embed(title=title, description=description, color=self.embed_color_for_game(game))
        if game.phase != "finished":
            embed.add_field(name="Status", value=self.active_status_text(note, game), inline=False)
            embed.add_field(name="Available Actions", value=self.active_actions_text_for_game(game), inline=False)
            embed.set_footer(text=f"Action timer: {PLAYER_ACTION_TIMEOUT_SECONDS}s • Insurance timer: {INSURANCE_TIMEOUT_SECONDS}s • {RULES_TEXT}")
        else:
            embed.set_footer(text=RULES_TEXT)

        embed.set_image(url="attachment://blackjack_table.png")

        view = None
        if game.phase != "finished":
            view = BlackjackView(self, key, balance, self.bump_view_version(key))
        else:
            self.cleanup_game(key)

        return embed, file, view


async def setup(bot: commands.Bot, db_path: str = DEFAULT_DB_PATH):
    await bot.add_cog(BlackjackCog(bot, db_path=db_path))
