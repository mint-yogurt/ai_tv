"""On-screen text: title cards and captions, drawn with Pillow as images.

Every line is measured and centred on its own, wrapped by pixel width inside the title-safe area
(80% of the frame) and shrunk until it fits. A card's lettering goes over a picture (a neighbouring
shot, darkened and softened) or, with no picture, a gradient in the style's colour: never a flat
colour. A caption is a transparent layer that the clip lays over its picture, faded in and out
(media._shot_graph).

The planner picks one style per production (plan.title_style), so text matches the piece's tone
and differs from one production to the next. Bump VERSION when the drawing changes, so cached
cards and captioned clips are redrawn.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSION = 4

OTF = "/usr/share/fonts/opentype/urw-base35/"
LATO = "/usr/share/fonts/truetype/lato/"

# size: the card title's height as a share of the frame; sub: later card lines relative to it.
# tracking: extra space between letters, as a share of the font size. stroke: outline width, same.
# shadow: (x, y offset as a share of size, blur px, opacity). glow: (blur px, opacity), drawn in the fill colour.
# sub_font / caption_font: other fonts for a card's smaller lines and for captions (default: font).
# caption: how captions stay readable over a picture: "shadow" or "box" (a dark band behind them).
# fade: (in, out) seconds for cards; caption_fade the same for captions.
STYLES = {
    "arthouse_serif": {
        "hint": "spaced-out serif capitals, quiet and expensive: art films, prestige drama, pretentious or eerie trailers",
        "font": OTF + "C059-Roman.otf", "upper": True, "tracking": 0.28, "size": 0.085, "sub": 0.55,
        "fill": (236, 232, 222), "stroke": 0.0, "shadow": (0.0, 0.0, 6, 0.6), "glow": None,
        "caption": "shadow", "fade": (1.2, 1.0), "caption_fade": (0.6, 0.5)},
    "hairline": {
        "hint": "thin, wide-spaced modern capitals: minimal, cold, unsettling, perfume, sci-fi",
        "font": LATO + "Lato-Light.ttf", "caption_font": LATO + "Lato-Semibold.ttf", "upper": True, "tracking": 0.4,
        "size": 0.08, "sub": 0.6,
        "fill": (245, 245, 245), "stroke": 0.0, "shadow": None, "glow": (8, 0.45),
        "caption": "shadow", "fade": (1.0, 1.0), "caption_fade": (0.5, 0.5)},
    "blockbuster": {
        "hint": "heavy bold serif capitals with a glow: Hollywood movie trailers, epic, action, disaster",
        "font": OTF + "P052-Bold.otf", "upper": True, "tracking": 0.1, "size": 0.1, "sub": 0.5,
        "fill": (242, 238, 228), "stroke": 0.0, "shadow": (0.0, 0.03, 4, 0.8), "glow": (14, 0.5),
        "caption": "shadow", "fade": (0.6, 0.6), "caption_fade": (0.3, 0.3)},
    "hard_sell": {
        "hint": "fat yellow sans with a black outline and drop shadow: late-night hard sell, prices, phone numbers, sales",
        "font": OTF + "NimbusSans-Bold.otf", "upper": True, "tracking": 0.0, "size": 0.11, "sub": 0.6,
        "fill": (255, 222, 40), "stroke": 0.09, "stroke_fill": (0, 0, 0), "shadow": (0.06, 0.06, 0, 0.9), "glow": None,
        "caption": "shadow", "fade": (0.15, 0.15), "caption_fade": (0.1, 0.15)},
    "network_promo": {
        "hint": "round geometric sans, bright and friendly: 80s network promos, sitcoms, game shows, toys",
        "font": OTF + "URWGothic-Demi.otf", "upper": False, "tracking": 0.02, "size": 0.1, "sub": 0.6,
        "fill": (255, 255, 255), "stroke": 0.05, "stroke_fill": (20, 20, 70), "shadow": (0.05, 0.05, 2, 0.7), "glow": None,
        "caption": "shadow", "fade": (0.3, 0.3), "caption_fade": (0.2, 0.2)},
    "warm_seventies": {
        "hint": "warm chunky Bookman in cream: wholesome, folksy, local business, retro, family products",
        "font": OTF + "URWBookman-Demi.otf", "upper": False, "tracking": 0.0, "size": 0.095, "sub": 0.6,
        "fill": (250, 214, 150), "stroke": 0.0, "shadow": (0.04, 0.05, 3, 0.85), "glow": None,
        "caption": "shadow", "fade": (0.5, 0.5), "caption_fade": (0.3, 0.3)},
    "bulletin": {
        "hint": "tall narrow sans capitals on dark bands: urgent, official, hotlines, news, warnings, PSAs",
        "font": OTF + "NimbusSansNarrow-Bold.otf", "upper": True, "tracking": 0.05, "size": 0.1, "sub": 0.6,
        "fill": (255, 255, 255), "stroke": 0.0, "shadow": None, "glow": None,
        "caption": "box", "fade": (0.2, 0.2), "caption_fade": (0.15, 0.15)},
    "luxury_script": {
        "hint": "gold calligraphic script: luxury, romance, cosmetics, chocolates, jewellery, fine dining",
        "font": OTF + "Z003-MediumItalic.otf", "upper": False, "tracking": 0.0, "size": 0.12, "sub": 0.45,
        "sub_font": OTF + "P052-Roman.otf", "caption_font": OTF + "P052-Bold.otf", "fill": (232, 200, 128), "stroke": 0.0,
        "shadow": (0.0, 0.02, 5, 0.7), "glow": (10, 0.25), "caption": "shadow", "fade": (0.9, 0.9), "caption_fade": (0.5, 0.5)},
}
DEFAULT_STYLE = "network_promo"

SAFE = 0.8           # text stays inside this share of the frame's width
CAPTION_SIZE = 0.055  # caption height as a share of the frame
CAPTION_BOTTOM = 0.09 # caption block's bottom edge sits this far above the frame's


def style(name: str | None) -> dict:
    return STYLES.get(name or DEFAULT_STYLE, STYLES[DEFAULT_STYLE])


def _font(path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, max(6, size))


def _width(text: str, font, tracking: float) -> float:
    if not tracking or len(text) < 2:
        return font.getlength(text)
    return sum(font.getlength(ch) for ch in text) + tracking * font.size * (len(text) - 1)


def _wrap(text: str, font, tracking: float, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and _width(trial, font, tracking) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def _balanced(text: str, font, tracking: float, max_w: float) -> list[str]:
    """Wrapped into as few lines as fit, with the lines as even as possible (no one-word last line)."""
    lines = _wrap(text, font, tracking, max_w)
    if len(lines) < 2:
        return lines
    lo, hi = 0.0, max_w
    while hi - lo > 2:
        mid = (lo + hi) / 2
        if len(_wrap(text, font, tracking, mid)) == len(lines):
            hi = mid
        else:
            lo = mid
    return _wrap(text, font, tracking, hi)


def _layout(paragraphs: list[tuple[str, str, int]], st: dict, max_w: float, max_h: float, gap: float = 0.0):
    """[(text, font path, size)] -> rows [(line, font, gap above)] and the block's height, shrunk until
    it fits. gap: space between the first paragraph (a card's title) and the rest."""
    scale = 1.0
    while True:
        rows = []
        for i, (text, path, size) in enumerate(paragraphs):
            font = _font(path, round(size * scale))
            for j, line in enumerate(_balanced(text, font, st["tracking"], max_w) or [""]):
                rows.append((line, font, gap * scale if i == 1 and j == 0 else 0.0))
        height = sum(f.size * 1.25 + g for _, f, g in rows)
        widest = max((_width(l, f, st["tracking"]) for l, f, _ in rows), default=0)
        if (height <= max_h and widest <= max_w) or scale < 0.3:
            return rows, height
        scale *= 0.92


def _draw_line(draw, x: float, y: float, line: str, font, st: dict, fill) -> None:
    stroke = round(st.get("stroke", 0) * font.size)
    kw = {"stroke_width": stroke, "stroke_fill": tuple(st.get("stroke_fill", (0, 0, 0))) + (255,)} if stroke else {}
    if not st["tracking"]:
        draw.text((x, y), line, font=font, fill=fill, **kw)
        return
    # Letter by letter: every outline first, then every fill, so no outline cuts into its neighbour.
    for pass_kw in ([kw, {}] if kw else [{}]):
        cx = x
        for ch in line:
            draw.text((cx, y), ch, font=font, fill=pass_kw.get("stroke_fill", fill), **pass_kw)
            cx += font.getlength(ch) + st["tracking"] * font.size


def _render(rows, top: float, w: int, h: int, st: dict):
    """The text rows centred line by line from top, with the style's glow and shadow, as RGBA."""
    from PIL import Image, ImageDraw, ImageFilter

    text = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text)
    y = top
    for line, font, gap in rows:
        y += gap
        x = (w - _width(line, font, st["tracking"])) / 2
        _draw_line(draw, x, y, line, font, st, tuple(st["fill"]) + (255,))
        y += font.size * 1.25
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    alpha = text.getchannel("A")
    size = rows[0][1].size if rows else 20
    if st.get("glow"):
        blur, opacity = st["glow"]
        glow = Image.new("RGBA", (w, h), tuple(st["fill"]) + (0,))
        glow.putalpha(alpha.filter(ImageFilter.GaussianBlur(blur)).point(lambda a: round(a * opacity)))
        out = Image.alpha_composite(out, glow)
    if st.get("shadow"):
        dx, dy, blur, opacity = st["shadow"]
        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shadow.putalpha(alpha.point(lambda a: round(a * opacity)))
        shadow = shadow.transform((w, h), Image.AFFINE, (1, 0, -dx * size, 0, 1, -dy * size))
        if blur:
            shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
        out = Image.alpha_composite(out, shadow)
    return Image.alpha_composite(out, text)


