from __future__ import annotations

from dataclasses import dataclass, field

from .cards import Card, new_shoe
from .rules import DEFAULT_MODE, RuleSet, get_rules

# The original single-deck game's floor, kept as the minimum for every mode.
MIN_CARDS_TO_START_HAND = 26

# Default cut depth for a Shoe built without one; every real shoe overwrites this
# in shuffle_new_deck() from its own mode's penetration band.
RESHUFFLE_AT_REMAINING_CARDS = 18


def min_cards_to_start(rules: RuleSet) -> int:
    """Cards that must remain before a new hand may be dealt.

    26 was sized for the 1-deck/2-hand game. A mode allowing four split hands
    plus doubles can legitimately draw far more than that, and a 2-deck shoe's
    cut card can land as low as 26, so the floor has to scale with the mode or a
    hand can start with fewer cards than it might need.
    """
    # 4 hands x ~8 cards worst-case realistic, plus the dealer's own draw.
    return max(MIN_CARDS_TO_START_HAND, rules.max_hands * 8 + 8)


@dataclass(slots=True)
class Shoe:
    mode: str = DEFAULT_MODE
    deck: list[Card] = field(default_factory=list)
    discard: list[Card] = field(default_factory=list)
    hands_played: int = 0
    last_shuffle_reason: str = "new shoe"
    reshuffled_before_current_hand: bool = False
    reshuffled_after_last_hand: bool = False
    # Cards remaining when the cut card comes out. Re-rolled on every shuffle so
    # the depth varies within the mode's penetration band, like a real table.
    cut_card_at: int = RESHUFFLE_AT_REMAINING_CARDS

    @property
    def rules(self) -> RuleSet:
        return get_rules(self.mode)

    @classmethod
    def fresh(cls, mode: str = DEFAULT_MODE, reason: str = "new shoe") -> "Shoe":
        shoe = cls(mode=mode, deck=[], discard=[], hands_played=0, last_shuffle_reason=reason)
        shoe.shuffle_new_deck(reason)
        return shoe

    def shuffle_new_deck(self, reason: str) -> None:
        rules = self.rules
        self.deck = new_shoe(rules.decks)
        self.discard.clear()
        self.hands_played = 0
        self.last_shuffle_reason = reason
        self.cut_card_at = rules.cut_card_at()

    def prepare_for_new_hand(self) -> bool:
        """Return True if the shoe was shuffled before dealing this hand."""
        self.reshuffled_before_current_hand = False
        self.reshuffled_after_last_hand = False

        if len(self.deck) < min_cards_to_start(self.rules):
            self.shuffle_new_deck("reshuffled before hand")
            self.reshuffled_before_current_hand = True

        return self.reshuffled_before_current_hand

    def finish_hand(self, used_cards: list[Card]) -> bool:
        """Move completed hand cards to discard. Return True if reshuffled after the hand."""
        self.discard.extend(used_cards)
        self.hands_played += 1
        self.reshuffled_after_last_hand = False

        if len(self.deck) <= self.cut_card_at:
            self.shuffle_new_deck("cut card reached")
            self.reshuffled_after_last_hand = True

        return self.reshuffled_after_last_hand

    @property
    def cards_remaining(self) -> int:
        return len(self.deck)

    @property
    def discard_count(self) -> int:
        return len(self.discard)


# The single-deck shoe this bot shipped with. Retained as an alias so existing
# imports and type hints keep working while the codebase moves to Shoe.
SingleDeckShoe = Shoe
