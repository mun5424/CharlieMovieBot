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
from .rules import DEFAULT_MODE, RULE_SETS, RuleSet, get_rules
from .shoe import Shoe

logger = logging.getLogger(__name__)

MIN_BET_CENTS = 1_000
MAX_BET_DOLLARS = 1_000_000
# How long a finished table keeps its "Deal Again"/"Change Bet" buttons. Kept
# deliberately short and non-persistent: a finished table message sits in channel
# scrollback forever, so permanently live replay buttons would be a permanent
# stale-click surface (someone scrolling back days later dealing a surprise hand).
# After this, on_timeout strips them and the message becomes an inert receipt.
REPLAY_TIMEOUT_SECONDS = 180
# How long the /blackjack_options picker stays interactive.
MODE_OPTIONS_TIMEOUT_SECONDS = 300
DAILY_BONUS_BASE_CENTS = 10_000
DAILY_BONUS_STREAK_STEP_CENTS = 2_500
DAILY_BONUS_PLATEAU_CENTS = 20_000
DAILY_BONUS_EXTENDED_STEP_CENTS = 1_000
DEFAULT_DB_PATH = "bot.db"
TIMEZONE = "America/Los_Angeles"
PLAYER_ACTION_TIMEOUT_SECONDS = 30
INSURANCE_TIMEOUT_SECONDS = 10
FINISHED_GAME_GRACE_SECONDS = 10 * 60
# A view's on_timeout only fires once, so unlike a failed edit from a player
# action (where the previous view is still live and will simply time out
# again later), a failed edit here has no live timer left to fall back on.
# Retry a bounded number of times before giving up so a purely transient
# Discord error (a network blip, a momentarily unreachable webhook) doesn't
# permanently strand the hand with no working recovery path.
TIMEOUT_EDIT_RETRY_SECONDS = 15
TIMEOUT_EDIT_MAX_RETRIES = 6

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
# Fallback footer for surfaces with no game in hand (e.g. a stale-hand notice).
# A live table uses game.rules.rules_line() so the footer matches the mode dealt.
RULES_TEXT = get_rules(DEFAULT_MODE).rules_line()

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


MODE_CHOICES = [
    app_commands.Choice(name=f"{r.name} · {r.house} ({r.payout_label})", value=r.key)
    for r in RULE_SETS.values()
]


def mode_label(rules: RuleSet) -> str:
    return f"{rules.name} · {rules.house}"


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
    def __init__(self, cog: "BlackjackCog", key: tuple[int, int], balance_cents: int):
        game = cog.games.get(key)
        super().__init__(timeout=timeout_for_game(game))
        self.cog = cog
        self.key = key
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
        # Every gate comes from the game rather than the hand alone: the mode
        # decides the hand limit, resplit-aces and double-after-split, and a split
        # ace can only ever split (never hit or double).
        self.hit_button.disabled = not game.can_hit_active
        self.stand_button.disabled = False
        self.double_button.disabled = not game.can_double_active or balance_cents < hand.bet_cents
        self.split_button.disabled = not game.can_split_active or balance_cents < hand.bet_cents

    async def on_timeout(self) -> None:
        # Timeout means: no player response, so either skip insurance or stand the active hand.
        # Passing `self` lets handle_timeout check identity against self.active_views[key] -
        # an old/replaced view whose timeout still fires late is recognized as stale because
        # it's no longer the object stored there, not because some separately-tracked counter
        # says so.
        if self.message is None:
            return
        await self.cog.handle_timeout(self.key, self.message, self)

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


class ReplayView(discord.ui.View):
    """Buttons offered on a *finished* table: replay the same bet, or pick a new one.

    The bet amount lives in the button label (`Deal Again ($25)`) rather than being
    asked about, so the player always sees what is about to leave their balance
    before they tap. See REPLAY_TIMEOUT_SECONDS for why this view is short-lived
    and deliberately not persistent across restarts."""

    def __init__(self, cog: "BlackjackCog", key: tuple[int, int], bet_cents: int):
        super().__init__(timeout=REPLAY_TIMEOUT_SECONDS)
        self.cog = cog
        self.key = key
        self.owner_id = key[0]
        self.bet_cents = bet_cents
        self.message: discord.Message | None = None
        self.deal_again_button.label = f"Deal Again ({money(bet_cents)})"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This blackjack hand belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        # Same reasoning as BlackjackView.on_error: discord.py's default only
        # prints to stderr, which this bot's logging setup does not capture.
        logger.exception(
            "Blackjack replay button error: key=%s item=%s message_id=%s bet_cents=%s",
            self.key,
            getattr(item, "label", item),
            getattr(interaction.message, "id", None),
            self.bet_cents,
            exc_info=error,
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong starting that hand. Please try `/blackjack` instead.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Something went wrong starting that hand. Please try `/blackjack` instead.",
                    ephemeral=True,
                )
        except discord.HTTPException:
            pass

    async def retire(self) -> None:
        """Stop the timer and strip these buttons off the finished table message."""
        self.stop()
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except (discord.HTTPException, AttributeError):
            pass

    async def on_timeout(self) -> None:
        await self.retire()

    async def replay(self, interaction: discord.Interaction, bet_cents: int) -> None:
        """Shared tail of both buttons: try to deal `bet_cents`, retire on success."""
        # start_hand re-checks the balance, the bet bounds and whether this player
        # already has a live hand, so a stale click from scrollback hits exactly the
        # same guard rails as /blackjack. Only retire the buttons once a hand was
        # really dealt - on a rejection (usually: this bet now exceeds their balance)
        # leaving them up is what gives the player Change Bet as a way to correct it.
        dealt = await self.cog.start_hand(interaction, bet_cents)
        if dealt:
            await self.retire()

    @discord.ui.button(label="Deal Again", style=discord.ButtonStyle.secondary, row=0)
    async def deal_again_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.replay(interaction, self.bet_cents)

    @discord.ui.button(label="Change Bet", style=discord.ButtonStyle.secondary, row=0)
    async def change_bet_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        # A modal has to be the FIRST response to an interaction - it cannot follow a
        # defer() - so this click only opens the modal. The modal *submit* is then a
        # separate interaction, and that is the one start_hand defers and deals on.
        await interaction.response.send_modal(ChangeBetModal(self))