# Words a writer puts on a card line to say what it is rather than what it says ("logo with tagline:").
_LABEL = (r"(?:logo|tagline|slogan|supers?|text|title(?: card)?|card|caption|on[- ]?screen(?: text)?|graphic|"
          r"fine print|chyron|lower third|end card|product name)")
_LABELS = re.compile(rf"^\s*{_LABEL}(?:\s*(?:with|and|plus|,|/|&)\s*{_LABEL})*\s*:\s*", re.IGNORECASE)


def clean_text(text: str | None) -> str | None:
    """Card or caption text with the writer's direction labels taken off each line ("logo with tagline: The
    Candy That Bites Back" -> "The Candy That Bites Back"); a line that was only a label goes."""
    if not text:
        return text
    out = []
    for line in text.split("\n"):
        bare = _LABELS.sub("", line).strip()
        if bare != line.strip():
            bare = bare.strip("'\"‘’“”").strip()
        if bare:
            out.append(bare)
    return "\n".join(out)


def card_background(dst: Path, w: int = 640, h: int = 480, picture: Path | None = None,
                    style_name: str | None = None) -> Path:
    """What a card's lettering sits on: picture filling the frame, softened and darkened so the text
    reads, or without one a gradient from the style's colour (dimmed) down to near black."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    if picture is not None:
        with Image.open(picture) as im:
            bg = ImageOps.fit(im.convert("RGB"), (w, h))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(max(2, w // 160)))).enhance(0.45)
    else:
        top = tuple(round(c * 0.3) for c in style(style_name)["fill"])
        bottom = (8, 8, 12)
        bg = Image.new("RGB", (w, h))
        bg.putdata([tuple(round(a + (b - a) * y / max(1, h - 1)) for a, b in zip(top, bottom))
                    for y in range(h) for _ in range(w)])
    bg.save(dst)
    return dst


def card_layer(text: str, style_name: str | None, w: int = 640, h: int = 480):
    """A card's lettering on a transparent layer (Pillow RGBA): the first line of text is the title; any
    later lines are set smaller underneath (a tagline, a date). A long single line is set at the
    smaller size too. Outlined and shadowed, so it reads over any picture."""
    st = dict(style(style_name))
    if st["upper"]:
        text = text.upper()
    st["stroke"] = max(st.get("stroke", 0), 0.04)
    st.setdefault("stroke_fill", (0, 0, 0))
    st["shadow"] = st.get("shadow") or (0.04, 0.05, 4, 0.85)
    parts = [p.strip() for p in text.split("\n") if p.strip()] or [""]
    big, small = round(h * st["size"]), round(h * st["size"] * st["sub"])
    sub_font = st.get("sub_font", st["font"])
    if len(parts[0]) <= 40:
        # A title shrinks a little to stay on one line before it wraps.
        one_line = big * w * SAFE / max(1.0, _width(parts[0], _font(st["font"], big), st["tracking"]))
        paragraphs = [(parts[0], st["font"], big if one_line >= big else round(one_line) if one_line >= big * 0.7 else big)]
    else:
        paragraphs = [(parts[0], sub_font, small)]
    paragraphs += [(p, sub_font, small) for p in parts[1:]]
    rows, height = _layout(paragraphs, st, w * SAFE, h * 0.8, gap=big * 0.3)
    return _render(rows, (h - height) / 2, w, h, st)


def card(text: str, style_name: str | None, dst: Path, w: int = 640, h: int = 480,
         picture: Path | None = None) -> Path:
    """A full-frame title card as one image: card_layer's lettering over card_background."""
    from PIL import Image

    bg = card_background(dst, w, h, picture, style_name)
    with Image.open(bg) as im:
        frame = im.convert("RGBA")
    Image.alpha_composite(frame, card_layer(text, style_name, w, h)).convert("RGB").save(dst)
    return dst


