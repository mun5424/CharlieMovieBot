from __future__ import annotations

from dataclasses import dataclass, field

from .cards import Card, new_single_deck

# Real single-deck games reshuffle before the deck gets dangerously low.
# For a solo Discord hand with split/double support, this keeps us from running
# out of cards mid-hand while still letting previous cards stay out for a while.
RESHUFFLE_AT_REMAINING_CARDS = 18
MIN_CARDS_TO_START_HAND = 26


@dataclass(slots=True)
class SingleDeckShoe:
    deck: list[Card] = field(default_factory=new_single_deck)
    discard: list[Card] = field(default_factory=list)
    hands_played: int = 0
    last_shuffle_reason: str = "new shoe"
    reshuffled_before_current_hand: bool = False
    reshuffled_after_last_hand: bool = False

    @classmethod
    def fresh(cls, reason: str = "new shoe") -> "SingleDeckShoe":
        shoe = cls(deck=new_single_deck(), discard=[], hands_played=0, last_shuffle_reason=reason)
        return shoe

    def shuffle_new_deck(self, reason: str) -> None:
        self.deck = new_single_deck()
        self.discard.clear()
        self.hands_played = 0
        self.last_shuffle_reason = reason

    def prepare_for_new_hand(self) -> bool:
        """Return True if the shoe was shuffled before dealing this hand."""
        self.reshuffled_before_current_hand = False
        self.reshuffled_after_last_hand = False

        if len(self.deck) < MIN_CARDS_TO_START_HAND:
            self.shuffle_new_deck("reshuffled before hand")
            self.reshuffled_before_current_hand = True

        return self.reshuffled_before_current_hand

    def finish_hand(self, used_cards: list[Card]) -> bool:
        """Move completed hand cards to discard. Return True if reshuffled after the hand."""
        self.discard.extend(used_cards)
        self.hands_played += 1
        self.reshuffled_after_last_hand = False

        if len(self.deck) <= RESHUFFLE_AT_REMAINING_CARDS:
            self.shuffle_new_deck("cut card reached")
            self.reshuffled_after_last_hand = True

        return self.reshuffled_after_last_hand

    @property
    def cards_remaining(self) -> int:
        return len(self.deck)

    @property
    def discard_count(self) -> int:
        return len(self.discard)
