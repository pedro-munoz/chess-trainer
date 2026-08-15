"""Generate the PWA icons in web/icons/ (run once; the PNGs are committed).

Renders the same knight glyph the site uses as its brand mark, in the site's
gold on the site's dark background, at the three sizes the manifest asks for.
The maskable variant keeps the glyph inside the safe zone Android crops to.

    python -m scripts.make_icons
"""

from PIL import Image, ImageDraw, ImageFont

from chess_trainer import PROJECT_ROOT

OUT = PROJECT_ROOT / "web" / "icons"
BG = "#14110d"
GOLD = "#d9a44e"
GLYPH = "\u265e"  # black chess knight — the filled one, same as the brand mark
# Android crops maskable icons to a circle inscribed in the middle 80%.
MASKABLE_SAFE = 0.60
PLAIN_SAFE = 0.78

FONT_CANDIDATES = [
    "C:/Windows/Fonts/seguisym.ttf",   # Segoe UI Symbol
    "C:/Windows/Fonts/SEGUIEMJ.TTF",
    "C:/Windows/Fonts/arial.ttf",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, size)
            if font.getbbox(GLYPH)[2] > 0:
                return font
        except OSError:
            continue
    raise SystemExit("No font with a chess glyph found; edit FONT_CANDIDATES.")


def render(size: int, safe: float, path) -> None:
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)

    # Fit the glyph to the safe zone by measuring at a reference size.
    ref = size
    font = load_font(ref)
    box = draw.textbbox((0, 0), GLYPH, font=font)
    glyph_w, glyph_h = box[2] - box[0], box[3] - box[1]
    scale = (size * safe) / max(glyph_w, glyph_h)
    font = load_font(max(8, int(ref * scale)))

    box = draw.textbbox((0, 0), GLYPH, font=font)
    x = (size - (box[2] - box[0])) / 2 - box[0]
    y = (size - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), GLYPH, font=font, fill=GOLD)
    img.save(path, "PNG", optimize=True)
    print(f"  {path.name}  {path.stat().st_size / 1024:.1f} KB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    render(192, PLAIN_SAFE, OUT / "icon-192.png")
    render(512, PLAIN_SAFE, OUT / "icon-512.png")
    render(512, MASKABLE_SAFE, OUT / "icon-512-maskable.png")


if __name__ == "__main__":
    main()
