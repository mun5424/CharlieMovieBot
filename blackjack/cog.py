from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from .cards import card_from_code, card_to_code
from .db import BlackjackDB
from .game import BlackjackGame, PlayerHand
from .renderer import CardRenderer, money
from .shoe import MIN_CARDS_TO_START_HAND, RESHUFFLE_AT_REMAINING_CARDS, SingleDeckShoe

logger = logging.getLogger(__name__)

MIN_BET_CENTS = 1_000
DAILY_BONUS_BASE_CENTS = 10_000
DAILY_BONUS_STREAK_STEP_CENTS = 2_500
DAILY_BONUS_MAX_CENTS = 20_000
DEFAULT_DB_PATH = "bot.db"
TIMEZONE = "America/Los_Angeles"
PLAYER_ACTION_TIMEOUT_SECONDS = 30
INSURANCE_TIMEOUT_SECONDS = 10
FINISHED_GAME_GRACE_SECONDS = 10 * 60

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
RULES_TEXT = "Single Deck • Dealer hits soft 17 • Blackjack 3:2 • Insurance 2:1 "

LEADERBOARD_MEDALS = ["🥇", "🥈", "🥉"]
# Blackjack balances/stats are global across every server the bot is in, so the
# leaderboard query itself has no guild scoping. To keep results limited to the
# requesting server, we pull more candidates than we need and filter out any
# user who isn't a member of that guild before taking the top 3.
LEADERBOARD_CANDIDATE_LIMIT = 25
# (metric key, embed field label, value format: "int" | "pct" | "money")
LEADERBOARD_CATEGORIES: list[tuple[str, str, str]] = [
    ("win_streak", "🔥 Highest Win Streak vs Dealer", "int"),
    ("busts_prevented", "🛡️ Most Busts Prevented", "int"),
    ("win_pct", "🎯 Highest Win % (min. 10 hands)", "pct"),
    ("roi_pct", "📈 Highest ROI % (min. $100 wagered)", "pct"),
    ("blackjacks_hit", "🂡 Most Blackjacks Hit", "int"),
    ("biggest_win", "💰 Biggest Single Win", "money"),
    ("hands_played", "🎰 Most Hands Played", "int"),
]


def today_key() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def timeout_for_game(game: BlackjackGame | None) -> int:
    if game and game.phase == "insurance":
        return INSURANCE_TIMEOUT_SECONDS
    return PLAYER_ACTION_TIMEOUT_SECONDS


