from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from .cards import Card, dealer_should_hit, hand_value, is_natural_blackjack, new_shoe, new_single_deck, same_rank
from .rules import DEFAULT_MODE, RuleSet, get_rules

logger = logging.getLogger(__name__)

Phase = Literal["insurance", "player", "finished"]


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    dollars, rem = divmod(cents, 100)
    if rem:
        return f"{sign}${dollars:,}.{rem:02d}"
    return f"{sign}${dollars:,}"


def payout_3_to_2(bet_cents: int) -> int:
    return int((Decimal(bet_cents) * Decimal("1.5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(slots=True)
class PlayerHand:
    cards: list[Card]
    bet_cents: int
    from_split: bool = False
    split_aces: bool = False
    stood: bool = False
    busted: bool = False
    doubled: bool = False

    @property
    def value(self) -> int:
        return hand_value(self.cards)[0]

    @property
    def is_soft(self) -> bool:
        return hand_value(self.cards)[1]

    @property
    def is_blackjack(self) -> bool:
        # Split 21 is treated as 21, not a natural blackjack.
        return not self.from_split and is_natural_blackjack(self.cards)

    @property
    def can_double(self) -> bool:
        # A split ace draws exactly one card, so it can never be doubled.
        return (
            len(self.cards) == 2
            and not self.stood
            and not self.busted
            and not self.doubled
            and not self.split_aces
        )

    @property
    def can_split(self) -> bool:
        """Pair *shape* only. Whether a split is actually allowed depends on the
        mode's hand limit and resplit-aces rule - see BlackjackGame.can_split_active.
        `from_split` is deliberately not checked here any more: modes that allow
        resplitting let an already-split hand split again."""
        return (
            len(self.cards) == 2
            and same_rank(self.cards[0], self.cards[1])
            and not self.stood
            and not self.busted
        )


@dataclass(slots=True)
class BlackjackGame:
    user_id: int
    channel_id: int
    bet_cents: int
    player_label: str = "Player"
    message_id: int = 0
    deck: list[Card] = field(default_factory=new_single_deck)
    dealer: list[Card] = field(default_factory=list)
    hands: list[PlayerHand] = field(default_factory=list)
    active_hand_index: int = 0
    phase: Phase = "player"
    insurance_bet_cents: int = 0
    insurance_resolved: bool = False
    settled: bool = False
    did_split: bool = False
    settlement_lines: list[str] = field(default_factory=list)
    settlement_credited_cents: int = 0
    settlement_net_cents: int = 0
    hand_results: list[str] = field(default_factory=list)
    busts_prevented: int = 0
    mode: str = DEFAULT_MODE
    # Set if _draw() had to refill mid-hand. The cog reads it after the hand and
    # forces a clean reshuffle, because the refill leaves the shoe's discard pile
    # and cut card describing a shoe that no longer exists.
    emergency_refill: bool = False

    @property
    def rules(self) -> RuleSet:
        return get_rules(self.mode)

    @classmethod
    def start(
        cls,
        *,
        user_id: int,
        channel_id: int,
        bet_cents: int,
        deck: list[Card] | None = None,
        player_label: str = "Player",
        mode: str = DEFAULT_MODE,
    ) -> "BlackjackGame":
        rules = get_rules(mode)
        game = cls(
            user_id=user_id,
            channel_id=channel_id,
            bet_cents=bet_cents,
            player_label=player_label,
            deck=deck if deck is not None else new_shoe(rules.decks),
            mode=rules.key,
        )

        # Deal in real table order: player, dealer, player, dealer.
        player_cards = [game._draw()]
        dealer_cards = [game._draw()]
        player_cards.append(game._draw())
        dealer_cards.append(game._draw())
        game.hands.append(PlayerHand(cards=player_cards, bet_cents=bet_cents))
        game.dealer = dealer_cards

        if game.dealer_upcard.rank == "A":
            game.phase = "insurance"
        else:
            game.resolve_opening_if_needed()

        return game


    def cards_in_play(self) -> list[Card]:
        cards: list[Card] = []
        cards.extend(self.dealer)
        for hand in self.hands:
            cards.extend(hand.cards)
        return cards

    @property
    def dealer_upcard(self) -> Card:
        return self.dealer[0]

    @property
    def active_hand(self) -> PlayerHand:
        return self.hands[self.active_hand_index]

    @property
    def dealer_value(self) -> int:
        return hand_value(self.dealer)[0]

    @property
    def dealer_is_soft(self) -> bool:
        return hand_value(self.dealer)[1]

    @property
    def dealer_has_blackjack(self) -> bool:
        return is_natural_blackjack(self.dealer)

    @property
    def insurance_max_cents(self) -> int:
        return self.hands[0].bet_cents // 2

    def resolve_insurance(self, take: bool) -> None:
        # The caller is responsible for deducting insurance_bet_cents from the user's balance.
        if take:
            self.insurance_bet_cents = self.insurance_max_cents
        self.insurance_resolved = True
        self.phase = "player"
        self.resolve_opening_if_needed()

    def resolve_opening_if_needed(self) -> None:
        player_has_blackjack = self.hands[0].is_blackjack
        if self.dealer_has_blackjack or player_has_blackjack:
            self.phase = "finished"

    def _draw(self) -> Card:
        """Take one card, refilling if the shoe somehow empties mid-hand.

        With any sane penetration this never fires - the cut card ends the shoe
        long before it empties. It exists so a pathological hand (split to the
        limit with every hand drawing out) degrades into a reshuffle rather than
        an IndexError, which would strand the player's stake mid-hand.
        """
        if not self.deck:
            # self.deck IS the shoe's list, so this refill is visible to the shoe
            # but leaves its discard/cut-card bookkeeping describing the old shoe.
            # Flag it so the cog can reshuffle properly once the hand is settled.
            logger.warning(
                "Blackjack: shoe exhausted mid-hand for user_id=%s mode=%s - refilling",
                self.user_id, self.mode,
            )
            self.deck.extend(new_shoe(self.rules.decks))
            self.emergency_refill = True
        return self.deck.pop()

    @property
    def can_hit_active(self) -> bool:
        hand = self.active_hand
        if hand.stood or hand.busted:
            return False
        # A split ace takes its one card at split time and is done.
        return not hand.split_aces

    @property
    def can_double_active(self) -> bool:
        hand = self.active_hand
        if not hand.can_double:
            return False
        return self.rules.can_double_total(hand.value, hand.from_split)

    def hand_can_split(self, hand: PlayerHand) -> bool:
        if not hand.can_split:
            return False
        if len(self.hands) >= self.rules.max_hands:
            return False
        if hand.split_aces and not self.rules.resplit_aces:
            return False
        return True

    @property
    def can_split_active(self) -> bool:
        return self.hand_can_split(self.active_hand)

    def _resolve_hand_entry(self, hand: PlayerHand) -> bool:
        """Auto-stand a hand the player has no real decision on. True if stood."""
        if hand.split_aces and not self.hand_can_split(hand):
            # One card to a split ace, then it stands. The only exception is a
            # fresh pair of aces under a resplit-aces rule, which may split again.
            hand.stood = True
            return True
        if hand.value == 21:
            hand.stood = True
            return True
        return False

    def hit(self) -> None:
        hand = self.active_hand
        hand.cards.append(self._draw())
        if hand.value > 21:
            hand.busted = True
            hand.stood = True
            self.advance_hand_or_finish()
        elif hand.value == 21:
            # Auto-stand on 21 to keep the UI snappy.
            hand.stood = True
            self.advance_hand_or_finish()

    def stand(self) -> None:
        hand = self.active_hand
        if 12 <= hand.value <= 16:
            # Standing on a stiff hand instead of hitting into likely bust range.
            self.busts_prevented += 1
        hand.stood = True
        self.advance_hand_or_finish()

    def double(self) -> None:
        hand = self.active_hand
        hand.bet_cents *= 2
        hand.doubled = True
        hand.cards.append(self._draw())
        if hand.value > 21:
            hand.busted = True
        hand.stood = True
        self.advance_hand_or_finish()

    def split(self) -> None:
        hand = self.active_hand
        first, second = hand.cards
        split_aces = first.rank == "A"
        hand.cards = [first, self._draw()]
        new_hand = PlayerHand(
            cards=[second, self._draw()], bet_cents=hand.bet_cents, from_split=True, split_aces=split_aces
        )
        hand.from_split = True
        hand.split_aces = split_aces
        self.hands.insert(self.active_hand_index + 1, new_hand)
        self.did_split = True

        # Each hand is auto-resolved as it becomes active rather than both being
        # stood here: under resplit-aces a freshly split ace can land on another
        # ace and is still owed a decision.
        if self._resolve_hand_entry(hand):
            self.advance_hand_or_finish()

    def advance_hand_or_finish(self) -> None:
        for index in range(self.active_hand_index + 1, len(self.hands)):
            hand = self.hands[index]
            if hand.stood or hand.busted:
                continue
            self.active_hand_index = index
            if self._resolve_hand_entry(hand):
                continue
            return

        if any(not hand.busted for hand in self.hands):
            while dealer_should_hit(self.dealer, self.rules.dealer_hits_soft_17):
                self.dealer.append(self._draw())

        self.phase = "finished"

    @property
    def total_wagered_cents(self) -> int:
        return sum(hand.bet_cents for hand in self.hands) + self.insurance_bet_cents

    def settle(self) -> int:
        """
        Return total cents to credit back to the player.
        Bets are assumed to have already been deducted when placed.
        """
        if self.settled:
            return 0

        self.settled = True
        self.settlement_lines.clear()
        self.settlement_credited_cents = 0
        self.settlement_net_cents = 0
        self.hand_results.clear()
        credited = 0

        if self.insurance_bet_cents:
            if self.dealer_has_blackjack:
                insurance_return = self.insurance_bet_cents * 3  # stake + 2:1 profit
                credited += insurance_return
                self.settlement_lines.append(f"Insurance won: {money(insurance_return)} returned")
            else:
                self.settlement_lines.append("Insurance lost")

        dealer_total = self.dealer_value
        dealer_bust = dealer_total > 21

        for idx, hand in enumerate(self.hands, start=1):
            prefix = f"Hand {idx}" if len(self.hands) > 1 else "Hand"

            if hand.is_blackjack and self.dealer_has_blackjack:
                credited += hand.bet_cents
                self.settlement_lines.append(f"{prefix}: blackjack push")
                self.hand_results.append("push")
            elif hand.is_blackjack:
                profit = self.rules.blackjack_profit(hand.bet_cents)
                credited += hand.bet_cents + profit
                self.settlement_lines.append(f"{prefix}: {self.rules.payout_label} blackjack")
                self.hand_results.append("win")
            elif hand.busted or hand.value > 21:
                self.settlement_lines.append(f"{prefix}: bust")
                self.hand_results.append("loss")
            elif self.dealer_has_blackjack:
                self.settlement_lines.append(f"{prefix}: dealer blackjack")
                self.hand_results.append("loss")
            elif dealer_bust:
                credited += hand.bet_cents * 2
                self.settlement_lines.append(f"{prefix}: dealer bust, win")
                self.hand_results.append("win")
            elif hand.value > dealer_total:
                credited += hand.bet_cents * 2
                self.settlement_lines.append(f"{prefix}: win")
                self.hand_results.append("win")
            elif hand.value < dealer_total:
                self.settlement_lines.append(f"{prefix}: lose")
                self.hand_results.append("loss")
            else:
                credited += hand.bet_cents
                self.settlement_lines.append(f"{prefix}: push")
                self.hand_results.append("push")

        self.settlement_credited_cents = credited
        self.settlement_net_cents = credited - self.total_wagered_cents
        return credited
