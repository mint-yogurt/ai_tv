"""PRIME CHEESE: a 30 s block promo for ChupTV. Rebuilds prime_cheese_promo.mp4 from the sources.

A pretentious luxury cheese advert that turns out to be aged "in front of a live studio audience": the record
scratches, the laugh track explodes, and the block's sitcoms get graded like cheeses. Then the cheese again, in gold.
Picture: 640x480 (4:3), 30 fps.
Run from ~/ai_tv with the venv:  .venv/bin/python test_outputs/video/2026-11-15_prime_cheese_promo/build.py
"""
import json, math, random, shutil, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
WORK = HERE / ".work"  # hidden: the Tests page skips dot folders
FONT = str(HERE.parents[2] / "library" / "fonts" / "CinzelDecorative-Black.ttf")  # the font archive
W, H, FPS, LENGTH = 640, 480, 30, 30.0
TV = Path("/mnt/hdd_1tb/tv")
DINO1 = TV / "Dinosaurs (1991)/Season 1/Dinosaurs (1991) - S01E01 - The Mighty Megalosaurus (480p DVD x265 Panda).mkv"
DINO3 = TV / "Dinosaurs (1991)/Season 1/Dinosaurs (1991) - S01E03 - Hurling Day (480p DVD x265 Panda).mkv"
PS2 = TV / "Perfect Strangers/Season 2/2x02 Hunks Like Us.avi"
PS5 = TV / "Perfect Strangers/Season 2/2x05 Lifesavers.avi"
HI1 = TV / "Home Improvement (1991)/Season 1/Home Improvement (1991) - S01E01 - Pilot (480p DVD x265 Panda).mkv"
HI2 = TV / "Home Improvement (1991)/Season 1/Home Improvement (1991) - S01E02 - Mow Better Blues (480p DVD x265 Panda).mkv"
ALF1 = TV / "Alf/ALF S01 (360p re-dvdrip)/ALF S01E01 A.L.F..mp4"
ALF3 = TV / "Alf/ALF S01 (360p re-dvdrip)/ALF S01E03 Looking for Lucky.mp4"
AI = HERE / "src" / "ai_clip.mp4"
MUSIC = HERE / "music" / "prime_cheese.wav"
SFX = {k: v["path"] for k, v in json.loads((HERE / "src" / "sfx.json").read_text()).items()}
VOICE = HERE / "voice"

LUX = "eq=saturation=1.1:contrast=1.05:gamma=0.95,colorbalance=rs=0.06:gs=0.02:bs=-0.08:rm=0.05:bm=-0.06"
# The sitcoms dressed up as an advert: warm, soft, glowing (a blurred copy screened over the picture).
GLOW = ",split[a][b];[b]gblur=sigma=9[g];[a][g]blend=all_mode=screen:all_opacity=0.45"
BRIGHT = "eq=saturation=1.35:contrast=1.12"  # the laugh-track half: loud and saturated

SCRATCH = 12.0      # the record scratch: the advert ends, the sitcoms begin
STROBE = (21.7, 23.5)
LOGO = 23.5

# The advert (0-12): (start, length, source, in point, speed). Portraits dissolve into each other.
OPEN = (0.0, 4.2, AI, 0.0, 1.0)
PORTRAITS = [  # each fades over the last for XF seconds; speed 0.5, frames held
    (DINO3, 31.0),   # Earl's big grin
    (PS2, 39.3),     # Balki waving, America or Burst
    (HI2, 12.6),     # Tim
    (ALF3, 13.2),    # ALF in the kitchen window
]
PORTRAIT_START, PORTRAIT_LEN, XF = 4.2, 1.35, 0.3
SET = (9.6, SCRATCH - 9.6, ALF3, 31.0, 0.6)   # the Tanners' living room, a slow push: "...a live studio audience"

