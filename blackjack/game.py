from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from .cards import Card, dealer_should_hit, hand_value, is_natural_blackjack, new_single_deck, same_rank

Phase = Literal["insurance", "player", "finished"]


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    dollars, rem = divmod(cents, 100)
    if rem:
        return f"{sign}${dollars:,}.{rem:02d}"
    return f"{sign}${dollars:,}"


def payout_6_to_5(bet_cents: int) -> int:
    return int((Decimal(bet_cents) * Decimal("1.2")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def payout_3_to_2(bet_cents: int) -> int:
    return int((Decimal(bet_cents) * Decimal("1.5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(slots=True)
class PlayerHand:
    cards: list[Card]
    bet_cents: int
    from_split: bool = False
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
        return len(self.cards) == 2 and not self.stood and not self.busted and not self.doubled

    @property
    def can_split(self) -> bool:
        return len(self.cards) == 2 and same_rank(self.cards[0], self.cards[1]) and not self.from_split


@dataclass(slots=True)
class BlackjackGame:
    user_id: int
    channel_id: int
    bet_cents: int
    lucky_blackjack: bool
    player_label: str = "Player"
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

    @classmethod
    def start(
        cls,
        *,
        user_id: int,
        channel_id: int,
        bet_cents: int,
        lucky_blackjack: bool,
        deck: list[Card] | None = None,
        player_label: str = "Player",
    ) -> "BlackjackGame":
        game = cls(
            user_id=user_id,
            channel_id=channel_id,
            bet_cents=bet_cents,
            lucky_blackjack=lucky_blackjack,
            player_label=player_label,
            deck=deck if deck is not None else new_single_deck(),
        )

        # Deal in real table order: player, dealer, player, dealer.
        player_cards = [game.deck.pop()]
        dealer_cards = [game.deck.pop()]
        player_cards.append(game.deck.pop())
        dealer_cards.append(game.deck.pop())
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

    def hit(self) -> None:
        hand = self.active_hand
        hand.cards.append(self.deck.pop())
        if hand.value > 21:
            hand.busted = True
            hand.stood = True
            self.advance_hand_or_finish()
        elif hand.value == 21:
            # Auto-stand on 21 to keep the UI snappy.
            hand.stood = True
            self.advance_hand_or_finish()

    def stand(self) -> None:
        self.active_hand.stood = True
        self.advance_hand_or_finish()

    def double(self) -> None:
        hand = self.active_hand
        hand.bet_cents *= 2
        hand.doubled = True
        hand.cards.append(self.deck.pop())
        if hand.value > 21:
            hand.busted = True
        hand.stood = True
        self.advance_hand_or_finish()

    def split(self) -> None:
        hand = self.active_hand
        first, second = hand.cards
        hand.cards = [first, self.deck.pop()]
        new_hand = PlayerHand(cards=[second, self.deck.pop()], bet_cents=hand.bet_cents, from_split=True)
        hand.from_split = True
        self.hands.insert(self.active_hand_index + 1, new_hand)
        self.did_split = True

        # If first split hand immediately lands on 21, move on.
        if hand.value == 21:
            hand.stood = True
            self.advance_hand_or_finish()

    def advance_hand_or_finish(self) -> None:
        for index in range(self.active_hand_index + 1, len(self.hands)):
            if not self.hands[index].stood and not self.hands[index].busted:
                self.active_hand_index = index
                return

        if any(not hand.busted for hand in self.hands):
            while dealer_should_hit(self.dealer):
                self.dealer.append(self.deck.pop())

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
            elif hand.is_blackjack:
                profit = payout_3_to_2(hand.bet_cents) if self.lucky_blackjack else payout_6_to_5(hand.bet_cents)
                credited += hand.bet_cents + profit
                label = "3:2 lucky blackjack" if self.lucky_blackjack else "6:5 blackjack"
                self.settlement_lines.append(f"{prefix}: {label}")
            elif hand.busted or hand.value > 21:
                self.settlement_lines.append(f"{prefix}: bust")
            elif self.dealer_has_blackjack:
                self.settlement_lines.append(f"{prefix}: dealer blackjack")
            elif dealer_bust:
                credited += hand.bet_cents * 2
                self.settlement_lines.append(f"{prefix}: dealer bust, win")
            elif hand.value > dealer_total:
                credited += hand.bet_cents * 2
                self.settlement_lines.append(f"{prefix}: win")
            elif hand.value < dealer_total:
                self.settlement_lines.append(f"{prefix}: lose")
            else:
                credited += hand.bet_cents
                self.settlement_lines.append(f"{prefix}: push")

        self.settlement_credited_cents = credited
        self.settlement_net_cents = credited - self.total_wagered_cents
        return credited
