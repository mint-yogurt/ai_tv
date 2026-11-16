"""Recurring characters pasted into generated scenes, all local.

A World character's reference image is cut out once (rembg, cached next to the library's derived
files) and pasted into a shot's scene at the position and size the shot plan asks for. The render
stage then runs a light img2img pass over the whole frame so the lighting and grain match, at a
strength low enough to keep the character's face (0.25 in the Nov 2026 test).

Frames are 16:9 for the video model, but the programme is 4:3: characters are placed inside the
4:3 middle so the final crop keeps them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Config

POSITIONS = {"left": 0.27, "center": 0.5, "right": 0.73}  # across the 4:3 middle of the frame
SIZES = {"closeup": 1.45, "waist": 0.95, "full": 0.85}     # figure height as a share of the frame
CHARACTER_IMAGE_ROLES = ("reference", "headshot")


def reference_image(conn: sqlite3.Connection, entity_id: int) -> sqlite3.Row | None:
    """The character's newest ready reference (or headshot) image."""
    return conn.execute(
        "SELECT a.* FROM assets a JOIN asset_entities ae ON ae.asset_id = a.id "
        f"WHERE ae.entity_id = ? AND ae.role IN ({','.join('?' * len(CHARACTER_IMAGE_ROLES))}) "
        "AND a.media = 'image' AND a.status = 'ready' ORDER BY ae.role = 'reference' DESC, a.id DESC LIMIT 1",
        (entity_id, *CHARACTER_IMAGE_ROLES),
    ).fetchone()


def cutout(cfg: Config, asset: sqlite3.Row) -> Path:
    """The reference with its background removed, cropped to the figure (RGBA PNG, cached)."""
    dst = cfg.store / "derived" / f"{asset['sha256']}_cutout.png"
    if not dst.is_file():
        from PIL import Image, PngImagePlugin
        from rembg import remove

        img = remove(Image.open(cfg.library / asset["path"]).convert("RGB"))
        box = img.getbbox()
        # Remember whether the figure runs off the bottom of the source (a waist-up photo).
        meta = PngImagePlugin.PngInfo()
        meta.add_text("cut_bottom", "1" if box and box[3] >= img.height - 2 else "0")
        (img.crop(box) if box else img).save(dst, pnginfo=meta)
    return dst


def paste(scene: Path, figures: list[tuple[Path, str, str]], dst: Path, safe_w: int = 640) -> Path:
    """Paste (cutout, position, size) figures onto scene, in order (later ones in front).

    A figure cut off at the bottom of its photo (waist-up) is always anchored below the frame's bottom
    edge, whatever size was asked for, so the cut never shows."""
    from PIL import Image

    frame = Image.open(scene).convert("RGB")
    w, h = frame.size
    left = (w - min(safe_w, w)) / 2
    for path, position, size in figures:
        fig = Image.open(path).convert("RGBA")
        cut_bottom = Image.open(path).info.get("cut_bottom") == "1"
        fh = round(h * SIZES.get(size, SIZES["waist"]))
        fw = round(fig.width * fh / fig.height)
        fig = fig.resize((fw, fh), Image.LANCZOS)
        cx = left + min(safe_w, w) * POSITIONS.get(position, 0.5)
        if size == "closeup":
            top = round(h * 0.03)                           # head near the top, body runs off the bottom
        elif cut_bottom or size == "waist":
            top = h - fh + round(h * 0.06)                  # cut edge hidden below the frame
        else:
            top = h - fh - round(h * 0.04)                  # standing, feet just above the bottom
        frame.paste(fig, (round(cx - fw / 2), top), fig)
    frame.save(dst)
    return dst