# The laugh track (12-21.3): each show with its own sound, and a grade stamped on it.
# (start, length, source, in point, stamp or None, stamp at[, audio in point if not the picture's])
MONTAGE = [
    (12.0, 2.4, DINO3, 201.55, "SHARP", 1.65),       # Baby takes the pan to Earl: "not the mama!"
    (14.4, 2.1, PS5, 244.62, "IMPORTED", 1.5),       # Balki: "It's a custom of my people."
    (16.5, 1.7, HI2, 86.65, "EXTRA STRONG", 1.05),   # Mark on the mower: "More power!"
    (18.2, 1.1, ALF3, 81.9, None, 0, 79.95),         # ALF in the window; his "Grease fire!" is from the shot before (Kate's)
    (19.3, 2.4, ALF3, 85.75, "SMOKED", 1.6),         # smoke out of the window: "Never mind the curtains. Put me out."
]
STROBE_POOL = [(DINO3, 31.6), (PS2, 75.0), (HI2, 19.4), (ALF3, 19.4), (DINO3, 52.6), (PS2, 70.8),
               (HI2, 13.2), (ALF3, 28.4), (DINO3, 40.6), (PS2, 68.3), (PS2, 69.2)]   # big grins and takes, no credits on screen
# The finale: the cheese again, slower, pushing in, under the logo.
FINALE = (LOGO, LENGTH - LOGO, AI, 1.0, 0.5)

CARDS = [
    (4.5, 9.3, "The Reserve Collection", "small"),
    (LOGO + 0.15, LENGTH, "PRIME|CHEESE", "logo"),
    (26.1, LENGTH, "only on chuptv", "tag"),
]
GOLD_LIGHT, GOLD_DARK = (255, 232, 150), (176, 120, 30)
WAX = (196, 24, 30)


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def frames_between(a, b):
    return round(b * FPS) - round(a * FPS)


FIT = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"


def clip(out, src, at, frames, speed=1.0, grade="null", extra=""):
    vf = f"{FIT},fps={FPS},setpts=PTS/{speed},fps={FPS},{grade}{extra},format=yuv420p"
    run("ffmpeg", "-v", "error", "-y", "-ss", at, "-i", src, "-an", "-vf", vf, "-frames:v", frames,
        "-c:v", "libx264", "-crf", 14, "-preset", "fast", out)
    return out


def push(frames, amount=0.12, cx=0.5, cy=0.5):
    """A slow, even push-in over the shot."""
    return (f",scale={W * 2}:{H * 2},zoompan=z='1+{amount}*on/{frames}':x='(iw-iw/zoom)*{cx}':y='(ih-ih/zoom)*{cy}'"
            f":d=1:s={W}x{H}:fps={FPS}")


def portraits():
    """The four portraits, slowed, glowing, each dissolving into the next."""
    n = len(PORTRAITS)
    total = SET[0] - PORTRAIT_START
    seg = (total + XF * (n - 1)) / n
    parts = []
    for i, (src, at) in enumerate(PORTRAITS):
        f = round(seg * FPS)
        parts.append(clip(WORK / f"p{i}.mp4", src, at, f, 0.5, LUX, GLOW + push(f, 0.08)))
    graph, last, off = [], "[0:v]", 0.0
    for i in range(1, n):
        off += seg - XF
        graph.append(f"{last}[{i}:v]xfade=transition=fade:duration={XF}:offset={off:.3f}[x{i}]")
        last = f"[x{i}]"
    args = []
    for p in parts:
        args += ["-i", p]
    out = WORK / "portraits.mp4"
    run("ffmpeg", "-v", "error", "-y", *args, "-filter_complex", ";".join(graph), "-map", last,
        "-frames:v", frames_between(PORTRAIT_START, SET[0]), "-c:v", "libx264", "-crf", 14, "-preset", "fast",
        "-pix_fmt", "yuv420p", out)
    return out