class ChangeBetModal(discord.ui.Modal, title="Change Your Bet"):
    def __init__(self, view: ReplayView):
        super().__init__()
        self.replay_view = view
        # Built per instance rather than as a class attribute: a class-level
        # TextInput is shared by every instance of the modal, so prefilling
        # `default` with this player's last bet there would leak it into another
        # player's modal opened at the same time.
        self.bet_input = discord.ui.TextInput(
            label="Bet amount in dollars",
            default=str(view.bet_cents // 100),
            placeholder=f"{MIN_BET_CENTS // 100} - {MAX_BET_DOLLARS}",
            min_length=1,
            max_length=9,
            required=True,
        )
        self.add_item(self.bet_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.bet_input.value.strip().lstrip("$").replace(",", "").replace("_", "")
        try:
            dollars = int(raw)
        except ValueError:
            await interaction.response.send_message(
                f"`{self.bet_input.value.strip()}` isn't a whole dollar amount. "
                f"Enter a number between {MIN_BET_CENTS // 100} and {MAX_BET_DOLLARS}.",
                ephemeral=True,
            )
            return
        # Bounds/balance are start_hand's job - it owns the same checks /blackjack
        # uses, so the two entry points can't drift apart.
        await self.replay_view.replay(interaction, dollars * 100)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception(
            "Blackjack change-bet modal error: key=%s raw=%r",
            self.replay_view.key,
            getattr(self.bet_input, "value", None),
            exc_info=error,
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong with that bet. Please try `/blackjack` instead.", ephemeral=True
                )
        except discord.HTTPException:
            pass


class ModeSelect(discord.ui.Select):
    """Rule browser and default-setter in one. Each option's description carries
    the headline rules, so the picker itself is the comparison table."""

    def __init__(self, cog: "BlackjackCog", current: str):
        options = [
            discord.SelectOption(
                label=mode_label(r),
                value=r.key,
                description=f"{r.payout_label} • {'H17' if r.dealer_hits_soft_17 else 'S17'} • "
                            f"split to {r.max_hands}{' • RSA' if r.resplit_aces else ''}"[:100],
                default=(r.key == current),
            )
            for r in RULE_SETS.values()
        ]
        super().__init__(placeholder="Choose a table…", options=options, min_values=1, max_values=1)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.apply_mode_choice(interaction, self.values[0])


class ModeOptionsView(discord.ui.View):
    def __init__(self, cog: "BlackjackCog", owner_id: int, current: str):
        super().__init__(timeout=MODE_OPTIONS_TIMEOUT_SECONDS)
        self.owner_id = owner_id
        self.message: discord.Message | None = None
        self.add_item(ModeSelect(cog, current))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Run `/blackjack_options` yourself to pick your table.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except (discord.HTTPException, AttributeError):
            pass


class BlackjackCog(commands.Cog):
    def __init__(self, bot: commands.Bot, *, db_path: str = DEFAULT_DB_PATH):
        self.bot = bot
        self.db = BlackjackDB(db_path)
        self.renderer = CardRenderer(Path(__file__).parent / "assets" / "cards")
        self.games: dict[tuple[int, int], BlackjackGame] = {}
        self.locks: dict[tuple[int, int], asyncio.Lock] = {}
        self.game_messages: dict[tuple[int, int], discord.Message] = {}
        self.active_views: dict[tuple[int, int], BlackjackView] = {}
        self.replay_views: dict[tuple[int, int], ReplayView] = {}
        self.finished_cleanup_tasks: dict[tuple[int, int], asyncio.Task] = {}
        # Keyed by (user_id, mode): a 2-deck shoe and an 8-deck shoe are different
        # objects, and switching modes must never deal one game's cards into another.
        self.shoes: dict[tuple[int, str], Shoe] = {}
        self.corrupt_state_refunds: dict[tuple[int, int], int] = {}
        self._startup_reconciled = False

    async def cog_load(self) -> None:
        await self.db.init()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # cog_load runs before the bot has even logged in (BotManager loads
        # cogs, then starts the bot), so it's too early to fetch/edit Discord
        # messages here - do it on the first on_ready instead, once the HTTP
        # client and gateway cache are actually usable. Guard against
        # reconnects firing this again after the first successful run.
        if self._startup_reconciled:
            return
        self._startup_reconciled = True
        await self.reconcile_active_games_on_startup()

    async def reconcile_active_games_on_startup(self) -> None:
        """Every hand left in blackjack_active_games belongs to the *previous*
        process life - self.games/self.active_views are all empty here, so
        none of those hands have a working Discord View
        anymore, and none have a timer ticking. A hand that finished but never
        got torn down (its final edit failed right before this restart) would
        otherwise rot in the DB until the player's next /blackjack call. Worse,
        a hand that was still mid-turn when the bot restarted (a deploy, a
        crash, the watchdog failsafe) is left with dead buttons and no timeout
        to resolve it: the player can't act (clicks silently fail with no
        view registered to handle them) and can't start a new hand either
        (blocked by the "already have an active hand" check) - a permanent
        hang with no self-healing path short of scripts/flush_blackjack_hands.py.
        Fix both by re-hydrating a live view (or cleaning up) here, once,
        right after the bot actually comes online.
        """
        try:
            rows = await self.db.get_all_active_games()
        except Exception:
            logger.exception("Blackjack: failed to load active games for startup reconciliation")
            return

        for user_id, state in rows:
            try:
                await self._reconcile_one_active_game(user_id, state)
            except Exception:
                # One malformed/unreachable row must never abort reconciliation
                # for every other user's hand.
                logger.exception("Blackjack: startup reconciliation failed for user_id=%s", user_id)

    async def _reconcile_one_active_game(self, user_id: int, state: dict) -> None:
        key = (user_id, 0)
        # Locked like every other mutation of self.games/the DB row for this key,
        # in case a real player interaction (e.g. a fresh /blackjack) lands while
        # this is still running - startup reconciliation races the live bot from
        # the moment on_ready fires.
        async with self.lock_for(key):
            if key in self.games:
                # A real interaction already loaded/handled this key while we
                # were waiting for the lock - nothing left for us to do.
                return
            # Re-fetch rather than trust the `state` snapshot taken before the
            # lock: a real interaction could have changed or deleted this row
            # in the meantime even without populating self.games (e.g. it hit
            # the corrupt-state recovery path and deleted the row itself).
            current_state = await self.db.get_active_game(user_id)
            if current_state is None:
                return
            await self._reconcile_one_active_game_locked(user_id, current_state)

    async def _reconcile_one_active_game_locked(self, user_id: int, state: dict) -> None:
        key = (user_id, 0)
        try:
            game = self.game_from_active_state(state)
        except Exception as exc:
            logger.error(
                "Blackjack: active-game state could not be restored for user_id=%s at startup: %s",
                user_id, exc, exc_info=exc,
            )
            await self.delete_active_game_for_key(key)
            await self.refund_unrestorable_game(key, state)
            return

        self.games[key] = game

        if game.phase == "finished":
            if not game.settled:
                # A hand can reach phase="finished" and get persisted (blackjack()
                # saves right after BlackjackGame.start(), and apply_action_locked
                # saves right after an action finishes the hand) *before*
                # settle_finished_game() runs to actually credit the payout and
                # flip settled=True. A crash/restart in that window would
                # otherwise have this branch delete the row via
                # cleanup_game_and_db without ever paying the player - settle
                # first so their stake/winnings aren't silently lost.
                logger.warning(
                    "Blackjack: found an unsettled finished hand at startup for user_id=%s; settling before cleanup.",
                    user_id,
                )
                await self.settle_finished_game(user_id, game)
            asyncio.create_task(self.disable_stale_message(key, game))
            await self.cleanup_game_and_db(key)
            return

        table_message = await self.resolve_table_message(key, game)
        if table_message is None:
            logger.warning(
                "Blackjack: could not locate the table message for an orphaned in-progress "
                "hand at startup: user_id=%s message_id=%s channel_id=%s. Leaving it in the "
                "DB for scripts/flush_blackjack_hands.py.",
                user_id, game.message_id, game.channel_id,
            )
            return

        try:
            embed, file, view = await self.build_response(
                key,
                note="🔄 The bot restarted, but your hand was saved. Pick up right where you left off.",
            )
        except Exception:
            logger.exception("Blackjack: failed to render an orphaned in-progress hand for user_id=%s at startup", user_id)
            return

        try:
            await table_message.edit(embed=embed, attachments=[file], view=view)
        except discord.HTTPException as exc:
            logger.warning(
                "Blackjack: failed to re-attach a live view for user_id=%s at startup: %s",
                user_id, exc, exc_info=exc,
            )
            self.discard_pending_view(view)
            return

        game.message_id = table_message.id
        self.commit_view(key, view)
        if view:
            view.message = table_message
            self.game_messages[key] = table_message
        await self.save_active_game(key)
        logger.info("Blackjack: re-attached a live view for an orphaned in-progress hand: user_id=%s", user_id)

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

    def cleanup_game(self, key: tuple[int, int]) -> None:
        task = self.finished_cleanup_tasks.pop(key, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self.games.pop(key, None)
        self.game_messages.pop(key, None)
        old_view = self.active_views.pop(key, None)
        if old_view is not None:
            old_view.stop()
        # Replay buttons belong to the hand that just ended, so they die with it.
        # Only the timer is stopped here (this method is sync and can't await an
        # edit); the message itself is stripped by ReplayView.retire() on its own
        # timeout, or by disable_stale_message when a new hand takes over the key.
        # That relies on REPLAY_TIMEOUT_SECONDS < FINISHED_GAME_GRACE_SECONDS: if
        # the grace period ever became the shorter of the two, this stop() would
        # land first and leave buttons that are visible but no longer wired to
        # anything ("This interaction failed" on click).
        old_replay = self.replay_views.pop(key, None)
        if old_replay is not None:
            old_replay.stop()
        # Popping `old_view` out of self.active_views here is what makes a stale
        # timeout harmless even without the .stop() above: handle_timeout/
        # _render_timeout_result check identity against self.active_views[key],
        # so once this key moves on to a brand new hand (even one reusing the
        # same key), no old view can ever be `is` whatever gets stored next -
        # no counter or reset bookkeeping needed for that guarantee to hold.

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
                    # Never persisted before modes: a split ace's "one card only"
                    # status had no effect once both hands were auto-stood. Under
                    # resplit-aces it gates doubling and hitting, so a hand restored
                    # after a restart would otherwise become illegally playable.
                    "split_aces": hand.split_aces,
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
            "mode": game.mode,
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
                    split_aces=bool(hand_state.get("split_aces", False)),
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
            # Hands persisted before modes existed carry no mode and are, by
            # definition, the original single-deck game.
            mode=str(state.get("mode") or DEFAULT_MODE),
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

    async def mode_for_user(self, user_id: int) -> str:
        return await self.db.get_mode(user_id)

    async def shoe_for_user(self, user_id: int, mode: str = DEFAULT_MODE) -> Shoe:
        key = (user_id, mode)
        shoe = self.shoes.get(key)
        if shoe is not None:
            return shoe

        state = await self.db.get_shoe_state(user_id, mode)
        if state is None:
            shoe = Shoe.fresh(mode, "new shoe")
        else:
            try:
                shoe = Shoe(
                    mode=mode,
                    deck=[card_from_code(code) for code in state["deck"]],
                    discard=[card_from_code(code) for code in state["discard"]],
                    hands_played=int(state["hands_played"]),
                    last_shuffle_reason=str(state["last_shuffle_reason"]),
                    # Re-roll only if this row predates the persisted cut card;
                    # an in-play shoe keeps the depth it was actually cut at.
                    cut_card_at=int(state.get("cut_card_at") or 0) or get_rules(mode).cut_card_at(),
                )
            except Exception:
                # If old/corrupt shoe data ever exists, fail safe with a fresh deck.
                shoe = Shoe.fresh(mode, "recovered from invalid shoe state")

        self.shoes[key] = shoe
        return shoe

    async def save_shoe_for_user(self, user_id: int, mode: str = DEFAULT_MODE) -> None:
        shoe = await self.shoe_for_user(user_id, mode)
        await self.db.save_shoe_state(
            user_id=user_id,
            deck_codes=[card_to_code(card) for card in shoe.deck],
            discard_codes=[card_to_code(card) for card in shoe.discard],
            hands_played=shoe.hands_played,
            last_shuffle_reason=shoe.last_shuffle_reason,
            mode=mode,
            cut_card_at=shoe.cut_card_at,
        )

    def mode_embed(self, rules: RuleSet, *, current: str, note: str = "") -> discord.Embed:
        is_current = rules.key == current
        embed = discord.Embed(
            title=f"🎲 {mode_label(rules)}",
            description="\n".join(rules.detail_lines()),
            color=discord.Color.green() if is_current else discord.Color.blurple(),
        )
        if note:
            embed.add_field(name="\u200b", value=note, inline=False)
        embed.set_footer(text="Your table is saved and used for every /blackjack hand until you change it.")
        return embed

    async def apply_mode_choice(self, interaction: discord.Interaction, chosen: str) -> None:
        user_id = interaction.user.id
        key = self.key_for(interaction)
        rules = get_rules(chosen)

        # A live hand belongs to the mode it was dealt from - switching underneath
        # it would settle that hand against a different rule sheet and a different
        # shoe. Make the player finish or time out first.
        async with self.lock_for(key):
            active = await self.load_active_game(key)
            if active is not None and active.phase != "finished":
                await interaction.response.send_message(
                    f"Finish your current {mode_label(active.rules)} hand before switching tables.",
                    ephemeral=True,
                )
                return

        previous = await self.mode_for_user(user_id)
        await self.db.set_mode(user_id, rules.key)

        if previous == rules.key:
            note = "Already your table - nothing changed."
        else:
            # Each mode keeps its own shoe, so this is a genuine sit-down at a
            # different table rather than a reshuffle of the one they just left.
            note = f"Switched from **{mode_label(get_rules(previous))}**. Your {mode_label(rules)} shoe picks up where you left it."
        await interaction.response.edit_message(embed=self.mode_embed(rules, current=rules.key, note=note), view=None)

    @app_commands.command(
        name="blackjack_options",
        description="Pick which blackjack table you play: single deck, Pechanga double deck, or Mandalay Bay 8-deck.",
    )
    async def blackjack_options(self, interaction: discord.Interaction) -> None:
        current = await self.mode_for_user(interaction.user.id)
        rules = get_rules(current)
        view = ModeOptionsView(self, interaction.user.id, current)
        await interaction.response.send_message(
            embed=self.mode_embed(rules, current=current, note="**Currently your table.** Pick another below to switch."),
            view=view,
            ephemeral=True,
        )
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            pass

    @app_commands.command(name="blackjack", description="Play blackjack at your chosen table. Change tables with /blackjack_options.")
    @app_commands.describe(bet="Bet amount in dollars. Minimum $10.")
    async def blackjack(self, interaction: discord.Interaction, bet: app_commands.Range[int, 10, MAX_BET_DOLLARS] = 10):
        await self.start_hand(interaction, bet * 100)

    async def start_hand(self, interaction: discord.Interaction, bet_cents: int) -> bool:
        """Deal a fresh hand for `interaction`'s user, or explain why it can't.

        The single entry point for dealing, shared by /blackjack and the finished
        table's replay buttons, so both go through the same active-hand guard, daily
        bonus claim, bet-bounds check and balance check. Split into its own method
        precisely so those can't drift apart - a replay path with its own copy of
        this would, for instance, quietly stop claiming the daily bonus.

        Returns True only if a hand was actually dealt. A False return means the
        caller's interaction has already been answered with the reason.
        """
        key = self.key_for(interaction)
        # Normalise through get_rules: a stored preference naming a retired mode
        # falls back to the default, and everything downstream (the shoe key here,
        # game.mode, settle, build_response) must agree on which mode that is.
        rules = get_rules(await self.mode_for_user(interaction.user.id))
        mode = rules.key
        async with self.lock_for(key):
            existing_game = await self.load_active_game(key)

            # A finished predecessor doesn't block a new hand, but do NOT tear it
            # down yet - every rejection below still has to leave the player
            # looking at that table. Tearing it down here (disable_stale_message
            # strips its buttons, cleanup_game stops its ReplayView) would take
            # the Change Bet button away before the player is told their bet was
            # refused, which is exactly the button they need to fix it with.
            # Retired once this call is actually committed to dealing instead.
            finished_predecessor = None
            if existing_game is not None and existing_game.phase == "finished":
                finished_predecessor = existing_game
                existing_game = None

            if existing_game is not None:
                active_channel = existing_game.channel_id
                if active_channel:
                    await interaction.response.send_message(
                        f"You already have an active blackjack hand in <#{active_channel}>.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message("You already have an active blackjack hand.", ephemeral=True)
                return False

            # Bet bounds are checked before anything with a side effect. /blackjack's
            # app_commands.Range enforces them too, but the Change Bet modal takes
            # free text, so this is the check that actually holds there - and a typo'd
            # amount must not be able to burn the player's once-a-day bonus claim.
            if bet_cents < rules.min_bet_cents:
                await interaction.response.send_message(
                    f"Minimum bet on {mode_label(rules)} is {money(rules.min_bet_cents)}.", ephemeral=True
                )
                return False
            if bet_cents > MAX_BET_DOLLARS * 100:
                await interaction.response.send_message(
                    f"Maximum blackjack bet is {money(MAX_BET_DOLLARS * 100)}.", ephemeral=True
                )
                return False

            day = today_key()
            bonus_claimed, bonus_amount_cents, bonus_streak = await self.db.claim_daily_bonus(
                interaction.user.id,
                day,
                base_cents=DAILY_BONUS_BASE_CENTS,
                streak_step_cents=DAILY_BONUS_STREAK_STEP_CENTS,
                plateau_cents=DAILY_BONUS_PLATEAU_CENTS,
                extended_step_cents=DAILY_BONUS_EXTENDED_STEP_CENTS,
            )

            balance = await self.db.get_balance(interaction.user.id)
            if balance < bet_cents:
                # Names the bet as well as the balance: on the replay path the
                # shortfall is almost never about the minimum, and "you only have
                # $15, the minimum bet is $10" reads like $15 is somehow too small.
                msg = f"That bet is {money(bet_cents)} but you only have {money(balance)}."
                if bonus_claimed:
                    msg += f" I did apply your daily +{money(bonus_amount_cents)} bonus."
                await interaction.response.send_message(msg, ephemeral=True)
                return False

            # Committed to dealing from here on, so it's finally safe to retire the
            # finished predecessor and to consume the pending refund note (popping it
            # on a rejected bet would drop the notice before any hand could show it).
            refund_note = self.pop_corrupt_refund_note(key)
            if finished_predecessor is not None:
                # Best-effort: if that hand's final edit_message/table_message.edit
                # never went through (e.g. a transient Discord API failure), its
                # message may still show live buttons even though the hand is fully
                # settled. Strip them now, in the background, so that old message
                # can't later be clicked and mistaken for a still-active hand once
                # this new one takes over the key. Fire-and-forget so a slow/failed
                # Discord call here never delays this interaction's own response.
                asyncio.create_task(self.disable_stale_message(key, finished_predecessor))
                await self.cleanup_game_and_db(key)

            # Committed to dealing a hand now - defer before the slow render/send
            # work below (a natural blackjack's celebration GIF alone can take
            # over a second to render), so Discord always gets acked well within
            # its 3s window regardless of render time. Public/non-ephemeral:
            # Discord ties a followup's ephemeral-ness to how the *original*
            # response was deferred, not to flags passed on the followup call
            # itself - deferring ephemeral here silently made the real, public
            # hand table ephemeral too (confirmed against the live bot).
            #
            # thinking=True matters for the replay buttons: a *component*
            # interaction's plain defer() is a deferred message *update*, so the
            # edit_original_response below would overwrite the finished table the
            # button sits on instead of posting the new hand. thinking=True forces
            # the same deferred-channel-message that an application command always
            # gets, so all three entry points (slash, button, modal submit) land a
            # brand new table message and leave the old one intact as a receipt.
            await interaction.response.defer(thinking=True)

            shoe = await self.shoe_for_user(interaction.user.id, mode)
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
                mode=mode,
            )
            self.games[key] = game
            await self.save_active_game(key)

            note_parts: list[str] = []
            if refund_note:
                note_parts.append(refund_note)
            if shuffled_before_hand:
                note_parts.append(f"Fresh {rules.deck_label.lower()} shuffled.")

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
                    # no attribute 'is_finished'. build_response now returns a ReplayView
                    # for finished hands so this should never be None, but the guard stays
                    # as cheap insurance against that crash coming back.
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
                # False: the stake was refunded (or the hand was already settled), so
                # a replay caller should keep its buttons up as a way back in.
                return False

            game.message_id = msg.id
            await self.save_active_game(key)
            # Every other successful-render path (handle_action, handle_shortcut_action,
            # _render_timeout_result, startup reconciliation) calls commit_view() here -
            # this one didn't, leaving a freshly-dealt hand's first view unregistered in
            # self.active_views. handle_timeout checks identity against that dict, so an
            # unregistered view's own 30s/10s timeout would always look stale (it can
            # never be `is` an entry that was never stored) and get silently dropped - a
            # hand nobody ever clicks on would sit active forever with no auto-resolve.
            # Worse, since it was never registered, nothing ever .stop()'d its timer
            # either, so it stays ticking and can misfire against whatever a later
            # action or hand leaves in self.active_views for this key.
            self.commit_view(key, view)
            if view:
                view.message = msg
                self.game_messages[key] = msg
            # Was an `elif` on the old `view is None` meaning of "hand already
            # over". A finished hand now carries a (truthy) ReplayView, so this
            # has to be its own check or an instant blackjack would never get
            # its cleanup scheduled and would rot in blackjack_active_games.
            if game.phase == "finished":
                self.schedule_finished_cleanup(key, game)
            return True

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

    async def ack_interaction(
        self, interaction: discord.Interaction, action: str, key: tuple[int, int]
    ) -> bool:
        """Ack a component interaction up front; report whether its token survived.

        Discord invalidates an interaction token 3 seconds after the click, and it
        must never be this bot's job to race that. Returning False rather than
        raising is the point: a hand whose ack arrived too late still has to be
        played out and repainted through the table message, not abandoned
        half-applied.
        """
        try:
            await interaction.response.defer()
            return True
        except discord.NotFound as exc:
            # 10062 Unknown interaction - the click sat unacked past Discord's
            # window (gateway lag, or the bot was busy/restarting when it landed).
            logger.warning(
                "Blackjack interaction expired before it could be acked: action=%s key=%s "
                "message_id=%s (%s). Playing the action out against the table message instead.",
                action,
                key,
                getattr(interaction.message, "id", None),
                exc,
            )
            return False
        except discord.InteractionResponded:
            # Something upstream already answered it; the token is alive either way.
            return True
        except discord.HTTPException as exc:
            logger.warning(
                "Blackjack defer failed for action=%s key=%s: %s", action, key, exc, exc_info=exc
            )
            return False

    async def handle_action(self, interaction: discord.Interaction, key: tuple[int, int], action: str) -> None:
        # Ack first - before the lock, before the database, before any render.
        #
        # Discord kills the interaction token 3s after the click, and every step
        # below can outlast that: lock_for(key) may still be held by the player's
        # previous click that is mid animation-render, and load/apply/save each hop
        # onto the SQLite worker thread. Deferring *after* that work is what produced
        # the "404 Unknown interaction" failures in the logs, and it failed in the
        # worst possible place: apply_action_locked and save_active_game had already
        # run, so the card was dealt and the bet settled while the table froze
        # on screen with no hint anything had happened.
        #
        # A component interaction's plain defer() is a deferred *update*: no
        # placeholder message, nothing the player sees, and edit_original_response
        # below still edits the exact message the buttons sit on.
        acked = await self.ack_interaction(interaction, action, key)

        async def notify(text: str) -> None:
            """Private notice to the clicker, dropped if the token is already gone."""
            if not acked:
                logger.info("Blackjack notice not delivered (expired token) key=%s: %s", key, text)
                return
            try:
                await interaction.followup.send(text, ephemeral=True)
            except discord.HTTPException as exc:
                logger.warning("Blackjack notice failed for key=%s: %s", key, exc)

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
                await self.expire_stale_interaction(interaction, reason, acked=acked)
                return

            if interaction.message and game.message_id and interaction.message.id != game.message_id:
                logger.warning(
                    "Blackjack message_id mismatch on action=%s: key=%s clicked_message_id=%s "
                    "current_game_message_id=%s game_phase=%s active_view_id=%s",
                    action,
                    key,
                    interaction.message.id,
                    game.message_id,
                    game.phase,
                    id(self.active_views.get(key)),
                )
                await self.expire_stale_interaction(
                    interaction,
                    "This is an older blackjack table. Use the newest blackjack message or start a new hand.",
                    acked=acked,
                )
                return

            async def repaint(embed, file, view) -> discord.Message | None:
                """Push a rendered table out: through the interaction while its token
                is alive, straight at the message once it isn't. Returns the message
                that now carries the table, or None if there was nothing to edit."""
                if acked:
                    return await interaction.edit_original_response(
                        embed=embed, attachments=[file], view=view
                    )
                target = await self.resolve_table_message(key, game)
                if target is None:
                    return None
                return await target.edit(embed=embed, attachments=[file], view=view)

            if game.phase == "finished":
                # If a timeout already finished the hand but the timeout edit failed,
                # recover by rendering the final result now instead of leaving the
                # message looking playable.
                embed, file, view = await self.build_response(key, note="")
                try:
                    msg = await repaint(embed, file, view)
                except discord.HTTPException as exc:
                    logger.warning(
                        "Blackjack finished-table repaint failed for key=%s: %s", key, exc, exc_info=exc
                    )
                    self.discard_pending_view(view)
                    return
                if msg is None:
                    self.discard_pending_view(view)
                    return
                self.commit_view(key, view)
                if view:
                    # The repaint edited exactly the message this table lives on -
                    # that's what the replay view needs in order to strip its own
                    # buttons when it times out.
                    view.message = msg
                self.schedule_finished_cleanup(key, game)
                return

            success, note = await self.apply_action_locked(key, interaction.user.id, action)
            if not success:
                await notify(note)
                return

            await self.save_active_game(key)

            embed, file, view = await self.build_response(key, note=note, celebrate=True)
            finished = game.phase == "finished"
            try:
                msg = await repaint(embed, file, view)
            except discord.HTTPException as exc:
                logger.warning("Blackjack action edit failed for key=%s: %s", key, exc, exc_info=exc)
                # The action itself was already applied and saved above (and, if
                # finished, already settled/paid out) - only the Discord-side
                # confirmation failed. Deliberately do NOT commit_view here: the
                # previous view is still attached to the real, clickable message
                # and its own timeout is still ticking, so leaving it in place
                # keeps that timeout as a working safety net that can auto-stand
                # this hand later even if the player never clicks again. Just
                # discard this attempt's unused candidate view instead.
                self.discard_pending_view(view)
                return
            if msg is None:
                # Same reasoning as the HTTPException above: the hand is applied and
                # saved, only the repaint had nowhere to land.
                logger.warning(
                    "Blackjack action=%s applied for key=%s but no table message could be resolved to update",
                    action,
                    key,
                )
                self.discard_pending_view(view)
                return
            game.message_id = msg.id
            self.commit_view(key, view)
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
                if game.phase == "finished":
                    # There's no message left to recover via (unlike the HTTPException
                    # branch below, where the table message still exists and a later
                    # click can resync it) - without this, a hand that finishes via a
                    # shortcut whose table message got deleted/unreachable would sit in
                    # blackjack_active_games as "finished" forever, since nothing else
                    # ever schedules its cleanup.
                    self.schedule_finished_cleanup(key, game)
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
                # Also same as handle_action: don't commit_view - the previous
                # view (still attached to the real message) keeps its own
                # timeout ticking as a safety net, instead of both it and this
                # unattached candidate view going permanently inert.
                self.discard_pending_view(view)
                return
            game.message_id = table_message.id
            self.commit_view(key, view)
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
                if not game.can_hit_active:
                    return False, "A split ace takes one card and stands - you can't hit it."
                game.hit()
                note = "Hit."
            elif action == "stand":
                game.stand()
                note = "Stand."
            elif action == "double":
                if not game.can_double_active:
                    if hand.split_aces:
                        return False, "You can't double a split ace."
                    if not game.rules.double_after_split and hand.from_split:
                        return False, f"{mode_label(game.rules)} doesn't allow doubling after a split."
                    return False, "You can only double down on your first two cards."
                if balance < hand.bet_cents:
                    return False, "You do not have enough money to double down."
                await self.db.add_balance(user_id, -hand.bet_cents, "blackjack_double_down")
                game.double()
                note = "Double down. One card dealt, then stand."
            elif action == "split":
                if not game.can_split_active:
                    if len(game.hands) >= game.rules.max_hands:
                        return False, (
                            f"{mode_label(game.rules)} allows up to {game.rules.max_hands} hands, "
                            "and you're already there."
                        )
                    if hand.split_aces and not game.rules.resplit_aces:
                        return False, f"{mode_label(game.rules)} doesn't allow resplitting aces."
                    return False, "You can only split a matching pair."
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

    async def expire_stale_interaction(
        self, interaction: discord.Interaction, reason: str, *, acked: bool
    ) -> None:
        """Replace a dead hand's table with an explanation of why it's dead.

        Only ever called from handle_action, which acks the interaction before it
        does anything else - so by the time we get here the token has either been
        deferred as an update (`acked`) or is already past saving. There is no fresh
        response slot to open either way: it's finish the deferred update, or edit
        the message directly, and the message is also the fallback when the token
        turns out to have died between the ack and now.
        """
        embed = self.stale_hand_embed(reason)

        if acked:
            try:
                await interaction.edit_original_response(embed=embed, attachments=[], view=None)
                return
            except discord.HTTPException as exc:
                logger.warning(
                    "Blackjack stale-table update failed for key-message %s: %s; falling back to a direct edit",
                    getattr(interaction.message, "id", None),
                    exc,
                )

        message = interaction.message
        if message is None:
            return
        try:
            await message.edit(embed=embed, attachments=[], view=None)
        except discord.HTTPException as exc:
            logger.warning(
                "Blackjack stale-table direct edit failed for message %s: %s", message.id, exc
            )

    async def handle_timeout(self, key: tuple[int, int], message: discord.Message, origin_view: "BlackjackView") -> None:
        async with self.lock_for(key):
            # Ignore stale timeouts from an old View that was replaced after a player
            # action: `origin_view` is the specific View instance whose own timer just
            # fired, so if it's no longer the one tracked for this key, something newer
            # has already taken over.
            if self.active_views.get(key) is not origin_view:
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

            # Still holding the lock from the mutation above, so this first render
            # attempt can't race a concurrent player click the way a dropped-then-
            # reacquired lock would. Retries (below) necessarily run later, in their
            # own task after a real sleep, and reacquire the lock themselves then.
            await self._render_timeout_result(key, message, note, origin_view, attempt=0)

    async def _render_timeout_result(
        self, key: tuple[int, int], message: discord.Message, note: str, origin_view: "BlackjackView", attempt: int
    ) -> None:
        """Render and edit in the current state of `key`'s game. Caller must already
        hold self.lock_for(key)."""
        if self.active_views.get(key) is not origin_view:
            # Only handle_timeout's very first call (attempt=0) is guaranteed to run
            # right after its own identity check, in the same lock acquisition, with
            # nothing else able to run in between - so that check alone doesn't cover
            # this method's retries, which reacquire the lock later after a real
            # sleep (up to TIMEOUT_EDIT_MAX_RETRIES * TIMEOUT_EDIT_RETRY_SECONDS
            # later). If the player has since acted, timed out again, or started a
            # brand new hand while a retry was pending, `key` has moved on to a
            # different game/message entirely. Blindly rendering here would render
            # THAT current game's state into `message` (the stale one this retry is
            # still holding a reference to) and then overwrite the current game's
            # message_id to point back at it - corrupting a hand this retry has
            # nothing to do with. Drop it instead.
            logger.info(
                "Blackjack: dropping a stale timeout render retry for key=%s (origin_view no longer active).",
                key,
            )
            return

        game = self.games.get(key)
        if not game:
            return

        embed, file, view = await self.build_response(key, note=note, celebrate=True)
        finished = game.phase == "finished"
        try:
            await message.edit(embed=embed, attachments=[file], view=view)
        except discord.HTTPException as exc:
            logger.warning(
                "Blackjack timeout edit failed for key=%s (attempt %s/%s): %s",
                key, attempt + 1, TIMEOUT_EDIT_MAX_RETRIES, exc, exc_info=exc,
            )
            # This view's own timeout already fired once (a view's timeout is
            # one-shot, unlike the action/shortcut edit-failure paths where the
            # previous view is still ticking and remains a safety net). Without
            # re-arming something here, a purely transient Discord error at
            # exactly this moment would otherwise strand the hand with no live
            # timer and no way to self-heal - the message was never actually
            # updated, so even a player clicking it still shows/edits stale state.
            self.discard_pending_view(view)
            if attempt + 1 < TIMEOUT_EDIT_MAX_RETRIES:
                asyncio.create_task(self.retry_render_timeout_result(key, message, note, origin_view, attempt + 1))
            else:
                logger.error(
                    "Blackjack timeout render permanently failed after %s attempts for key=%s. "
                    "This hand's state is correctly saved in the DB, but its table message was never "
                    "updated and no timer remains to retry - see scripts/flush_blackjack_hands.py.",
                    TIMEOUT_EDIT_MAX_RETRIES, key,
                )
            return

        game.message_id = message.id
        self.commit_view(key, view)
        if view:
            view.message = message
            self.game_messages[key] = message
        if finished:
            self.schedule_finished_cleanup(key, game)
        else:
            await self.save_active_game(key)

    async def retry_render_timeout_result(
        self, key: tuple[int, int], message: discord.Message, note: str, origin_view: "BlackjackView", attempt: int
    ) -> None:
        await asyncio.sleep(TIMEOUT_EDIT_RETRY_SECONDS)
        async with self.lock_for(key):
            await self._render_timeout_result(key, message, note, origin_view, attempt)

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
                mode=game.mode,
            )

            # game.mode, not the player's current preference: they may have switched
            # modes while this hand was live, and the cards belong to the shoe they
            # were actually dealt from.
            shoe = await self.shoe_for_user(user_id, game.mode)
            if game.emergency_refill:
                # The hand outran the shoe and _draw() patched cards in. Reset to a
                # genuinely clean shoe rather than carrying a stale discard pile and
                # a cut card measured against a shoe that is no longer there.
                shoe.shuffle_new_deck("recovered from mid-hand exhaustion")
                reshuffled = True
            else:
                reshuffled = shoe.finish_hand(game.cards_in_play())
            await self.save_shoe_for_user(user_id, game.mode)
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
        if amount_cents >= DAILY_BONUS_PLATEAU_CENTS:
            streak_text += " • +$10/day bonus!"
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
        actions = []
        if game.can_hit_active:
            actions.append("**H** Hit")
        actions.append("**S** Stand")
        if game.can_double_active:
            actions.append("**D** Double")
        if game.can_split_active:
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
    ) -> tuple[discord.Embed, discord.File, BlackjackView | ReplayView | None]:
        game = self.games[key]
        balance = await self.db.get_balance(game.user_id)
        shoe = await self.shoe_for_user(game.user_id, game.mode)
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
            embed.set_footer(
                text=f"Action timer: {PLAYER_ACTION_TIMEOUT_SECONDS}s • "
                     f"Insurance timer: {INSURANCE_TIMEOUT_SECONDS}s • {game.rules.rules_line()}"
            )
        else:
            embed.set_footer(text=game.rules.rules_line())

        embed.set_image(url=f"attachment://{image_filename}")

        # Build the candidate next view, but do NOT touch self.active_views yet,
        # and do NOT stop the current view here. This method only renders a
        # response - it has no idea yet whether the caller's upcoming Discord
        # edit will actually succeed. Committing the swap here unconditionally
        # used to mean: if that edit later failed (e.g. a transient "Unknown
        # Webhook"/expired-interaction error), the OLD view - still the only
        # thing actually attached to the real, clickable message - was already
        # permanently .stop()'d, while the NEW view (now the tracked "active"
        # one) never got a `.message` set since the edit that would have set it
        # failed, leaving its own timeout permanently inert (on_timeout no-ops
        # when .message is None). That silently disabled the auto-timeout
        # safety net for the hand forever, stranding the player's stake with no
        # self-healing path short of them clicking that same now-unresponsive
        # message again.
        #
        # Instead, the caller commits the swap via commit_view(...) only after
        # its edit succeeds. On failure, it leaves the previous view (and its
        # ticking timeout) completely untouched so it remains a working
        # safety net, and discards this candidate via discard_pending_view(...).
        if game.phase != "finished":
            view = BlackjackView(self, key, balance)
        else:
            # game.bet_cents is the hand's *base* bet, so a hand that was doubled
            # or split still offers to replay the original stake, not the total
            # that ended up on the table.
            view = ReplayView(self, key, game.bet_cents)

        return embed, file, view

    def commit_view(self, key: tuple[int, int], view: "BlackjackView | ReplayView | None") -> None:
        """Swap in `view` as the tracked view for `key`. Call this ONLY after the
        Discord edit that attaches `view` to a real message has actually
        succeeded - see the long comment in build_response for why.

        Popping the old view out of self.active_views (and stopping it) is also
        what a stale timeout's identity check (see handle_timeout/
        _render_timeout_result) relies on: once a key's tracked view has been
        swapped out here, no old view instance can ever be `is` whatever gets
        stored next, whether or not this call produces a new view to store."""
        old_view = self.active_views.pop(key, None)
        if old_view is not None:
            old_view.stop()
        old_replay = self.replay_views.pop(key, None)
        if old_replay is not None and old_replay is not view:
            old_replay.stop()
        # A ReplayView is tracked separately from active_views on purpose: the
        # staleness checks in handle_timeout/_render_timeout_result identify a live
        # hand by `self.active_views[key] is origin_view`, and a finished hand's
        # replay buttons are not a hand in progress. Keeping it out of that dict
        # means a finished table can never be mistaken for a playable one.
        if isinstance(view, ReplayView):
            self.replay_views[key] = view
        elif view is not None:
            self.active_views[key] = view

    def discard_pending_view(self, view: "BlackjackView | ReplayView | None") -> None:
        """Cancel a candidate view's timeout after the edit that would have
        attached it failed, since it was never committed via commit_view and
        would otherwise sit around with a dangling, unusable timer."""
        if view is not None:
            view.stop()

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
    @app_commands.describe(table="Limit to one table. Omit to combine every table.")
    @app_commands.choices(table=MODE_CHOICES)
    async def blackjack_leaderboard(
        self, interaction: discord.Interaction, table: app_commands.Choice[str] | None = None
    ):
        await interaction.response.defer()

        guild = interaction.guild
        member_cache: dict[int, discord.Member | None] = {}
        mode = table.value if table else None

        title = "🃏 Blackjack Leaderboard"
        if mode:
            title += f" — {mode_label(get_rules(mode))}"
        embed = discord.Embed(title=title, color=discord.Color.gold())
        blocks = []
        for metric, label, fmt in LEADERBOARD_CATEGORIES:
            candidates = await self.db.get_leaderboard(metric, limit=LEADERBOARD_CANDIDATE_LIMIT, mode=mode)

            lines = []
            for row in candidates:
                member = await self.resolve_guild_member(guild, row["user_id"], member_cache)
                if member is None:
                    continue
                medal = LEADERBOARD_MEDALS[len(lines)] if len(lines) < len(LEADERBOARD_MEDALS) else "•"
                value_text = self.format_leaderboard_value(row["value"], fmt)
                lines.append(f"{medal} {member.display_name} — **{value_text}**")
                if len(lines) >= len(LEADERBOARD_MEDALS):
                    break

            body = "\n".join(lines) if lines else "No qualifying players yet."
            blocks.append(f"**{label}**\n{body}")

        embed.description = "\n\n".join(blocks)
        embed.set_footer(text="Win %/ROI % require a minimum sample size to qualify. Play /blackjack to climb the board!")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="blackjack_stats",
        description="View your own or another member's blackjack stats.",
    )
    @app_commands.describe(
        member="View this member's stats instead of your own",
        table="Limit to one table. Omit to combine every table.",
    )
    @app_commands.choices(table=MODE_CHOICES)
    async def blackjack_stats(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        table: app_commands.Choice[str] | None = None,
    ):
        await interaction.response.defer()

        target = member or interaction.user
        mode = table.value if table else None

        stats = await self.db.get_stats(target.id, mode)
        balance = await self.db.get_balance(target.id)

        decided = stats["hands_won"] + stats["hands_lost"]
        win_pct = (100.0 * stats["hands_won"] / decided) if decided else 0.0
        roi_pct = (100.0 * stats["total_profit_cents"] / stats["total_wagered_cents"]) if stats["total_wagered_cents"] else 0.0
        profit_cents = stats["total_profit_cents"]
        profit_text = f"{'+' if profit_cents >= 0 else '-'}{money(abs(profit_cents))}"

        embed = discord.Embed(
            title=f"📊 Blackjack Stats — {target.display_name}",
            color=discord.Color.blue(),
        )

        blocks = [
            "**🃏 Hands**\n"
            f"Played: **{stats['hands_played']}**\n"
            f"Won / Lost / Pushed: **{stats['hands_won']} / {stats['hands_lost']} / {stats['hands_pushed']}**\n"
            f"Win %: **{win_pct:.1f}%**\n"
            f"Blackjacks Hit: **{stats['blackjacks_hit']}**",
            "**🔥 Streaks**\n"
            f"Current Win Streak: **{stats['current_win_streak']}**\n"
            f"Best Win Streak: **{stats['best_win_streak']}**\n"
            f"Daily Sign-in Streak: **{stats['daily_streak']}**",
            "**🛡️ Discipline**\n"
            f"Busts: **{stats['busts']}**\n"
            f"Busts Prevented: **{stats['busts_prevented']}**",
            "**💰 Money**\n"
            f"Balance: **{money(balance)}**\n"
            f"Total Wagered: **{money(stats['total_wagered_cents'])}**\n"
            f"Lifetime P/L: **{profit_text}**\n"
            f"ROI: **{roi_pct:.1f}%**\n"
            f"Biggest Win: **{money(stats['biggest_win_cents'])}**",
        ]
        embed.description = "\n\n".join(blocks)

        embed.set_footer(text="Check /blackjack_leaderboard to see how you stack up.")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot, db_path: str = DEFAULT_DB_PATH):
    await bot.add_cog(BlackjackCog(bot, db_path=db_path))