def caption(text: str, style_name: str | None, dst: Path, w: int = 640, h: int = 480) -> Path:
    """A transparent caption layer: centred lines near the bottom, readable over any picture."""
    from PIL import Image, ImageDraw

    st = dict(style(style_name))
    if st["upper"]:
        text = text.upper()
    st["tracking"] = min(st["tracking"], 0.12)  # wide tracking only reads at card size
    if st["caption"] == "shadow":  # something dark behind the letters, whatever the style
        st["stroke"] = max(st.get("stroke", 0), 0.06)
        st.setdefault("stroke_fill", (0, 0, 0))
        st["shadow"] = st.get("shadow") or (0.04, 0.05, 3, 0.8)
        st["glow"] = None
    else:
        st["stroke"], st["shadow"], st["glow"] = 0.0, None, None
    font = st.get("caption_font", st["font"])
    paragraphs = [(p.strip(), font, round(h * CAPTION_SIZE)) for p in text.split("\n") if p.strip()]
    rows, height = _layout(paragraphs or [("", font, 10)], st, w * SAFE, h * 0.3)
    top = h - h * CAPTION_BOTTOM - height
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if st["caption"] == "box":
        pad = rows[0][1].size * 0.45
        widest = max(_width(l, f, st["tracking"]) for l, f, _ in rows)
        ImageDraw.Draw(layer).rectangle(((w - widest) / 2 - pad * 1.5, top - pad, (w + widest) / 2 + pad * 1.5,
                                         top + height + pad * 0.4), fill=(0, 0, 0, 170))
    Image.alpha_composite(layer, _render(rows, top, w, h, st)).save(dst)
    return dst


