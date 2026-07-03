from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable

SUITS = ("S", "H", "D", "C")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUIT_SYMBOLS = {
    "S": "♠",
    "H": "♥",
    "D": "♦",
    "C": "♣",
}

@dataclass(frozen=True, slots=True)
class Card:
    rank: str
    suit: str

    @property
    def label(self) -> str:
        return f"{self.rank}{SUIT_SYMBOLS[self.suit]}"

    @property
    def image_name(self) -> str:
        # Expected asset file names: AS.png, 10H.png, KC.png, etc.
        return f"{self.rank}{self.suit}.png"


def new_single_deck() -> list[Card]:
    deck = [Card(rank, suit) for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def card_points(card: Card) -> int:
    if card.rank in {"J", "Q", "K"}:
        return 10
    if card.rank == "A":
        return 11
    return int(card.rank)


def hand_value(cards: Iterable[Card]) -> tuple[int, bool]:
    """Return (best_total, is_soft). Soft means at least one Ace is counted as 11."""
    total = 0
    aces = 0

    for card in cards:
        total += card_points(card)
        if card.rank == "A":
            aces += 1

    soft_aces = aces
    while total > 21 and soft_aces:
        total -= 10
        soft_aces -= 1

    is_soft = soft_aces > 0
    return total, is_soft


def is_natural_blackjack(cards: list[Card]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


def same_rank(a: Card, b: Card) -> bool:
    return a.rank == b.rank


def dealer_should_hit(cards: list[Card]) -> bool:
    total, soft = hand_value(cards)
    # H17: dealer hits soft 17.
    return total < 17 or (total == 17 and soft)


def card_to_code(card: Card) -> str:
    return f"{card.rank}{card.suit}"


def card_from_code(code: str) -> Card:
    code = code.strip().upper()
    if len(code) < 2:
        raise ValueError(f"Invalid card code: {code!r}")
    rank = code[:-1]
    suit = code[-1]
    if rank not in RANKS or suit not in SUITS:
        raise ValueError(f"Invalid card code: {code!r}")
    return Card(rank, suit)
