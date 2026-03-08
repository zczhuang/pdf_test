from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"

TOP_BG = (103, 119, 255)
BOTTOM_BG = (49, 70, 201)
GLOW = (170, 182, 255, 105)
PAGE_LEFT = (247, 241, 232, 255)
PAGE_RIGHT = (251, 247, 241, 255)
LINE = (204, 214, 255, 255)
SPINE = (217, 224, 255, 255)
SUN = (233, 124, 90, 255)
GOLD = (244, 197, 108, 255)
SHADOW = (31, 49, 140, 46)


def lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def rounded_rect(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], radius: float, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def ellipse_box(cx: float, cy: float, radius: float) -> tuple[float, float, float, float]:
    return (cx - radius, cy - radius, cx + radius, cy + radius)


def draw_star(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float, fill):
    points = [
        (cx, cy - size),
        (cx + size * 0.3, cy - size * 0.3),
        (cx + size, cy),
        (cx + size * 0.3, cy + size * 0.3),
        (cx, cy + size),
        (cx - size * 0.3, cy + size * 0.3),
        (cx - size, cy),
        (cx - size * 0.3, cy - size * 0.3),
    ]
    draw.polygon(points, fill=fill)


def create_icon(size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(canvas)

    for y in range(size):
        t = y / max(size - 1, 1)
        color = (
            lerp(TOP_BG[0], BOTTOM_BG[0], t),
            lerp(TOP_BG[1], BOTTOM_BG[1], t),
            lerp(TOP_BG[2], BOTTOM_BG[2], t),
            255,
        )
        bg_draw.line((0, y, size, y), fill=color)

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        ellipse_box(size * 0.35, size * 0.24, size * 0.24),
        fill=GLOW,
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size * 0.07))
    canvas.alpha_composite(glow)

    base = ImageDraw.Draw(canvas)
    base.ellipse(ellipse_box(size * 0.68, size * 0.27, size * 0.115), fill=SUN)
    draw_star(base, size * 0.48, size * 0.215, size * 0.055, GOLD)

    shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    left_page = [
        (size * 0.24, size * 0.30),
        (size * 0.34, size * 0.26),
        (size * 0.45, size * 0.27),
        (size * 0.50, size * 0.31),
        (size * 0.50, size * 0.72),
        (size * 0.40, size * 0.67),
        (size * 0.31, size * 0.67),
        (size * 0.24, size * 0.70),
    ]
    right_page = [
        (size * 0.76, size * 0.30),
        (size * 0.66, size * 0.26),
        (size * 0.55, size * 0.27),
        (size * 0.50, size * 0.31),
        (size * 0.50, size * 0.72),
        (size * 0.60, size * 0.67),
        (size * 0.69, size * 0.67),
        (size * 0.76, size * 0.70),
    ]
    shadow_draw.polygon([(x, y + size * 0.02) for x, y in left_page], fill=SHADOW)
    shadow_draw.polygon([(x, y + size * 0.02) for x, y in right_page], fill=SHADOW)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=size * 0.03))
    canvas.alpha_composite(shadow_layer)

    base.polygon(left_page, fill=PAGE_LEFT)
    base.polygon(right_page, fill=PAGE_RIGHT)
    base.line((size * 0.50, size * 0.31, size * 0.50, size * 0.72), fill=SPINE, width=max(2, round(size * 0.03)))

    line_width = max(2, round(size * 0.028))
    for y_ratio in (0.40, 0.49):
        base.line((size * 0.31, size * y_ratio, size * 0.42, size * y_ratio), fill=LINE, width=line_width)
        base.line((size * 0.58, size * y_ratio, size * 0.69, size * y_ratio), fill=LINE, width=line_width)

    base.arc(
        (size * 0.25, size * 0.59, size * 0.75, size * 0.84),
        start=196,
        end=344,
        fill=GOLD,
        width=max(2, round(size * 0.03)),
    )

    return canvas


def save_icon(size: int, filename: str):
    image = create_icon(size)
    image.save(STATIC_DIR / filename, format="PNG")


def main():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    save_icon(512, "icon-512.png")
    save_icon(192, "icon-192.png")
    save_icon(180, "apple-touch-icon.png")
    save_icon(32, "favicon-32.png")


if __name__ == "__main__":
    main()
