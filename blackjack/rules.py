from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

# A mode is a frozen rule sheet. Everything the engine is allowed to vary between
# games lives here - game.py/shoe.py/cards.py read these fields instead of
# hardcoding single-deck behaviour, so adding a fourth casino is a dict entry.


@dataclass(frozen=True, slots=True)
class RuleSet:
    key: str
    name: str
    house: str
    decks: int
    dealer_hits_soft_17: bool
    payout_num: int
    payout_den: int
    double_after_split: bool
    # Total hands a player may end up with, splits included. 2 = a single split.
    max_hands: int
    resplit_aces: bool
    # Hard totals a first double is allowed on; None means any two cards.
    double_on: tuple[int, ...] | None
    late_surrender: bool
    # Fraction of the shoe dealt before the cut card comes out. Real tables vary
    # the cut within a band rather than at a fixed card, so the shoe picks a new
    # depth in [pen_low, pen_high] on every shuffle.
    pen_low: float
    pen_high: float
    min_bet_cents: int

    @property
    def payout_label(self) -> str:
        return f"{self.payout_num}:{self.payout_den}"

    @property
    def total_cards(self) -> int:
        return self.decks * 52

    def blackjack_profit(self, bet_cents: int) -> int:
        """Profit (not counting the returned stake) on a natural, rounded half up.

        Decimal rather than float: 6:5 on odd cent amounts is exactly the case
        where binary floating point starts handing out results that are a cent
        off, and this figure is credited straight to a real balance.
        """
        value = Decimal(bet_cents) * Decimal(self.payout_num) / Decimal(self.payout_den)
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def cut_card_at(self) -> int:
        """Cards left in the shoe when the cut card ends the shoe."""
        pen = random.uniform(self.pen_low, self.pen_high)
        return max(1, round(self.total_cards * (1.0 - pen)))

    def can_double_total(self, total: int, from_split: bool) -> bool:
        if from_split and not self.double_after_split:
            return False
        if self.double_on is not None and total not in self.double_on:
            return False
        return True

    @property
    def deck_label(self) -> str:
        return "Single Deck" if self.decks == 1 else f"{self.decks}-Deck"

    def rules_line(self) -> str:
        """The one-line footer that sits under a table."""
        bits = [
            self.deck_label,
            "Dealer hits soft 17" if self.dealer_hits_soft_17 else "Dealer stands on all 17s",
            f"Blackjack {self.payout_label}",
            "Insurance 2:1",
        ]
        return " • ".join(bits)

    def detail_lines(self) -> list[str]:
        """The full sheet, for /blackjack_options."""
        splits = "No splitting" if self.max_hands < 2 else f"Split to {self.max_hands} hands"
        if self.double_on is None:
            double = "Double on any two cards"
        else:
            double = "Double on " + "/".join(str(t) for t in self.double_on) + " only"
        return [
            f"**Decks:** {self.decks}",
            f"**Dealer:** {'Hits' if self.dealer_hits_soft_17 else 'Stands on'} soft 17",
            f"**Blackjack pays:** {self.payout_label}",
            f"**{double}**",
            f"**{splits}**",
            f"**Double after split:** {'Yes' if self.double_after_split else 'No'}",
            f"**Resplit aces:** {'Yes' if self.resplit_aces else 'No'}",
            f"**Surrender:** {'Late' if self.late_surrender else 'Not offered'}",
            f"**Penetration:** {round(self.pen_low * 100)}–{round(self.pen_high * 100)}%",
            f"**Table minimum:** ${self.min_bet_cents // 100}",
        ]


# The game this bot shipped with. Kept as a mode of its own so every existing
# wallet, stat row and shoe has somewhere to land when modes go live.
SINGLE_HOUSE = RuleSet(
    key="single_house",
    name="Single Deck",
    house="House",
    decks=1,
    dealer_hits_soft_17=True,
    payout_num=3,
    payout_den=2,
    double_after_split=True,
    max_hands=2,
    resplit_aces=False,
    double_on=None,
    late_surrender=False,
    # 18 of 52 cards left is the reshuffle point this game has always used.
    pen_low=0.65,
    pen_high=0.65,
    min_bet_cents=1_000,
)

DOUBLE_PECHANGA = RuleSet(
    key="double_pechanga",
    name="Double Deck",
    house="Pechanga",
    decks=2,
    dealer_hits_soft_17=True,
    # The 6:5 is the catch on this game - it is a worse deal than the 8-deck
    # below despite the shallower shoe, which is exactly how it runs in practice.
    payout_num=6,
    payout_den=5,
    double_after_split=True,
    max_hands=4,
    resplit_aces=True,
    double_on=None,
    late_surrender=False,
    pen_low=0.65,
    pen_high=0.75,
    min_bet_cents=1_000,
)

EIGHT_MANDALAY = RuleSet(
    key="eight_mandalay",
    name="8-Deck",
    house="Mandalay Bay",
    decks=8,
    dealer_hits_soft_17=True,
    payout_num=3,
    payout_den=2,
    double_after_split=True,
    max_hands=4,
    resplit_aces=True,
    double_on=None,
    late_surrender=False,
    pen_low=0.65,
    pen_high=0.75,
    min_bet_cents=1_000,
)

RULE_SETS: dict[str, RuleSet] = {
    r.key: r for r in (SINGLE_HOUSE, DOUBLE_PECHANGA, EIGHT_MANDALAY)
}
DEFAULT_MODE = SINGLE_HOUSE.key


def get_rules(key: str | None) -> RuleSet:
    """Never raises: an unknown/missing mode falls back to the default game
    rather than stranding a player whose stored preference no longer exists."""
    return RULE_SETS.get(key or "", RULE_SETS[DEFAULT_MODE])