def picture():
    parts = []
    s, l, src, at, sp = OPEN
    f = frames_between(s, s + l)
    parts.append(clip(WORK / "a0.mp4", src, at, f, sp, LUX, push(f, 0.10, 0.5, 0.45)))
    parts.append(portraits())
    s, l, src, at, sp = SET
    f = frames_between(s, s + l)
    parts.append(clip(WORK / "a1.mp4", src, at, f, sp, LUX, GLOW + push(f, 0.18)))
    for i, (start, length, src, at, stamp, stamp_at, *_) in enumerate(MONTAGE):
        parts.append(clip(WORK / f"m{i:02d}.mp4", src, at, frames_between(start, start + length), 1.0, BRIGHT))
    rng = random.Random(7)
    t, k = STROBE[0], 0
    while t < STROBE[1] - 1e-6:
        n = min(rng.choice((2, 3, 3, 4)), frames_between(t, STROBE[1]))
        src, at = STROBE_POOL[k % len(STROBE_POOL)]
        look = ",eq=brightness=0.3:contrast=1.5" if k % 3 == 2 else ""
        parts.append(clip(WORK / f"s{k:03d}.mp4", src, at, n, 1.0, BRIGHT, look))
        t = round(t * FPS + n) / FPS
        k += 1
    s, l, src, at, sp = FINALE
    f = frames_between(s, s + l)
    parts.append(clip(WORK / "z0.mp4", src, at, f, sp, LUX, push(f, 0.14, 0.5, 0.4)))
    (WORK / "parts.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = WORK / "picture.mp4"
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", 0, "-i", WORK / "parts.txt", "-c", "copy", out)
    return out


# --- titles -------------------------------------------------------------------------------------------------

def font(size):
    return ImageFont.truetype(FONT, size)


def tracked(draw, xy, text, f, fill, tracking):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + tracking


def tracked_width(text, f, tracking):
    return sum(f.getlength(ch) for ch in text) + tracking * (len(text) - 1)


def gold(mask, top, bottom, shine=None):
    """Gold leaf through a mask: light at the top, a dark band, light again; shine is a moving glint (0-1)."""
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        p = min(max((y - top) / max(1, bottom - top), 0), 1)
        k = 0.5 + 0.5 * math.cos(p * math.pi * 2.2)
        grad.putpixel((0, y), tuple(round(d + (l - d) * k) for l, d in zip(GOLD_LIGHT, GOLD_DARK)))
    fill = grad.resize((W, H)).convert("RGBA")
    if shine is not None:  # a diagonal glint sweeping left to right
        glint = Image.new("L", (W, H), 0)
        x = -200 + shine * (W + 400)
        ImageDraw.Draw(glint).polygon([(x, 0), (x + 60, 0), (x - 60, H), (x - 120, H)], fill=255)
        glint = glint.filter(ImageFilter.GaussianBlur(14))
        fill = Image.composite(Image.new("RGBA", (W, H), (255, 252, 235, 255)), fill, glint)
    fill.putalpha(mask)
    return fill


def gold_line(layer, text, size, cy, alpha, tracking=4, shine=None):
    f = font(size)
    w = tracked_width(text, f, tracking)
    while w > W - 60:
        size -= 2
        f = font(size)
        w = tracked_width(text, f, tracking)
    x, y = (W - w) / 2, cy - size * 0.62
    mask = Image.new("L", (W, H), 0)
    tracked(ImageDraw.Draw(mask), (x, y), text, f, 255, tracking)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.putalpha(mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(5)).transform((W, H), Image.AFFINE, (1, 0, -2, 0, 1, -3)))
    edge = Image.new("RGBA", (W, H), (60, 30, 0, 0))
    edge.putalpha(mask.filter(ImageFilter.MaxFilter(3)))
    glow = Image.new("RGBA", (W, H), (255, 200, 90, 0))
    glow.putalpha(mask.filter(ImageFilter.GaussianBlur(10)).point(lambda v: v // 2))
    out = Image.alpha_composite(shadow, glow)
    out = Image.alpha_composite(out, edge)
    out = Image.alpha_composite(out, gold(mask, y + size * 0.15, y + size * 1.05, shine))
    out.putalpha(out.getchannel("A").point(lambda a: round(a * alpha)))
    layer.alpha_composite(out)


def stamp(layer, word, age, seed):
    """A red wax grading stamp, slammed on: oversized for 2 frames, then lands with a tiny bounce."""
    rng = random.Random(seed)
    scale = 0.7 * (1.9 if age < 1 / FPS else 1.25 if age < 2 / FPS else 0.96 if age < 3 / FPS else 1.0)
    f = font(64 if len(word) <= 6 else 54 if len(word) <= 8 else 46)
    tw = tracked_width(word, f, 3)
    ew, eh = max(tw + 140, 300), 200
    size = max(520, round(ew) + 40)
    seal = Image.new("RGBA", (size, size // 2 if size // 2 > eh + 40 else eh + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(seal)
    sh = seal.height
    box = ((size - ew) / 2, (sh - eh) / 2, (size + ew) / 2, (sh + eh) / 2)
    d.rounded_rectangle(box, radius=eh // 2, outline=WAX + (255,), width=9)
    d.rounded_rectangle((box[0] + 16, box[1] + 16, box[2] - 16, box[3] - 16), radius=eh // 2 - 16, outline=WAX + (255,), width=3)
    tracked(d, ((size - tw) / 2, sh / 2 - f.size * 0.62), word, f, WAX + (255,), 3)
    small = font(18)
    for txt, yy in (("GRADE", box[1] + 24), ("PRIME", box[3] - 44)):
        sw = tracked_width(txt, small, 6)
        tracked(d, ((size - sw) / 2, yy), txt, small, WAX + (255,), 6)
    # worn ink: knock random specks out of the alpha
    a = seal.getchannel("A")
    speck = Image.new("L", seal.size, 255)
    sd = ImageDraw.Draw(speck)
    for _ in range(260):
        x, y, r = rng.uniform(0, size), rng.uniform(0, sh), rng.uniform(1, 4)
        sd.ellipse((x - r, y - r, x + r, y + r), fill=rng.choice((0, 90)))
    seal.putalpha(Image.composite(a, Image.new("L", seal.size, 0), speck).point(lambda v: round(v * 0.9)))
    seal = seal.rotate(rng.uniform(-11, -5), resample=Image.BICUBIC, expand=True)
    seal = seal.resize((round(seal.width * scale), round(seal.height * scale)), Image.BICUBIC)
    cx, cy = W * rng.uniform(0.47, 0.53), H * 0.72
    shadow = Image.new("RGBA", seal.size, (0, 0, 0, 0))
    shadow.putalpha(seal.getchannel("A").filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(3)).point(lambda v: min(255, v)))
    paper = Image.new("RGBA", seal.size, (255, 244, 220, 0))   # a pale halo so the red reads on warm skin
    paper.putalpha(seal.getchannel("A").filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(4)).point(lambda v: v * 3 // 4))
    layer.alpha_composite(paper, (round(cx - seal.width / 2), round(cy - seal.height / 2)))
    layer.alpha_composite(seal, (round(cx - seal.width / 2), round(cy - seal.height / 2)))


def backing(layer, alpha):
    """A soft dark pool behind the logo and the tag, so gold reads over pale cheese."""
    pool = Image.new("L", (W, H), 0)
    ImageDraw.Draw(pool).ellipse((40, 100, W - 40, 330), fill=150)
    ImageDraw.Draw(pool).rounded_rectangle((90, 372, W - 90, 424), radius=26, fill=150)
    pool = pool.filter(ImageFilter.GaussianBlur(30)).point(lambda v: round(v * alpha))
    dark = Image.new("RGBA", (W, H), (12, 6, 0, 0))
    dark.putalpha(pool)
    layer.alpha_composite(dark)


def text_frame(t):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for start, end, text, style in CARDS:
        if not start <= t < end:
            continue
        if style == "small":
            fade = min(1.0, (t - start) / 0.6, (end - t) / 0.5)
            gold_line(layer, text, 30, 52, fade, tracking=3)
        elif style == "logo":
            age = t - start
            backing(layer, min(1.0, age / 0.6))
            top, bottom = text.split("|")
            # each word rises out of the dark, then a glint sweeps across both
            shine = (age - 1.4) / 0.9 if 1.4 <= age < 2.3 else None
            gold_line(layer, top, 92, 168, min(1.0, age / 0.6), 8, shine)
            gold_line(layer, bottom, 92, 270, min(1.0, max(0.0, age - 0.35) / 0.6), 8, shine)
        elif style == "tag":
            gold_line(layer, text.upper(), 36, 398, min(1.0, (t - start) / 0.5), 6)
    for i, (start, length, src, at, word, word_at, *_) in enumerate(MONTAGE):
        if word and start + word_at <= t < start + length:
            stamp(layer, word, t - start - word_at, i)
    return layer


def titles():
    out = WORK / "titles"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for i in range(round(LENGTH * FPS)):
        text_frame(i / FPS).save(out / f"{i:04d}.png")
    return out


# --- sound --------------------------------------------------------------------------------------------------

VOICE_LINES = [  # (take, at, volume)
    ("1_age", 0.6, 1.0),
    ("2_aged", 4.2, 1.0),
    ("3_audience", 8.35, 1.0),     # "audience" has to be over before the scratch
    ("4_name", 24.7, 1.3),
    ("5_only", 26.4, 1.3),
]


def voice_windows():
    """When each line is actually speaking (the takes have silence at the ends)."""
    spans = []
    for name, at, _ in VOICE_LINES:
        d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                           "-of", "csv=p=0", str(VOICE / f"{name}.wav")]))
        spans.append((at, at + d))
    return spans


def sound():
    """The advert's world (strings, fire, wine), then the sitcoms' own sound under a laugh track, then the sting.
    The bed sits low all the way and only dips a little (to 0.75, soft 0.3 s ramps) under the narration."""
    inputs, chains = [], []

    def add(path, chain):
        inputs.append(path)
        chains.append(chain.replace("[IN]", f"[{len(inputs) - 1}:a]"))

    def bed(path, src_at, start, end, vol, fade_in=0.3, fade_out=0.3):
        add(path, f"[IN]atrim={src_at}:{src_at + end - start},asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,"
                  f"afade=t=in:d={fade_in},afade=t=out:st={end - start - fade_out}:d={fade_out},volume={vol},"
                  f"adelay={round(start * 1000)}:all=1")

    def hit(path, at, vol, src_at=0.0, length=None):
        trim = f"atrim={src_at}:{src_at + length}," if length else (f"atrim={src_at}," if src_at else "")
        add(path, f"[IN]{trim}asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,volume={vol},adelay={round(at * 1000)}:all=1")

    # the advert
    add(MUSIC, f"[IN]aformat=channel_layouts=stereo,volume='if(between(t,{SCRATCH},{LOGO - 1.4}),0,0.6-0.18*clip((t-{LOGO + 0.3})/0.8\\,0\\,1))':eval=frame")   # the sting eases down under the names
    bed(SFX["fireplace"], 1.0, 0.0, SCRATCH, 0.5, 1.0, 0.02)
    bed(SFX["restaurant"], 3.0, 4.2, SCRATCH, 0.18, 1.2, 0.02)
    hit(SFX["cheese_cut"], 0.3, 0.7, 2.4, 2.2)
    hit(SFX["wine_pour"], 2.6, 0.35, 0.5, 2.5)
    hit(SFX["clink"], 8.3, 0.4)
    hit(SFX["record_scratch"], SCRATCH - 0.12, 1.1, 0.6)
    # the laugh track
    bed(SFX["laugh_claps"], 0.3, SCRATCH + 0.1, SCRATCH + 1.6, 1.0, 0.02, 0.6)   # the explosion, out before Baby speaks
    for start, length, src, at, word, word_at, *audio_at in MONTAGE:
        bed(src, (audio_at or [at])[0], start, start + length, 1.0, 0.02, 0.04)
        if word:
            hit(SFX["stamp"], start + word_at, 0.9, *STAMP_HIT)
    for name, src_at, start, end, vol in LAUGHS:
        bed(SFX[name], src_at, start, end, vol, 0.03, 0.25)
    bed(SFX["laugh_2"], 0.75, STROBE[0] - 0.1, LOGO + 0.2, 0.75, 0.3, 0.5)   # the big one, under the strobe
    # the finale
    hit(SFX["harp"], LOGO - 0.1, 0.45, 0.0, 3.0)
    bed(SFX["cheer_clap"], 1.0, 28.0, LENGTH, 0.55, 0.4, 0.5)   # the audience answers the name
    world = len(chains)
    for name, at, vol in VOICE_LINES:
        inputs.append(VOICE / f"{name}.wav")
        chains.append(f"[{len(inputs) - 1}:a]loudnorm=I=-16:TP=-2,aresample=48000,aecho=0.8:0.5:50:0.12,volume={vol},"
                      f"aformat=channel_layouts=stereo,adelay={round(at * 1000)}:all=1")
    labels = [f"[x{i}]" for i in range(len(chains))]
    graph = [c + l for c, l in zip(chains, labels)]
    r = 0.3
    under = "+".join(f"clip(min((t-{a:.2f})/{r}\\,({b:.2f}-t)/{r})\\,0\\,1)" for a, b in voice_windows())
    graph.append("".join(labels[:world]) + f"amix=inputs={world}:normalize=0:duration=longest,"
                 f"volume='1-0.25*min(1\\,{under})':eval=frame[world]")
    graph.append("".join(labels[world:]) + f"amix=inputs={len(labels) - world}:normalize=0:duration=longest,volume=1.5[voice]")
    graph.append(f"[world][voice]amix=inputs=2:normalize=0:duration=first,atrim=0:{LENGTH},"
                 "afade=t=out:st=29.5:d=0.5,alimiter=limit=0.7:level=false[pre]")
    raw = WORK / "mix_raw.wav"
    args = []
    for p in inputs:
        args += ["-i", p]
    run("ffmpeg", "-v", "error", "-y", *args, "-filter_complex", ";".join(graph), "-map", "[pre]", "-ar", 48000, "-ac", 2, raw)
    probe = subprocess.run(["ffmpeg", "-i", str(raw), "-af", "loudnorm=I=-16:TP=-2:LRA=15:print_format=json",
                            "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(probe[probe.rindex("{"):probe.rindex("}") + 1])
    final = WORK / "mix.wav"
    run("ffmpeg", "-v", "error", "-y", "-i", raw, "-af",
        f"loudnorm=I=-16:TP=-2:LRA=15:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
        f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,alimiter=limit=0.79:level=false",
        "-ar", 48000, final)
    return final


STAMP_HIT = (0.15, 0.45)   # which part of the stamp recording is the thump
# After each stamp, a burst that is gone before the next line: (name, src in point, start, end, volume)
LAUGHS = [("laugh_1", 0.0, 13.75, 14.45, 0.45), ("laugh_5", 0.74, 15.95, 16.65, 0.5), ("crowd_laugh", 0.44, 17.65, 18.25, 0.5)]


def main():
    WORK.mkdir(exist_ok=True)
    pic = picture()
    t = titles()
    comp = WORK / "comp.mp4"
    run("ffmpeg", "-v", "error", "-y", "-i", pic, "-framerate", FPS, "-i", t / "%04d.png", "-filter_complex",
        "[0:v][1:v]overlay=0:0:format=auto,noise=alls=5:allf=t,vignette=PI/5,format=yuv420p[v]", "-map", "[v]",
        "-c:v", "libx264", "-crf", 14, "-preset", "fast", comp)
    mix = sound()
    out = HERE / "prime_cheese_promo.mp4"
    run("ffmpeg", "-v", "error", "-y", "-i", comp, "-i", mix, "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
        "-crf", 16, "-preset", "slow", "-c:a", "aac", "-b:a", "192k", "-t", LENGTH, "-movflags", "+faststart", out)
    print(out)


if __name__ == "__main__":
    main()
