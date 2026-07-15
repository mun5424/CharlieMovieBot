from __future__ import annotations

import io
import math
import random
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
        self._source_cache: dict[str, "Image.Image"] = {}
        self._resized_cache: dict[tuple[str, int, int], "Image.Image"] = {}
        self._font_cache: dict[int, "ImageFont.FreeTypeFont"] = {}

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
            from PIL import Image, ImageDraw  # noqa: F401 (import validates Pillow is installed)
        except ImportError as exc:
            raise RuntimeError("Install Pillow to render blackjack card images: pip install Pillow") from exc

        dealer_hidden = game.phase != "finished"
        rows = self._build_rows(game, dealer_hidden=dealer_hidden, player_name=player_name)
        card_w, card_h = self._card_size(rows)
        image = self._draw_base_table(rows, card_w, card_h)

        output = io.BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        return output

    def render_natural_blackjack_gif(
        self,
        game: BlackjackGame,
        *,
        payout_cents: int,
        player_name: str = "Player",
    ) -> io.BytesIO:
        """Celebratory animated GIF for an instant player natural blackjack win.

        Naturals are only ever decided on the initial two cards (before a split
        is possible), so the game always has exactly one player row here and
        both hands are already revealed (phase is "finished").
        """
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Install Pillow to render blackjack card images: pip install Pillow") from exc

        rows = self._build_rows(game, dealer_hidden=False, player_name=player_name)
        card_w, card_h = self._card_size(rows)
        extra_bottom = 100
        background, row_h = self._draw_table_background(rows, card_w, card_h, extra_bottom=extra_bottom)

        label_h = 52
        player_row_top = 34 + row_h + SECTION_GAP
        card_bottom_y = player_row_top + label_h + card_h
        center = (CANVAS_W // 2, player_row_top + (label_h + card_h) // 2)
        # Sits over the seam between the two rows, biased up into the dealer
        # row's bottom edge so it never covers the player row's label text.
        banner_text_y = 34 + row_h - 30

        confetti = self._make_confetti(random.Random(1), background.size)

        # Cards never move frame-to-frame; every zero-sunburst frame (the
        # reveal beat and the whole payout beat) is otherwise identical, so
        # precompute that composite once and hand out cheap copies of it
        # instead of re-pasting the same card images onto a fresh background
        # every time.
        base_with_cards = background.copy()
        self._paste_cards_only(base_with_cards, rows, card_w, card_h)

        frames: list["Image.Image"] = []
        durations: list[int] = []

        def build_frame(*, sunburst: float = 0.0) -> "Image.Image":
            if sunburst <= 0:
                return base_with_cards.copy()
            frame = background.copy()
            self._draw_sunburst(frame, center, sunburst)
            self._paste_cards_only(frame, rows, card_w, card_h)
            return frame

        def add(frame: "Image.Image", duration_ms: int) -> None:
            frames.append(frame.convert("RGB"))
            durations.append(duration_ms)

        # Beat 0: plain reveal, matching the still render, held briefly.
        add(build_frame(), 500)

        # Frame counts below are trimmed from an original 19-frame cut (5/4/3/6
        # per beat) to 13, with each beat's duration kept the same overall
        # length by stretching out the fewer remaining frames - same pacing,
        # ~1/3 fewer frames to draw/encode/quantize.
        burst_steps = 3
        for step in range(1, burst_steps + 1):
            progress = step / burst_steps
            frame = build_frame(sunburst=progress)
            self._draw_confetti(frame, confetti, step)
            self._draw_banner_text(frame, "BLACKJACK!", banner_text_y, progress)
            add(frame, 133)

        hold_steps = 3
        for step in range(hold_steps):
            frame = build_frame(sunburst=1.0)
            self._draw_confetti(frame, confetti, burst_steps + step)
            self._draw_banner_text(frame, "BLACKJACK!", banner_text_y, 1.0)
            self._draw_sparkles(frame, center, card_w, card_h, step)
            add(frame, 173)

        transition_steps = 2
        for step in range(1, transition_steps + 1):
            fade = 1.0 - step / transition_steps
            frame = build_frame(sunburst=fade)
            self._draw_confetti(frame, confetti, burst_steps + hold_steps + step)
            self._draw_sparkles(frame, center, card_w, card_h, hold_steps + step)
            self._draw_ribbon(frame, "* Natural 21 *", card_bottom_y - 26, step / transition_steps)
            add(frame, 165)

        payout_steps = 4
        payout_text = f"+{money(payout_cents)}"
        for step in range(payout_steps):
            frame = build_frame()
            self._draw_confetti(frame, confetti, burst_steps + hold_steps + transition_steps + step)
            self._draw_sparkles(frame, center, card_w, card_h, hold_steps + transition_steps + step)
            self._draw_ribbon(frame, "* Natural 21 *", card_bottom_y - 26, 1.0)
            reveal = min(1.0, (step + 1) / 3)
            self._draw_ribbon(frame, payout_text, card_bottom_y + 60, reveal, accent=True)
            add(frame, 240)

        # Pillow quantizes each RGB frame to its own adaptive 256-color palette
        # at save time unless the frame already arrives in "P" mode - profiling
        # showed that per-frame adaptive quantization is ~70% of this method's
        # total time (~750ms of ~1.1s for a 19-frame animation). Building one
        # shared palette from the busiest frames and reusing it for every frame
        # cuts that to a few dozen ms, since mapping onto an existing palette is
        # far cheaper than computing a new one.
        peak_index = min(1 + burst_steps, len(frames) - 1)
        palette_source = self._gif_palette_source([frames[peak_index], frames[-1]])
        paletted_frames = [frame.quantize(palette=palette_source, dither=Image.Dither.NONE) for frame in frames]

        output = io.BytesIO()
        paletted_frames[0].save(
            output,
            format="GIF",
            save_all=True,
            append_images=paletted_frames[1:],
            duration=durations,
            loop=0,
            disposal=2,
        )
        output.seek(0)
        return output

    def _gif_palette_source(self, representative_frames: list["Image.Image"]) -> "Image.Image":
        """Build one adaptive palette from a couple of the busiest frames.

        Side-by-side rather than blended so every distinct color used across
        the animation (sunburst gold, confetti, ribbons) survives into the
        256-color palette every other frame gets quantized against.
        """
        from PIL import Image

        width = sum(frame.width for frame in representative_frames)
        height = max(frame.height for frame in representative_frames)
        combo = Image.new("RGB", (width, height))
        x = 0
        for frame in representative_frames:
            combo.paste(frame, (x, 0))
            x += frame.width
        return combo.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)

    def _draw_base_table(self, rows: list[dict[str, object]], card_w: int, card_h: int) -> "Image.Image":
        image, _ = self._draw_table_background(rows, card_w, card_h)
        self._paste_cards_only(image, rows, card_w, card_h)
        return image

    def _draw_table_background(
        self, rows: list[dict[str, object]], card_w: int, card_h: int, extra_bottom: int = 0
    ) -> tuple["Image.Image", int]:
        """Draw the table felt, row outlines, and labels, but not the cards.

        Splitting this out from card placement lets the natural-blackjack GIF
        paint its sunburst glow behind the cards instead of washing out the
        white card faces on top of them.
        """
        from PIL import Image, ImageDraw

        label_h = 52
        row_h = label_h + card_h + 30
        height = 34 + len(rows) * row_h + max(0, len(rows) - 1) * SECTION_GAP + 34 + extra_bottom

        image = Image.new("RGB", (CANVAS_W, height), (25, 95, 55))
        draw = ImageDraw.Draw(image)
        font_label = self._font(LABEL_FONT_SIZE)

        y = 34
        for row in rows:
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
            y += row_h + SECTION_GAP

        return image, row_h

    def _paste_cards_only(self, image: "Image.Image", rows: list[dict[str, object]], card_w: int, card_h: int) -> None:
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        label_h = 52
        row_h = label_h + card_h + 30
        y = 34
        for row in rows:
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

    def _make_confetti(self, rng: random.Random, size: tuple[int, int]) -> list[dict[str, float]]:
        width, height = size
        colors = [(255, 209, 102), (239, 71, 111), (17, 138, 178), (6, 214, 160), (255, 255, 255)]
        return [
            {
                "x": rng.uniform(0, width),
                "y": rng.uniform(-height * 0.4, height * 0.6),
                "speed": rng.uniform(6, 16),
                "drift": rng.uniform(-2.5, 2.5),
                "size": rng.uniform(6, 12),
                "color": rng.choice(colors),
                "angle": rng.uniform(0, 360),
                "spin": rng.uniform(-25, 25),
            }
            for _ in range(46)
        ]

    def _draw_confetti(self, frame: "Image.Image", pieces: list[dict[str, float]], step: int) -> None:
        from PIL import Image, ImageDraw

        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        height = frame.size[1]
        for piece in pieces:
            y = (piece["y"] + piece["speed"] * step) % (height + 40) - 20
            x = piece["x"] + piece["drift"] * step
            half = piece["size"] / 2
            angle = math.radians(piece["angle"] + piece["spin"] * step)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            points = []
            for dx, dy in ((-half, -half * 0.4), (half, -half * 0.4), (half, half * 0.4), (-half, half * 0.4)):
                points.append((x + dx * cos_a - dy * sin_a, y + dx * sin_a + dy * cos_a))
            draw.polygon(points, fill=(*piece["color"], 235))
        frame.paste(overlay, (0, 0), overlay)

    def _draw_sunburst(self, frame: "Image.Image", center: tuple[int, int], progress: float) -> None:
        from PIL import Image, ImageDraw

        if progress <= 0:
            return
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        cx, cy = center
        max_radius = frame.size[0] * 0.55 * progress
        ray_count = 18
        alpha = int(110 * progress)
        half_width = (math.pi / ray_count) * 0.6
        for i in range(ray_count):
            angle = (2 * math.pi / ray_count) * i
            p1 = (cx, cy)
            p2 = (cx + max_radius * math.cos(angle - half_width), cy + max_radius * math.sin(angle - half_width))
            p3 = (cx + max_radius * math.cos(angle + half_width), cy + max_radius * math.sin(angle + half_width))
            draw.polygon([p1, p2, p3], fill=(255, 210, 90, alpha))
        frame.paste(overlay, (0, 0), overlay)

    def _draw_sparkles(self, frame: "Image.Image", center: tuple[int, int], card_w: int, card_h: int, step: int) -> None:
        from PIL import Image, ImageDraw

        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        cx, cy = center
        spread_x, spread_y = card_w * 1.6, card_h * 0.9
        positions = [
            (cx - spread_x, cy - spread_y * 0.5),
            (cx + spread_x, cy - spread_y * 0.3),
            (cx - spread_x * 0.6, cy + spread_y),
            (cx + spread_x * 0.7, cy + spread_y * 0.8),
            (cx, cy - spread_y * 1.1),
        ]
        for i, (x, y) in enumerate(positions):
            if (step + i) % 2 == 0:
                continue
            size = 9
            draw.line((x - size, y, x + size, y), fill=(255, 255, 255, 220), width=3)
            draw.line((x, y - size, x, y + size), fill=(255, 255, 255, 220), width=3)
            draw.line((x - size * 0.6, y - size * 0.6, x + size * 0.6, y + size * 0.6), fill=(255, 235, 150, 200), width=2)
            draw.line((x - size * 0.6, y + size * 0.6, x + size * 0.6, y - size * 0.6), fill=(255, 235, 150, 200), width=2)
        frame.paste(overlay, (0, 0), overlay)

    def _draw_banner_text(self, frame: "Image.Image", text: str, y_center: float, progress: float) -> None:
        from PIL import Image, ImageDraw

        if progress <= 0:
            return
        size = max(18, int(72 * (0.5 + 0.5 * progress)))
        font = self._font(size)
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        bbox = draw.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (frame.size[0] - w) // 2 - bbox[0]
        y = y_center - h // 2 - bbox[1]
        alpha = int(255 * min(1.0, progress * 1.4))
        for dx, dy in ((-3, -3), (3, -3), (-3, 3), (3, 3), (0, -3), (0, 3), (-3, 0), (3, 0)):
            draw.text((x + dx, y + dy), text, font=font, fill=(20, 15, 5, alpha))
        draw.text((x, y), text, font=font, fill=(255, 205, 60, alpha))
        frame.paste(overlay, (0, 0), overlay)

    def _draw_ribbon(
        self,
        frame: "Image.Image",
        text: str,
        y_center: float,
        progress: float,
        *,
        accent: bool = False,
    ) -> None:
        from PIL import Image, ImageDraw

        if progress <= 0:
            return
        font = self._font(38 if accent else 34)
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = 34, 14
        width = max(1, min(frame.size[0] - 40, int((text_w + pad_x * 2) * min(1.0, progress * 1.3))))
        height = text_h + pad_y * 2
        x0 = (frame.size[0] - width) // 2
        y0 = int(y_center - height / 2)
        outline = (120, 255, 170, 255) if accent else (255, 205, 60, 255)
        draw.rounded_rectangle((x0, y0, x0 + width, y0 + height), radius=height // 2, fill=(0, 0, 0, 235), outline=outline, width=3)
        if progress >= 0.75:
            tx = (frame.size[0] - text_w) // 2 - bbox[0]
            ty = int(y_center - text_h / 2 - bbox[1])
            draw.text((tx, ty), text, font=font, fill=outline)
        frame.paste(overlay, (0, 0), overlay)

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

        scaled = self._scaled_asset(card.image_name, card_w, card_h)
        if scaled is not None:
            return scaled

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

        scaled = self._scaled_asset("back.png", card_w, card_h)
        if scaled is not None:
            return scaled

        img = Image.new("RGBA", (card_w, card_h), (35, 65, 140))
        draw = ImageDraw.Draw(img)
        radius = max(12, card_w // 10)
        border_w = max(3, card_w // 38)
        draw.rounded_rectangle((0, 0, card_w - 1, card_h - 1), radius=radius, outline="white", width=border_w)
        draw.text((card_w // 3, card_h // 2 - 12), "CARD", fill="white", font=self._font(max(20, card_w // 6)))
        return img

    def _scaled_asset(self, filename: str, card_w: int, card_h: int):
        """Return a high-quality resized copy of an asset, cached by (name, size).

        Card art is downscaled from large source PNGs to fit the row, sometimes
        by 5x+ when several cards share the row. Pillow's default resize filter
        (bicubic, single pass) aliases badly at that reduction ratio, which is
        what reads as "pixelated" card text/pips. Lanczos with a reducing_gap
        pre-shrinks in box-filtered steps first, which stays crisp at small
        sizes. Results are cached since the same (card, card_w) pairs recur
        across renders/hands.
        """
        from PIL import Image

        cache_key = (filename, card_w, card_h)
        cached = self._resized_cache.get(cache_key)
        if cached is not None:
            return cached

        source = self._source_cache.get(filename)
        if source is None:
            path = self.asset_dir / filename
            if not path.exists():
                return None
            source = Image.open(path).convert("RGBA")
            self._source_cache[filename] = source

        resample = getattr(Image, "Resampling", Image).LANCZOS
        scaled = source.resize((card_w, card_h), resample=resample, reducing_gap=3.0)
        self._resized_cache[cache_key] = scaled
        return scaled

    def _font(self, size: int):
        cached = self._font_cache.get(size)
        if cached is not None:
            return cached

        from PIL import ImageFont

        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size)
        except Exception:
            # When DejaVuSans.ttf isn't installed, truetype() searches several
            # system font directories before giving up - repeating that failed
            # search on every single text draw (a GIF frame draws several) is
            # a measurable chunk of render time, so the miss is cached too.
            font = ImageFont.load_default()
        self._font_cache[size] = font
        return font


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    dollars, rem = divmod(cents, 100)
    if rem:
        return f"{sign}${dollars:,}.{rem:02d}"
    return f"{sign}${dollars:,}"