def timeout_text_for_game(game: BlackjackGame) -> str:
    if game.phase == "insurance":
        return f"Auto-skips insurance in {INSURANCE_TIMEOUT_SECONDS} seconds."
    return ""


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

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        # discord.py's default View.on_error only prints to stderr, which this bot's
        # logging setup does not capture into logs/bot.log (only the `logging` module
        # output is captured there). Route it through `logging` so button-click
        # failures are actually visible, and try to leave the user with a response
        # instead of a silently-failed interaction.
        game = self.cog.games.get(self.key)
        logger.exception(
            "Blackjack button error: key=%s item=%s message_id=%s game_message_id=%s game_phase=%s",
            self.key,
            getattr(item, "label", item),
            getattr(interaction.message, "id", None),
            getattr(game, "message_id", None),
            getattr(game, "phase", None),
            exc_info=error,
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong handling that click. Please try again or start a new hand with `/blackjack`.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Something went wrong handling that click. Please try again or start a new hand with `/blackjack`.",
                    ephemeral=True,
                )
        except discord.HTTPException:
            pass

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

        # Dealer only offers insurance when showing an Ace (that's what puts the
        # game into the "insurance" phase above), so outside that phase these
        # buttons are never actionable - remove them instead of just disabling.
        self.remove_item(self.insurance_button)
        self.remove_item(self.skip_insurance_button)

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

    @discord.ui.button(label="Double", style=discord.ButtonStyle.success, row=0)
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
        self.active_views: dict[tuple[int, int], BlackjackView] = {}
        self.finished_cleanup_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self.shoes: dict[int, SingleDeckShoe] = {}
        self.corrupt_state_refunds: dict[tuple[int, int], int] = {}

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
        task = self.finished_cleanup_tasks.pop(key, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self.games.pop(key, None)
        self.game_messages.pop(key, None)
        old_view = self.active_views.pop(key, None)
        if old_view is not None:
            old_view.stop()
        # view_versions is intentionally NOT reset here. Each BlackjackView starts
        # its own independent timeout timer at construction and is never explicitly
        # cancelled when superseded (aside from the .stop() above, added after this
        # bug was found), so an old view's timer can still be pending when this key
        # gets reused for a brand new hand. If the version counter reset to 0 here,
        # a new hand's first view would restart at version 1 - the exact same value
        # a quickly-finished previous hand's only view would have had - so a stale
        # timeout from hand N could coincidentally pass the staleness check
        # (self.view_versions[key] != version) for hand N+1 and get misapplied to
        # it, corrupting its message_id. Keeping this counter monotonic per key for
        # the process's lifetime guarantees no two views for the same key ever
        # share a version number, regardless of the .stop() calls below.

    async def delete_active_game_for_key(self, key: tuple[int, int]) -> None:
        await self.db.delete_active_game(key[0])

    async def cleanup_game_and_db(self, key: tuple[int, int]) -> None:
        self.cleanup_game(key)
        await self.delete_active_game_for_key(key)

    def schedule_finished_cleanup(self, key: tuple[int, int], game: BlackjackGame) -> None:
        old_task = self.finished_cleanup_tasks.pop(key, None)
        if old_task and not old_task.done():
            old_task.cancel()

        async def cleanup_later() -> None:
            try:
                await asyncio.sleep(FINISHED_GAME_GRACE_SECONDS)
                if self.games.get(key) is game and game.phase == "finished":
                    self.cleanup_game(key)
                    await self.delete_active_game_for_key(key)
            except asyncio.CancelledError:
                return

        self.finished_cleanup_tasks[key] = asyncio.create_task(cleanup_later())

    def active_game_state(self, game: BlackjackGame) -> dict:
        return {
            "user_id": game.user_id,
            "channel_id": game.channel_id,
            "bet_cents": game.bet_cents,
            "player_label": game.player_label,
            "message_id": game.message_id,
            "deck": [card_to_code(card) for card in game.deck],
            "dealer": [card_to_code(card) for card in game.dealer],
            "hands": [
                {
                    "cards": [card_to_code(card) for card in hand.cards],
                    "bet_cents": hand.bet_cents,
                    "from_split": hand.from_split,
                    "stood": hand.stood,
                    "busted": hand.busted,
                    "doubled": hand.doubled,
                }
                for hand in game.hands
            ],
            "active_hand_index": game.active_hand_index,
            "phase": game.phase,
            "insurance_bet_cents": game.insurance_bet_cents,
            "insurance_resolved": game.insurance_resolved,
            "settled": game.settled,
            "did_split": game.did_split,
            "settlement_lines": game.settlement_lines,
            "settlement_credited_cents": game.settlement_credited_cents,
            "settlement_net_cents": game.settlement_net_cents,
            "hand_results": game.hand_results,
            "busts_prevented": game.busts_prevented,
        }

    def game_from_active_state(self, state: dict) -> BlackjackGame:
        return BlackjackGame(
            user_id=int(state["user_id"]),
            channel_id=int(state.get("channel_id", 0)),
            bet_cents=int(state["bet_cents"]),
            player_label=str(state.get("player_label") or "Player"),
            message_id=int(state.get("message_id", 0)),
            deck=[card_from_code(code) for code in state.get("deck", [])],
            dealer=[card_from_code(code) for code in state.get("dealer", [])],
            hands=[
                PlayerHand(
                    cards=[card_from_code(code) for code in hand_state.get("cards", [])],
                    bet_cents=int(hand_state.get("bet_cents", 0)),
                    from_split=bool(hand_state.get("from_split", False)),
                    stood=bool(hand_state.get("stood", False)),
                    busted=bool(hand_state.get("busted", False)),
                    doubled=bool(hand_state.get("doubled", False)),
                )
                for hand_state in state.get("hands", [])
            ],
            active_hand_index=int(state.get("active_hand_index", 0)),
            phase=state.get("phase", "player"),
            insurance_bet_cents=int(state.get("insurance_bet_cents", 0)),
            insurance_resolved=bool(state.get("insurance_resolved", False)),
            settled=bool(state.get("settled", False)),
            did_split=bool(state.get("did_split", False)),
            settlement_lines=list(state.get("settlement_lines", [])),
            settlement_credited_cents=int(state.get("settlement_credited_cents", 0)),
            settlement_net_cents=int(state.get("settlement_net_cents", 0)),
            hand_results=list(state.get("hand_results", [])),
            busts_prevented=int(state.get("busts_prevented", 0)),
        )

    async def save_active_game(self, key: tuple[int, int]) -> None:
        game = self.games.get(key)
        if game is None:
            return
        await self.db.save_active_game(game.user_id, game.message_id, self.active_game_state(game))

    async def load_active_game(self, key: tuple[int, int]) -> BlackjackGame | None:
        game = self.games.get(key)
        if game is not None:
            return game
        state = await self.db.get_active_game(key[0])
        if state is None:
            return None
        try:
            game = self.game_from_active_state(state)
        except Exception as exc:
            logger.error("Blackjack active-game state could not be restored for key=%s: %s", key, exc, exc_info=exc)
            await self.delete_active_game_for_key(key)
            await self.refund_unrestorable_game(key, state)
            return None
        self.games[key] = game
        return game

    async def refund_unrestorable_game(self, key: tuple[int, int], state: dict) -> None:
        """A persisted hand that can't be reconstructed must not silently keep the player's bet."""
        if state.get("settled"):
            # settle() already credited this hand's payout before the state became
            # unreadable (it's kept around briefly for the finished-game grace period
            # / until the next /blackjack call) - refunding again would double-pay.
            return

        # key[0] is the trusted user id this row was looked up by; don't trust a
        # possibly-corrupt "user_id" field inside the state we're already recovering from.
        user_id = key[0]

        try:
            staked = sum(int(hand.get("bet_cents", 0)) for hand in state.get("hands", []))
            staked += int(state.get("insurance_bet_cents", 0))
            if staked <= 0:
                staked = int(state.get("bet_cents", 0) or 0)
        except Exception:
            staked = int(state.get("bet_cents", 0) or 0)

        if staked > 0:
            await self.db.add_balance(user_id, staked, "blackjack_corrupt_state_refund")
            self.corrupt_state_refunds[key] = staked
            logger.warning("Blackjack refunded %s cents to user_id=%s after unrestorable hand state.", staked, user_id)

    def pop_corrupt_refund_note(self, key: tuple[int, int]) -> str | None:
        refunded = self.corrupt_state_refunds.pop(key, 0)
        if not refunded:
            return None
        return f"Your previous blackjack hand's saved data was corrupted and could not be resumed. {money(refunded)} was refunded to your balance."

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
            existing_game = await self.load_active_game(key)
            refund_note = self.pop_corrupt_refund_note(key)
            if existing_game and existing_game.phase == "finished":
                # Best-effort: if that hand's final edit_message/table_message.edit
                # never went through (e.g. a transient Discord API failure), its
                # message may still show live buttons even though the hand is fully
                # settled. Strip them now, in the background, so that old message
                # can't later be clicked and mistaken for a still-active hand once
                # this new one takes over the key. Fire-and-forget so a slow/failed
                # Discord call here never delays this interaction's own response.
                asyncio.create_task(self.disable_stale_message(key, existing_game))
                await self.cleanup_game_and_db(key)
                existing_game = None

            if existing_game and existing_game.phase != "finished":
                active_channel = existing_game.channel_id
                if active_channel:
                    await interaction.response.send_message(
                        f"You already have an active blackjack hand in <#{active_channel}>.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message("You already have an active blackjack hand.", ephemeral=True)
                return

            day = today_key()
            bonus_claimed, bonus_amount_cents, bonus_streak = await self.db.claim_daily_bonus(
                interaction.user.id,
                day,
                base_cents=DAILY_BONUS_BASE_CENTS,
                streak_step_cents=DAILY_BONUS_STREAK_STEP_CENTS,
                max_cents=DAILY_BONUS_MAX_CENTS,
            )

            bet_cents = bet * 100
            if bet_cents < MIN_BET_CENTS:
                await interaction.response.send_message("Minimum blackjack bet is $10.", ephemeral=True)
                return

            balance = await self.db.get_balance(interaction.user.id)
            if balance < bet_cents:
                msg = f"You only have {money(balance)}. The minimum bet is $10."
                if bonus_claimed:
                    msg += f" I did apply your daily +{money(bonus_amount_cents)} bonus."
                await interaction.response.send_message(msg, ephemeral=True)
                return

            # Committed to dealing a hand now - defer before the slow render/send
            # work below (a natural blackjack's celebration GIF alone can take
            # over a second to render), so Discord always gets acked well within
            # its 3s window regardless of render time. Public/non-ephemeral:
            # Discord ties a followup's ephemeral-ness to how the *original*
            # response was deferred, not to flags passed on the followup call
            # itself - deferring ephemeral here silently made the real, public
            # hand table ephemeral too (confirmed against the live bot).
            await interaction.response.defer()

            shoe = await self.shoe_for_user(interaction.user.id)
            shuffled_before_hand = shoe.prepare_for_new_hand()

            await self.db.add_balance(interaction.user.id, -bet_cents, "blackjack_bet")

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
                deck=shoe.deck,
                player_label=player_label,
            )
            self.games[key] = game
            await self.save_active_game(key)

            note_parts: list[str] = []
            if refund_note:
                note_parts.append(refund_note)
            if shuffled_before_hand:
                note_parts.append("Fresh single deck shuffled.")

            if game.phase == "insurance":
                note_parts.append(f"Dealer shows Ace. Take insurance or skip. Auto-skips in {INSURANCE_TIMEOUT_SECONDS} seconds.")
            elif game.phase == "finished":
                note_parts.append(await self.settle_finished_game(interaction.user.id, game))
            else:
                note_parts.append(f"Choose an action.")

            try:
                embed, file, view = await self.build_response(key, note=" ".join(note_parts), celebrate=True)
                if bonus_claimed:
                    # edit_original_response turns the deferred placeholder into a real,
                    # permanent public message instead of leaving it as a dangling
                    # "thinking..." - it's always the earliest message in the channel
                    # (created back at defer()), so it must carry the bonus embed here
                    # for the bonus-then-table reading order; the table then goes out
                    # as a second, later followup message below it.
                    bonus_embed = self.build_daily_bonus_embed(bonus_amount_cents, bonus_streak)
                    await interaction.edit_original_response(embed=bonus_embed)
                    # discord.py's followup.send() treats an explicitly-passed view=None
                    # differently from an omitted view (it checks `is not MISSING`, not
                    # truthiness) and crashes with AttributeError: 'NoneType' object has
                    # no attribute 'is_finished'. view is None whenever the hand finishes
                    # instantly (a natural or dealer blackjack dealt before any player
                    # action), so the view kwarg must be omitted entirely in that case.
                    send_kwargs: dict = {"embed": embed, "file": file}
                    if view is not None:
                        send_kwargs["view"] = view
                    msg = await interaction.followup.send(**send_kwargs, wait=True)
                else:
                    msg = await interaction.edit_original_response(embed=embed, attachments=[file], view=view)
            except Exception:
                # self.games[key] was already marked active and persisted above, before
                # any of this. If rendering or delivering the table fails now (a slow
                # render + a flaky Discord webhook call is enough), that leaves a phantom
                # "active" hand with no message/view ever attached to resolve it - the
                # player is locked out of /blackjack forever with nothing to click and no
                # timeout to save them (BlackjackView.on_timeout no-ops when .message is
                # None). Tear the phantom hand down and refund so the key is usable again.
                logger.exception("Blackjack: failed to deliver a freshly dealt hand for key=%s", key)
                already_settled = game.phase == "finished"
                await self.cleanup_game_and_db(key)
                if not already_settled:
                    await self.db.add_balance(interaction.user.id, bet_cents, "blackjack_deal_send_failure_refund")
                try:
                    note = (
                        "Something went wrong showing that hand's result, but it was already settled - "
                        "check your balance, then try `/blackjack` again."
                        if already_settled
                        else "Something went wrong starting that hand and your bet was refunded. Please try `/blackjack` again."
                    )
                    await interaction.edit_original_response(content=note, embed=None, attachments=[], view=None)
                except discord.HTTPException:
                    pass
                return

            game.message_id = msg.id
            await self.save_active_game(key)
            if view:
                view.message = msg
                self.game_messages[key] = msg
            elif game.phase == "finished":
                self.schedule_finished_cleanup(key, game)

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
        async with self.lock_for(key):
            game = await self.load_active_game(key)

        if not game:
            note = self.pop_corrupt_refund_note(key)
            if note:
                await self.send_shortcut_error(message, note)
            return
        if game.phase == "finished":
            return

        # Shortcuts only work in the channel where the hand was started.
        if game.channel_id and game.channel_id != message.channel.id:
            return

        # Keep the game channel clean. Missing Manage Messages permission is fine.
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        try:
            await self.handle_shortcut_action(message, key, action)
        except Exception:
            # on_message errors go through discord.py's generic Client.on_error,
            # which (like View.on_error) only prints to stderr by default and is
            # not captured by this bot's logging setup. Log it properly here so a
            # shortcut-triggered crash is actually visible in logs/bot.log.
            logger.exception("Blackjack shortcut error: key=%s action=%s", key, action)

    async def handle_action(self, interaction: discord.Interaction, key: tuple[int, int], action: str) -> None:
        async with self.lock_for(key):
            game = await self.load_active_game(key)

            if not game:
                logger.warning(
                    "Blackjack 'not game' on action=%s: key=%s clicked_message_id=%s db_state_present=%s",
                    action,
                    key,
                    getattr(interaction.message, "id", None),
                    (await self.db.get_active_game(key[0])) is not None,
                )
                reason = self.pop_corrupt_refund_note(key) or (
                    "This blackjack hand is no longer active due to timeout."
                )
                await self.expire_stale_interaction(interaction, reason)
                return

            if interaction.message and game.message_id and interaction.message.id != game.message_id:
                logger.warning(
                    "Blackjack message_id mismatch on action=%s: key=%s clicked_message_id=%s "
                    "current_game_message_id=%s game_phase=%s view_version=%s",
                    action,
                    key,
                    interaction.message.id,
                    game.message_id,
                    game.phase,
                    self.view_versions.get(key),
                )
                await self.expire_stale_interaction(
                    interaction,
                    "This is an older blackjack table. Use the newest blackjack message or start a new hand.",
                )
                return

            if game.phase == "finished":
                # If a timeout already finished the hand but the timeout edit failed,
                # recover by rendering the final result now instead of leaving the
                # message looking playable.
                embed, file, _ = await self.build_response(key, note="")
                await interaction.response.edit_message(embed=embed, attachments=[file], view=None)
                self.schedule_finished_cleanup(key, game)
                return

            success, note = await self.apply_action_locked(key, interaction.user.id, action)
            if not success:
                await interaction.response.send_message(note, ephemeral=True)
                return

            await self.save_active_game(key)

            # Defer before build_response: resolving insurance can reveal the
            # player's own natural blackjack (the only way a fresh natural can
            # surface outside the initial deal), which routes into the ~1s+
            # celebration GIF render. That alone can blow past Discord's 3s
            # ack window for this component interaction, turning what should be
            # a normal edit into a "404 Unknown interaction". defer() on a
            # component interaction is a silent deferred *update* - it doesn't
            # post any placeholder message, so there's no visible change here.
            await interaction.response.defer()
            embed, file, view = await self.build_response(key, note=note, celebrate=True)
            finished = game.phase == "finished"
            try:
                msg = await interaction.edit_original_response(embed=embed, attachments=[file], view=view)
            except discord.HTTPException as exc:
                logger.warning("Blackjack action edit failed for key=%s: %s", key, exc, exc_info=exc)
                # The action itself was already applied and saved above (and, if
                # finished, already settled/paid out) - only the Discord-side
                # confirmation failed. Leave the game in memory (uncleaned) so the
                # next click on this same message can recover and render the real
                # state, matching handle_timeout's recovery behavior below.
                return
            game.message_id = msg.id
            if view:
                view.message = msg
                self.game_messages[key] = msg
            if finished:
                self.schedule_finished_cleanup(key, game)
            else:
                await self.save_active_game(key)

    async def resolve_table_message(self, key: tuple[int, int], game: BlackjackGame) -> discord.Message | None:
        """Find the live table message, refetching it if the bot restarted and lost the in-memory cache."""
        cached = self.game_messages.get(key)
        if cached is not None:
            return cached

        if not game.channel_id or not game.message_id:
            logger.warning("resolve_table_message: key=%s has no channel_id/message_id to resolve from", key)
            return None

        channel = self.bot.get_channel(game.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(game.channel_id)
            except (discord.HTTPException, discord.ClientException) as exc:
                logger.warning("resolve_table_message: key=%s fetch_channel(%s) failed: %s", key, game.channel_id, exc)
                return None

        try:
            fetched = await channel.fetch_message(game.message_id)
        except (discord.HTTPException, AttributeError) as exc:
            # AttributeError covers channel types (e.g. category/forum channels) that
            # don't implement fetch_message at all.
            logger.warning("resolve_table_message: key=%s fetch_message(%s) failed: %s", key, game.message_id, exc)
            return None

        self.game_messages[key] = fetched
        return fetched

    async def disable_stale_message(self, key: tuple[int, int], game: BlackjackGame) -> None:
        """Best-effort: strip buttons from a finished hand's message in case its real
        final edit never went through. Runs detached (fire-and-forget) after a new
        hand may already have taken over `key`, so this deliberately never touches
        self.game_messages/self.games - it only knows the old game's channel/message
        ids, fetched directly, to avoid clobbering the new hand's cached message."""
        if not game.channel_id or not game.message_id:
            return

        channel = self.bot.get_channel(game.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(game.channel_id)
            except (discord.HTTPException, discord.ClientException):
                return

        try:
            message = await channel.fetch_message(game.message_id)
            await message.edit(view=None)
        except (discord.HTTPException, AttributeError):
            pass

    async def handle_shortcut_action(self, message: discord.Message, key: tuple[int, int], action: str) -> None:
        async with self.lock_for(key):
            game = await self.load_active_game(key)
            if not game or game.phase == "finished":
                return

            success, note = await self.apply_action_locked(key, message.author.id, action)
            if not success:
                await self.send_shortcut_error(message, note)
                return

            # Persist immediately so any money already moved by this action (e.g. a
            # double or split debit) is never lost, even if the table message below
            # can't be located or edited.
            await self.save_active_game(key)

            table_message = await self.resolve_table_message(key, game)
            if table_message is None:
                await self.send_shortcut_error(
                    message,
                    "Your action was applied, but I could not find the blackjack table message to update it. "
                    "Check the table with the newest blackjack message, or click its buttons instead.",
                )
                return

            embed, file, view = await self.build_response(key, note=note, celebrate=True)
            finished = game.phase == "finished"
            try:
                await table_message.edit(embed=embed, attachments=[file], view=view)
            except discord.HTTPException as exc:
                logger.warning("Blackjack shortcut edit failed for key=%s: %s", key, exc, exc_info=exc)
                # Same as handle_action: the action was already applied and saved
                # above, so leave the game recoverable via the next click instead of
                # desyncing the message from the real, already-persisted state.
                return
            game.message_id = table_message.id
            if view:
                view.message = table_message
                self.game_messages[key] = table_message
            if finished:
                self.schedule_finished_cleanup(key, game)
            else:
                await self.save_active_game(key)

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

        return True, note

    async def send_shortcut_error(self, message: discord.Message, text: str) -> None:
        try:
            await message.channel.send(f"{message.author.mention} {text}", delete_after=6)
        except discord.HTTPException:
            pass

    def stale_hand_embed(self, reason: str) -> discord.Embed:
        embed = discord.Embed(
            title="⌛ Blackjack hand unavailable",
            description=f"{reason}\n\nNo additional money was charged by this click. Start a new hand with `/blackjack`.",
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text=RULES_TEXT)
        return embed

    async def expire_stale_interaction(self, interaction: discord.Interaction, reason: str) -> None:
        embed = self.stale_hand_embed(reason)
        try:
            await interaction.response.edit_message(embed=embed, attachments=[], view=None)
        except discord.HTTPException:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "That blackjack hand is no longer active. Start a new hand with `/blackjack`.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "That blackjack hand is no longer active. Start a new hand with `/blackjack`.",
                    ephemeral=True,
                )

    async def handle_timeout(self, key: tuple[int, int], message: discord.Message, version: int) -> None:
        async with self.lock_for(key):
            # Ignore stale timeouts from an old View that was replaced after a player action.
            if self.view_versions.get(key) != version:
                return

            game = await self.load_active_game(key)
            if not game:
                note = self.pop_corrupt_refund_note(key)
                if note:
                    try:
                        await message.channel.send(note, delete_after=15)
                    except discord.HTTPException:
                        pass
                return
            if game.phase == "finished":
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

            await self.save_active_game(key)
            embed, file, view = await self.build_response(key, note=note, celebrate=True)
            finished = game.phase == "finished"
            try:
                await message.edit(embed=embed, attachments=[file], view=view)
            except discord.HTTPException as exc:
                logger.warning("Blackjack timeout edit failed for key=%s, version=%s: %s", key, version, exc, exc_info=exc)
                # Leave the finished game in memory so the next stale button click can
                # recover and render the final result instead of showing an active table.
                return

            game.message_id = message.id
            if view:
                view.message = message
                self.game_messages[key] = message
            if finished:
                self.schedule_finished_cleanup(key, game)
            else:
                await self.save_active_game(key)

    async def settle_finished_game(self, user_id: int, game: BlackjackGame) -> str:
        first_settlement = not game.settled
        credited = game.settle()
        if credited:
            balance = await self.db.add_balance(user_id, credited, "blackjack_settlement")
        else:
            balance = await self.db.get_balance(user_id)

        deck_note = ""
        if first_settlement:
            blackjacks_hit = sum(1 for hand in game.hands if hand.is_blackjack)
            busts = sum(1 for hand in game.hands if hand.busted or hand.value > 21)
            await self.db.record_round_stats(
                user_id,
                hand_results=game.hand_results,
                blackjacks_hit=blackjacks_hit,
                busts=busts,
                busts_prevented=game.busts_prevented,
                wagered_cents=game.total_wagered_cents,
                profit_cents=game.settlement_net_cents,
            )

            shoe = await self.shoe_for_user(user_id)
            reshuffled = shoe.finish_hand(game.cards_in_play())
            await self.save_shoe_for_user(user_id)
            if reshuffled:
                deck_note = "\n\n🔄 Deck reached the cut card and was reshuffled for the next hand."

        return self.format_finished_result(game, balance, deck_note=deck_note)

    def build_daily_bonus_embed(self, amount_cents: int, streak: int) -> discord.Embed:
        embed = discord.Embed(
            title="🎁 Your first Blackjack game today!",
            description=f"You have been awarded **{money(amount_cents)}** before the cards are dealt.",
            color=discord.Color.gold(),
        )
        streak_text = f"🔥 {streak} day streak"
        if amount_cents >= DAILY_BONUS_MAX_CENTS:
            streak_text += " • max bonus reached!"
        embed.add_field(name="Sign-in Streak", value=streak_text, inline=False)
        embed.set_footer(text="Daily bonus resets at midnight PT. Miss a day and your streak resets.")
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
                if hand.doubled:
                    result += " (doubled)"
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

    def active_description_for_game(self, game: BlackjackGame) -> str | None:
        if game.phase == "insurance":
            return "Dealer shows an Ace. Would you like insurance?"
        if len(game.hands) > 1:
            return f"Playing Hand {game.active_hand_index + 1} of {len(game.hands)}."
        return None

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
        text = text or ("Waiting for your decision." if game.phase != "insurance" else "Insurance decision pending.")

        if len(game.hands) > 1:
            bet_text = " • ".join(
                f"Hand {idx}: {money(hand.bet_cents)}{' (doubled)' if hand.doubled else ''}"
                for idx, hand in enumerate(game.hands, start=1)
            )
        else:
            hand = game.hands[0] if game.hands else None
            bet_text = money(hand.bet_cents) if hand else money(game.bet_cents)
            if hand and hand.doubled:
                bet_text += " (doubled)"

        return f"{text} Your Bet: {bet_text}"

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

    async def build_response(
        self, key: tuple[int, int], *, note: str, celebrate: bool = False
    ) -> tuple[discord.Embed, discord.File, BlackjackView | None]:
        game = self.games[key]
        balance = await self.db.get_balance(game.user_id)
        shoe = await self.shoe_for_user(game.user_id)
        player_name = self.display_name_for_game(game)

        natural_win = (
            celebrate
            and game.phase == "finished"
            and game.settlement_net_cents > 0
            and any(hand.is_blackjack for hand in game.hands)
        )
        if natural_win:
            # The celebration GIF takes well over a second of CPU-bound Pillow
            # work (compositing ~19 frames) - run it off the event loop so it
            # can't stall every other user's concurrent interaction while it renders.
            image = await asyncio.to_thread(
                self.renderer.render_natural_blackjack_gif,
                game,
                payout_cents=game.settlement_net_cents,
                player_name=player_name,
            )
            image_filename = "blackjack_table.gif"
        else:
            image = await asyncio.to_thread(
                self.renderer.render_png, game, note=note, shoe=shoe, player_name=player_name
            )
            image_filename = "blackjack_table.png"
        file = discord.File(image, filename=image_filename)

        if game.phase == "finished":
            title = self.finished_title_for_game(game)
            description = self.finished_description_for_game(game, balance_cents=balance)
        else:
            title = self.active_title_for_game(game)
            # Fold the status text (note + bet) directly into the description
            # instead of a separate "Status" field - Discord always reserves a
            # line for a field's name, even a blank one, which showed up as an
            # unwanted gap between the title and this text.
            context = self.active_description_for_game(game)
            status = self.active_status_text(note, game)
            description = f"{context}\n{status}" if context else status

        embed = discord.Embed(title=title, description=description, color=self.embed_color_for_game(game))
        if game.phase != "finished":
            embed.add_field(name="Available Actions", value=self.active_actions_text_for_game(game), inline=False)
            embed.set_footer(text=f"Action timer: {PLAYER_ACTION_TIMEOUT_SECONDS}s • Insurance timer: {INSURANCE_TIMEOUT_SECONDS}s • {RULES_TEXT}")
        else:
            embed.set_footer(text=RULES_TEXT)

        embed.set_image(url=f"attachment://{image_filename}")

        # Whenever we're about to replace the view attached to this key's message
        # (or stop attaching one at all, because the hand finished), stop the
        # previous view so its independent 30s timeout timer can never fire again.
        # Without this, a stale view's timeout could still land after this key has
        # moved on to a different hand entirely.
        old_view = self.active_views.pop(key, None)
        if old_view is not None:
            old_view.stop()

        view = None
        if game.phase != "finished":
            view = BlackjackView(self, key, balance, self.bump_view_version(key))
            self.active_views[key] = view

        return embed, file, view

    async def resolve_display_name(self, user_id: int, guild: discord.Guild | None) -> str:
        if guild is not None:
            member = guild.get_member(user_id)
            if member is not None:
                return member.display_name

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                user = None

        if user is not None:
            return getattr(user, "display_name", None) or getattr(user, "global_name", None) or user.name
        return f"User {user_id}"

    async def resolve_guild_member(
        self, guild: discord.Guild, user_id: int, cache: dict[int, discord.Member | None]
    ) -> discord.Member | None:
        """Resolve a user_id to a Member of `guild`, or None if they aren't a member.

        Used to filter the (global) blackjack leaderboard down to just this
        server, since the members gateway intent isn't enabled and guild.members
        can't be trusted to be a complete cache.
        """
        if user_id in cache:
            return cache[user_id]

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                member = None

        cache[user_id] = member
        return member

    def format_leaderboard_value(self, value: float, fmt: str) -> str:
        if fmt == "pct":
            return f"{value:.1f}%"
        if fmt == "money":
            return money(int(value))
        return str(int(value))

    @app_commands.command(
        name="blackjack_leaderboard",
        description="View the blackjack leaderboard across several fun categories.",
    )
    @app_commands.guild_only()
    async def blackjack_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild
        member_cache: dict[int, discord.Member | None] = {}

        embed = discord.Embed(title="🃏 Blackjack Leaderboard", color=discord.Color.gold())
        for metric, label, fmt in LEADERBOARD_CATEGORIES:
            candidates = await self.db.get_leaderboard(metric, limit=LEADERBOARD_CANDIDATE_LIMIT)

            lines = []
            for row in candidates:
                member = await self.resolve_guild_member(guild, row["user_id"], member_cache)
                if member is None:
                    continue
                medal = LEADERBOARD_MEDALS[len(lines)] if len(lines) < len(LEADERBOARD_MEDALS) else "•"
                value_text = self.format_leaderboard_value(row["value"], fmt)
                lines.append(f"{medal} **{member.display_name}** — {value_text}")
                if len(lines) >= len(LEADERBOARD_MEDALS):
                    break

            if not lines:
                embed.add_field(name=label, value="No qualifying players yet.", inline=False)
                continue
            embed.add_field(name=label, value="\n\n".join(lines), inline=False)

        embed.set_footer(text="Win %/ROI % require a minimum sample size to qualify. Play /blackjack to climb the board!")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="blackjack_stats",
        description="View your own blackjack stats.",
    )
    async def blackjack_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        stats = await self.db.get_stats(interaction.user.id)
        balance = await self.db.get_balance(interaction.user.id)

        decided = stats["hands_won"] + stats["hands_lost"]
        win_pct = (100.0 * stats["hands_won"] / decided) if decided else 0.0
        roi_pct = (100.0 * stats["total_profit_cents"] / stats["total_wagered_cents"]) if stats["total_wagered_cents"] else 0.0
        profit_cents = stats["total_profit_cents"]
        profit_text = f"{'+' if profit_cents >= 0 else '-'}{money(abs(profit_cents))}"

        embed = discord.Embed(
            title=f"📊 Blackjack Stats — {interaction.user.display_name}",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="🃏 Hands",
            value=(
                f"**Played:** `{stats['hands_played']}`\n\n"
                f"**Won / Lost / Pushed:** `{stats['hands_won']} / {stats['hands_lost']} / {stats['hands_pushed']}`\n\n"
                f"**Win %:** `{win_pct:.1f}%`\n\n"
                f"**Blackjacks Hit:** `{stats['blackjacks_hit']}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="​",
            value="​",
            inline=False,
        )
        embed.add_field(
            name="🔥 Streaks",
            value=(
                f"**Current Win Streak:** `{stats['current_win_streak']}`\n\n"
                f"**Best Win Streak:** `{stats['best_win_streak']}`\n\n"
                f"**Daily Sign-in Streak:** `{stats['daily_streak']}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="​",
            value="​",
            inline=False,
        )
        embed.add_field(
            name="🛡️ Discipline",
            value=(
                f"**Busts:** `{stats['busts']}`\n\n"
                f"**Busts Prevented:** `{stats['busts_prevented']}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="​",
            value="​",
            inline=False,
        )
        embed.add_field(
            name="💰 Money",
            value=(
                f"**Balance:** {money(balance)}\n\n"
                f"**Total Wagered:** {money(stats['total_wagered_cents'])}\n\n"
                f"**Lifetime P/L:** {profit_text}\n\n"
                f"**ROI:** `{roi_pct:.1f}%`\n\n"
                f"**Biggest Win:** {money(stats['biggest_win_cents'])}"
            ),
            inline=False,
        )

        embed.set_footer(text="Check /blackjack_leaderboard to see how you stack up.")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot, db_path: str = DEFAULT_DB_PATH):
    await bot.add_cog(BlackjackCog(bot, db_path=db_path))
