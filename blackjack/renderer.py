from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

from .cards import Card, SUIT_SYMBOLS, hand_value
from .game import BlackjackGame
from .shoe import SingleDeckShoe

CARD_W = 120
CARD_H = 168
GAP = 18


class CardRenderer:
    def __init__(self, asset_dir: str | Path | None = None):
        self.asset_dir = Path(asset_dir) if asset_dir else Path(__file__).parent / "assets" / "cards"

    def render_png(self, game: BlackjackGame, *, note: str = "", shoe: SingleDeckShoe | None = None) -> io.BytesIO:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise RuntimeError("Install Pillow to render blackjack card images: pip install Pillow") from exc

        hand_rows = max(1, len(game.hands))
        height = max(760, 430 + hand_rows * 235)
        image = Image.new("RGB", (1100, height), (25, 95, 55))
        draw = ImageDraw.Draw(image)
        font_big = self._font(36)
        font_med = self._font(24)
        font_small = self._font(18)

        draw.text((40, 26), "Blackjack", fill="white", font=font_big)
        if game.lucky_blackjack:
            draw.text((250, 36), "Lucky Hour: Blackjack pays 3:2", fill=(255, 230, 120), font=font_med)
        else:
            draw.text((250, 36), "Blackjack pays 6:5", fill=(230, 230, 230), font=font_med)

        if shoe is not None:
            deck_text = f"Single deck: {shoe.cards_remaining} left • {shoe.discard_count} discard"
            draw.text((760, 36), deck_text, fill=(230, 230, 230), font=font_small)

        if note:
            draw.text((40, 84), note[:130], fill=(255, 240, 180), font=font_small)

        dealer_hidden = game.phase != "finished"
        dealer_value_text = "?" if dealer_hidden else str(game.dealer_value)
        draw.text((40, 125), f"Dealer ({dealer_value_text})", fill="white", font=font_med)
        self._draw_cards(draw, image, game.dealer, x=40, y=165, hide_second=dealer_hidden)

        y = 405
        for idx, hand in enumerate(game.hands):
            value, soft = hand_value(hand.cards)
            active = game.phase == "player" and idx == game.active_hand_index
            label = f"Hand {idx + 1}: {value}{' soft' if soft else ''} — Bet {money(hand.bet_cents)}"
            if hand.busted:
                label += " — BUST"
            elif hand.stood:
                label += " — STAND"
            if active:
                draw.rounded_rectangle((32, y - 10, 1040, y + CARD_H + 52), radius=16, outline=(255, 230, 120), width=4)
                label = "▶ " + label
            draw.text((50, y), label, fill="white", font=font_med)
            self._draw_cards(draw, image, hand.cards, x=50, y=y + 42)
            y += 235

        output = io.BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        return output

    def _draw_cards(self, draw, canvas, cards: Iterable[Card], *, x: int, y: int, hide_second: bool = False) -> None:
        for idx, card in enumerate(cards):
            hidden = hide_second and idx == 1
            card_img = self._card_back() if hidden else self._card_image(card)
            canvas.paste(card_img, (x + idx * (CARD_W + GAP), y))

    def _card_image(self, card: Card):
        from PIL import Image, ImageDraw

        path = self.asset_dir / card.image_name
        if path.exists():
            return Image.open(path).convert("RGBA").resize((CARD_W, CARD_H))

        # Fallback placeholder if image assets are not installed yet.
        img = Image.new("RGBA", (CARD_W, CARD_H), "white")
        draw = ImageDraw.Draw(img)
        color = (190, 0, 0) if card.suit in {"H", "D"} else (0, 0, 0)
        draw.rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=12, outline=(20, 20, 20), width=3)
        font_big = self._font(38)
        font_small = self._font(24)
        draw.text((12, 10), card.rank, fill=color, font=font_small)
        draw.text((12, 38), SUIT_SYMBOLS[card.suit], fill=color, font=font_small)
        draw.text((CARD_W // 2 - 24, CARD_H // 2 - 28), SUIT_SYMBOLS[card.suit], fill=color, font=font_big)
        return img

    def _card_back(self):
        from PIL import Image, ImageDraw

        path = self.asset_dir / "back.png"
        if path.exists():
            return Image.open(path).convert("RGBA").resize((CARD_W, CARD_H))

        img = Image.new("RGBA", (CARD_W, CARD_H), (35, 65, 140))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=12, outline="white", width=3)
        draw.text((34, 66), "CARD", fill="white", font=self._font(20))
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
