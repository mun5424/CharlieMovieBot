from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

from .cards import Card, SUIT_SYMBOLS, hand_value
from .game import BlackjackGame
from .shoe import SingleDeckShoe

CANVAS_W = 900
MARGIN_X = 44
SECTION_GAP = 26
CARD_GAP = 24
CARD_RATIO = 1.4
MIN_CARD_W = 72
MAX_CARD_W = 365
LABEL_FONT_SIZE = 40
MAX_LABEL_CHARS = 24


class CardRenderer:
    def __init__(self, asset_dir: str | Path | None = None):
        self.asset_dir = Path(asset_dir) if asset_dir else Path(__file__).parent / "assets" / "cards"

    def render_png(
        self,
        game: BlackjackGame,
        *,
        note: str = "",
        shoe: SingleDeckShoe | None = None,
        player_name: str = "Player",
    ) -> io.BytesIO:
        """Render a mobile-friendly blackjack table.

        The image intentionally focuses on only the dealer cards and player cards.
        Deck/discard information, table title, and other low-value chrome are omitted
        so the cards become the hero of the image when viewed on a phone.
        """
        try:
            from PIL import Image, ImageDraw
        except ImportError as exc:
            raise RuntimeError("Install Pillow to render blackjack card images: pip install Pillow") from exc

        dealer_hidden = game.phase != "finished"
        rows = self._build_rows(game, dealer_hidden=dealer_hidden, player_name=player_name)
        card_w, card_h = self._card_size(rows)

        label_h = 52
        row_h = label_h + card_h + 30
        height = 34 + len(rows) * row_h + max(0, len(rows) - 1) * SECTION_GAP + 34

        image = Image.new("RGB", (CANVAS_W, height), (25, 95, 55))
        draw = ImageDraw.Draw(image)
        font_label = self._font(LABEL_FONT_SIZE)

        y = 34
        for index, row in enumerate(rows):
            top = y - 12
            bottom = y + row_h - 10
            if row["active"]:
                outline, width = (255, 230, 120), 5
            elif row["doubled"]:
                outline, width = (255, 140, 0), 4
            else:
                outline, width = (235, 235, 235), 2
            draw.rounded_rectangle(
                (MARGIN_X // 2, top, CANVAS_W - MARGIN_X // 2, bottom),
                radius=22,
                outline=outline,
                width=width,
            )

            draw.text((MARGIN_X, y), row["label"], fill="white", font=font_label)
            cards_y = y + label_h
            cards_x = self._centered_cards_x(len(row["cards"]), card_w, card_h, row["hide_second"])
            self._draw_cards(
                draw,
                image,
                row["cards"],
                x=cards_x,
                y=cards_y,
                card_w=card_w,
                card_h=card_h,
                hide_second=row["hide_second"],
            )

            y += row_h + SECTION_GAP

        output = io.BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        return output

    def _build_rows(self, game: BlackjackGame, *, dealer_hidden: bool, player_name: str = "Player") -> list[dict[str, object]]:
        dealer_value_text = "?" if dealer_hidden else str(game.dealer_value)
        rows: list[dict[str, object]] = [
            {
                "label": f"Dealer ({dealer_value_text})",
                "cards": game.dealer,
                "hide_second": dealer_hidden,
                "active": False,
                "doubled": False,
            }
        ]

        player_label = self._safe_label(player_name) or "Player"
        multiple_hands = len(game.hands) > 1
        for idx, hand in enumerate(game.hands):
            value, soft = hand_value(hand.cards)
            active = game.phase == "player" and idx == game.active_hand_index
            prefix = f"{player_label} Hand {idx + 1}" if multiple_hands else player_label
            label = f"{prefix} ({value}{' soft' if soft else ''})"
            # double() always sets stood=True too, so doubled must be checked
            # first or a doubled hand would just be mislabeled "STAND".
            if hand.doubled and hand.busted:
                label += " — DOUBLED, BUST"
            elif hand.doubled:
                label += " — DOUBLED"
            elif hand.busted:
                label += " — BUST"
            elif hand.stood:
                label += " — STAND"
            if active:
                label = "▶ " + label

            rows.append(
                {
                    "label": label,
                    "cards": hand.cards,
                    "hide_second": False,
                    "active": active,
                    "doubled": hand.doubled,
                }
            )

        return rows

    def _safe_label(self, value: str) -> str:
        label = " ".join((value or "Player").split())
        if len(label) <= MAX_LABEL_CHARS:
            return label
        return label[: MAX_LABEL_CHARS - 1].rstrip() + "…"

    def _card_size(self, rows: list[dict[str, object]]) -> tuple[int, int]:
        max_cards = max(1, max(len(row["cards"]) for row in rows))
        available_w = CANVAS_W - (MARGIN_X * 2)
        card_w = (available_w - CARD_GAP * (max_cards - 1)) // max_cards
        card_w = max(MIN_CARD_W, min(MAX_CARD_W, card_w))
        return int(card_w), int(card_w * CARD_RATIO)

    def _centered_cards_x(self, count: int, card_w: int, card_h: int, hide_second: bool) -> int:
        total_w = count * card_w + max(0, count - 1) * CARD_GAP
        return (CANVAS_W - total_w) // 2

    def _draw_cards(
        self,
        draw,
        canvas,
        cards: Iterable[Card],
        *,
        x: int,
        y: int,
        card_w: int,
        card_h: int,
        hide_second: bool = False,
    ) -> None:
        for idx, card in enumerate(cards):
            hidden = hide_second and idx == 1
            card_img = self._card_back(card_w, card_h) if hidden else self._card_image(card, card_w, card_h)
            canvas.paste(card_img, (x + idx * (card_w + CARD_GAP), y), card_img)

    def _card_image(self, card: Card, card_w: int, card_h: int):
        from PIL import Image, ImageDraw

        path = self.asset_dir / card.image_name
        if path.exists():
            return Image.open(path).convert("RGBA").resize((card_w, card_h))

        # Fallback placeholder if image assets are not installed yet.
        img = Image.new("RGBA", (card_w, card_h), "white")
        draw = ImageDraw.Draw(img)
        color = (190, 0, 0) if card.suit in {"H", "D"} else (0, 0, 0)
        radius = max(12, card_w // 10)
        border_w = max(3, card_w // 38)
        draw.rounded_rectangle((0, 0, card_w - 1, card_h - 1), radius=radius, outline=(20, 20, 20), width=border_w)
        font_big = self._font(max(38, card_w // 3))
        font_small = self._font(max(24, card_w // 5))
        draw.text((card_w // 10, card_h // 16), card.rank, fill=color, font=font_small)
        draw.text((card_w // 10, card_h // 4), SUIT_SYMBOLS[card.suit], fill=color, font=font_small)
        draw.text((card_w // 2 - card_w // 7, card_h // 2 - card_h // 8), SUIT_SYMBOLS[card.suit], fill=color, font=font_big)
        return img

    def _card_back(self, card_w: int, card_h: int):
        from PIL import Image, ImageDraw

        path = self.asset_dir / "back.png"
        if path.exists():
            return Image.open(path).convert("RGBA").resize((card_w, card_h))

        img = Image.new("RGBA", (card_w, card_h), (35, 65, 140))
        draw = ImageDraw.Draw(img)
        radius = max(12, card_w // 10)
        border_w = max(3, card_w // 38)
        draw.rounded_rectangle((0, 0, card_w - 1, card_h - 1), radius=radius, outline="white", width=border_w)
        draw.text((card_w // 3, card_h // 2 - 12), "CARD", fill="white", font=self._font(max(20, card_w // 6)))
        return img

    def _font(self, size: int):
        from PIL import ImageFont

        try:
            return ImageFont.truetype("DejaVuSans.ttf", size)
        except Exception:
            return ImageFont.load_default()


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    dollars, rem = divmod(cents, 100)
    if rem:
        return f"{sign}${dollars:,}.{rem:02d}"
    return f"{sign}${dollars:,}"