def overlay(text: str, style_name: str | None, w: int = 640, h: int = 480, position: str = "center"):
    """A card's lettering on a transparent layer, for a title laid over footage (pipeline v2's EDL titles):
    a card's sizes, with an outline and shadow added so it reads over any picture. position: center,
    upper or lower third. Returns a Pillow RGBA image."""
    st = dict(style(style_name))
    if st["upper"]:
        text = text.upper()
    st["stroke"] = max(st.get("stroke", 0), 0.05)
    st.setdefault("stroke_fill", (0, 0, 0))
    st["shadow"] = st.get("shadow") or (0.04, 0.05, 4, 0.85)
    parts = [p.strip() for p in text.split("\n") if p.strip()] or [""]
    big, small = round(h * st["size"]), round(h * st["size"] * st["sub"])
    sub_font = st.get("sub_font", st["font"])
    paragraphs = [(parts[0], st["font"] if len(parts[0]) <= 40 else sub_font, big if len(parts[0]) <= 40 else small)]
    paragraphs += [(p, sub_font, small) for p in parts[1:]]
    rows, height = _layout(paragraphs, st, w * SAFE, h * 0.6, gap=big * 0.3)
    top = {"upper": h * 0.12, "lower": h * 0.88 - height}.get(position, (h - height) / 2)
    return _render(rows, top, w, h, st)
