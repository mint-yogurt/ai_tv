from PIL import Image

from aitv import titles


def _centres(path, threshold=60):
    """The horizontal centre of every band of rows that has text in it."""
    img = Image.open(path).convert("RGBA")
    lum = Image.new("L", img.size)
    lum.putdata([round(a * (r + g + b) / 765) for r, g, b, a in img.getdata()])
    w, h = img.size
    bands, start = [], None
    for y in range(h + 1):
        row = lum.crop((0, y, w, y + 1)).point(lambda v: 255 if v > threshold else 0).getbbox() if y < h else None
        if row and start is None:
            start = y
        elif not row and start is not None:
            box = lum.crop((0, start, w, y)).point(lambda v: 255 if v > threshold else 0).getbbox()
            bands.append((box[0] + box[2]) / 2)
            start = None
    return bands


def test_every_line_of_a_card_is_centred(tmp_path):
    for name in titles.STYLES:
        dst = tmp_path / f"{name}.png"
        titles.card_layer("The Shart in Mart\nEverything must go. Now showing at a theatre near you.", name).save(dst)
        centres = _centres(dst)
        assert len(centres) >= 2, name
        assert all(abs(c - 320) <= 3 for c in centres), (name, centres)


def test_captions_are_centred_and_stay_inside_the_safe_area(tmp_path):
    for name in titles.STYLES:
        dst = titles.caption("Only $19.95 plus shipping and handling\nCall 1-800-555-0199", name, tmp_path / f"{name}.png")
        box = Image.open(dst).getchannel("A").getbbox()
        assert box[1] > 240 and box[3] < 480, name  # in the bottom half, not off the frame
        assert abs((box[0] + box[2]) / 2 - 320) <= 3, name
        assert box[2] - box[0] <= 640 * titles.SAFE + 60, name  # text plus its box padding


def test_fades_leave_the_text_at_full_strength_for_half_the_shot():
    from aitv import pipeline

    assert pipeline._fit_fades((1.2, 1.0), 2.2) == (0.6, 0.5)
    assert pipeline._fit_fades((0.3, 0.3), 4.0) == (0.3, 0.3)


def test_a_card_is_never_a_flat_colour(tmp_path):
    """Over nothing it gets a gradient; over a picture, the picture shows through."""
    plain = Image.open(titles.card("Toxic Horror Droplets", "hard_sell", tmp_path / "plain.png"))
    top, bottom = plain.getpixel((5, 5)), plain.getpixel((5, 474))
    assert top != bottom
    picture = tmp_path / "pic.png"
    Image.new("RGB", (640, 480), (200, 40, 40)).save(picture)
    over = Image.open(titles.card("Toxic Horror Droplets", "hard_sell", tmp_path / "over.png", picture=picture))
    r, g, b = over.getpixel((5, 5))
    assert r > g + 20 and r > b + 20  # the red picture, darkened


def test_card_text_loses_the_writers_labels():
    assert titles.clean_text("Toxic Horror Droplets\nlogo with tagline: The Candy That Bites Back") == \
        "Toxic Horror Droplets\nThe Candy That Bites Back"
    assert titles.clean_text("Super: 'Only $3.99'") == "Only $3.99"
    assert titles.clean_text("LOGO:\nHam Pillow") == "Ham Pillow"
    assert titles.clean_text("WARNING: may glow") == "WARNING: may glow"  # real on-screen words stay
    assert titles.clean_text("Results may vary. Stains probable.") == "Results may vary. Stains probable."
