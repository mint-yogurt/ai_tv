"""CEREAL KILLERS: a 30 s block promo for ChupTV. Rebuilds cereal_killers_promo.mp4 from the sources.

Picture: 640x480 (4:3), 30 fps. Every bar of the score (120 bpm, 2 s) is a cut.
Run from ~/ai_tv with the venv:  .venv/bin/python test_outputs/video/2026-11-15_cereal_killers_promo/build.py
"""
import math, random, shutil, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
WORK = HERE / ".work"  # hidden: the Tests page skips dot folders
FONT = str(HERE.parents[2] / "library" / "fonts" / "Creepster-Regular.ttf")  # the font archive
W, H, FPS, LENGTH = 640, 480, 30, 30.0
TV = Path("/mnt/hdd_1tb/tv")
GOOSE = TV / "Goosebumps/Goosebumps 1995 Season 1 Complete UPDATED DVDRip x264 [i_c]/Goosebumps S01E03 The Cuckoo Clock of Doom.mkv"
CRYPT = TV / "Tales from the Cryptkeeper/Tales from the Cryptkeeper S01 (1993- 360p re-dvdrip)/Tales From the Cryptkeeper S01E02 Nature.mp4"
PINGU = TV / "Pingu/Season 1/Pingu.S01E01.iNTERNAL.DVDRip.XviD-aAF/aaf-pingu.s01e01.pingu.is.introduced.dvdrip.xvid.avi"
TOXIC = TV / "The Toxic Crusaders (1991)/Season 01/The Toxic Crusaders - 1x02 - This Spud's for You.mkv"
PEE = TV / "Pee Wee's Playhouse/Pee-Wee Vol 1 Disc 1 E01 Ice Cream Soup.mp4"
SHARK = TV / "STREET SHARKS (1994-1995)/Season 1 (1994)/STREET SHARKS - S01 E02 - Sharkbite.mp4"
BW = TV / "Beast Wars/Season 1/Beast Wars S01E02 Beast Wars (Part 2).mkv"
RGB = TV / "Real Ghostbusters/The Real Ghostbusters/Season 01/The Real Ghostbusters - S01E02 - Killerwatt SDTV.mp4"
REBOOT = TV / "ReBoot/Season 1/S1 E10 - The Great Brain.mp4"
PR = TV / "Mighty Morphin Power Rangers/Power Rangers - S01E01 - Day of the Dumpster (480p x265 EDGE2020).mkv"
MARIO = TV / "Super Mario Bros. Super Show/Season 1/01 - The Bird! The Bird! & Neatness Counts.avi"
KIGE = Path("/mnt/media/videos/kige_ramsey/Breakfast Cereal.mp4")
AI = HERE / "src" / "ai_clip.mp4"
MUSIC = HERE / "music" / "cereal_killers.wav"

COLD = "eq=saturation=0.75:brightness=-0.03,colorbalance=bs=0.12:bm=0.06:rs=-0.05"
LINEUP = "eq=saturation=0.85:contrast=1.15"
HOT = "eq=saturation=1.35:contrast=1.08"
SLIME = "eq=saturation=1.4:brightness=-0.06"

# (start on the timeline, length, source, in point, how, grade). "freeze" holds the in-point frame with a push-in.
SHOTS = [
    # Act 1, the crime scene (bars 1-4)
    (0.0, 4.0, AI, 0.0, "play", COLD),
    (4.0, 2.0, GOOSE, 24.4, "play", COLD),          # the dog's eyes light up
    (6.0, 2.0, CRYPT, 58.3, "play", COLD),          # the TV (the cackle is laid from 59.8, in sound())
    # Act 2, the lineup (bars 5-7), a suspect every second
    (8.0, 1.0, PINGU, 82.6, "freeze", LINEUP),
    (9.0, 1.0, TOXIC, 19.4, "freeze", LINEUP),
    (10.0, 1.0, PEE, 37.9, "freeze", LINEUP),
    (11.0, 1.0, SHARK, 7.9, "freeze", LINEUP),
    (12.0, 1.0, CRYPT, 52.2, "freeze", LINEUP),
    (13.0, 1.0, BW, 10.35, "freeze", LINEUP),
    # bar 8: the mastermind
    (14.0, 2.0, KIGE, 43.3, "play", LINEUP),
    # Act 3, the rock montage (bars 9-13)
    (16.0, 1.0, SHARK, 52.0, "play", HOT),          # THEY TRAVEL IN PACKS
    (17.0, 1.0, RGB, 43.2, "play", HOT),
    (18.0, 0.5, RGB, 49.3, "play", HOT),
    (18.5, 0.5, BW, 34.3, "play", HOT),
    (19.0, 0.5, SHARK, 31.2, "play", HOT),
    (19.5, 0.5, TOXIC, 37.2, "play", HOT),
    (20.0, 1.0, TOXIC, 13.1, "play", HOT),          # THEY FIGHT DIRTY
    (21.0, 0.5, SHARK, 37.3, "play", HOT),
    (21.5, 0.5, CRYPT, 19.3, "play", HOT),
    (22.0, 0.5, REBOOT, 40.3, "play", HOT),         # THEY ALWAYS COME BACK FOR SECONDS
    (22.5, 0.5, PR, 17.6, "play", HOT),
    (23.0, 0.5, MARIO, 46.6, "play", HOT),
    (23.5, 0.5, RGB, 40.3, "play", HOT),
    (24.0, 0.25, REBOOT, 26.0, "play", HOT),        # the tom fill: a cut every eighth note
    (24.25, 0.25, PEE, 69.5, "play", HOT),
    (24.5, 0.25, BW, 52.3, "play", HOT),
    (24.75, 0.25, PINGU, 82.0, "play", HOT),
    (25.0, 0.25, TOXIC, 34.3, "play", HOT),
    (25.25, 0.25, RGB, 37.4, "play", HOT),
    (25.5, 0.25, SHARK, 7.7, "play", HOT),
    (25.75, 0.25, BW, 10.3, "play", HOT),
    # bars 14-15: the logo over the Goosebumps slime
    (26.0, 4.0, GOOSE, 44.6, "play", SLIME),
]

# Words on screen: (start, end, text, style)
CARDS = [
    (0.7, 3.85, "THE BOWL WAS EMPTY.", "card"),
    (4.15, 5.9, "THE ONLY WITNESS\nISN'T TALKING.", "card"),
    (6.15, 7.9, "AND THE TV\nWAS STILL ON.", "card"),
    (8.0, 14.0, "THE USUAL SUSPECTS", "header"),
    (14.25, 15.95, "THE MASTERMIND.", "card_top"),
    (16.05, 17.95, "THEY TRAVEL\nIN PACKS.", "card"),
    (20.05, 21.95, "THEY FIGHT DIRTY.", "card"),
    (22.05, 23.95, "THEY ALWAYS COME\nBACK FOR SECONDS.", "card"),
    (26.0, 30.0, "CEREAL\nKILLERS", "logo"),
    (28.35, 30.0, "ONLY ON CHUPTV", "tag"),
]
FLASHES = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 26.0]
SUSPECT_NO = {8.0: 1, 9.0: 2, 10.0: 3, 11.0: 4, 12.0: 5, 13.0: 6}

SLIME_GREEN, CANDY, INK = (166, 242, 58), (255, 79, 163), (34, 8, 46)


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def cut(i, shot):
    start, length, src, at, how, grade = shot
    out = WORK / f"shot_{i:02d}.mp4"
    frames = round((start + length) * FPS) - round(start * FPS)  # from the boundaries, so 7.5-frame shots don't drift
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    if how == "freeze":
        still = WORK / f"still_{i:02d}.png"
        run("ffmpeg", "-v", "error", "-y", "-ss", at, "-i", src, "-frames:v", 1, "-vf", fit, still)
        vf = (f"scale={W*2}:{H*2},zoompan=z='1+0.10*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={W}x{H}:fps={FPS},{grade},format=yuv420p")
        run("ffmpeg", "-v", "error", "-y", "-loop", 1, "-i", still, "-vf", vf, "-frames:v", frames,
            "-c:v", "libx264", "-crf", 14, "-preset", "fast", out)
    else:
        run("ffmpeg", "-v", "error", "-y", "-ss", at, "-i", src, "-t", length, "-an",
            "-vf", f"{fit},fps={FPS},{grade},format=yuv420p", "-frames:v", frames,
            "-c:v", "libx264", "-crf", 14, "-preset", "fast", out)
    return out


def font(size):
    return ImageFont.truetype(FONT, size)


def draw_text(layer, text, size, centre, fill, stroke=5, shadow=6, scale=1.0, alpha=1.0, spacing=4):
    """Text centred on `centre`, drawn at `scale` (for pop-ins), with an ink outline and a drop shadow."""
    f = font(max(8, round(size * scale)))
    d = ImageDraw.Draw(layer)
    box = d.multiline_textbbox((0, 0), text, font=f, align="center", spacing=spacing, stroke_width=stroke)
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = centre[0] - w / 2 - box[0], centre[1] - h / 2 - box[1]
    tmp = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)
    td.multiline_text((x + shadow, y + shadow), text, font=f, fill=(0, 0, 0, 170), align="center",
                      spacing=spacing, stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
    td.multiline_text((x, y), text, font=f, fill=fill + (255,), align="center", spacing=spacing,
                      stroke_width=stroke, stroke_fill=INK + (255,))
    if alpha < 1:
        tmp.putalpha(tmp.getchannel("A").point(lambda a: round(a * alpha)))
    layer.alpha_composite(tmp)


def lineup_chart(layer, t):
    """A police height chart over the suspects, and a numbered placard."""
    d = ImageDraw.Draw(layer)
    small = font(18)
    for k, y in enumerate(range(70, H - 20, 46)):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 70), width=2)
        d.text((10, y - 22), f"{7 - k * 0.5:g}'" if k % 2 == 0 else "", font=small, fill=(255, 255, 255, 150))
    start = max(s for s in SUSPECT_NO if s <= t)
    no = SUSPECT_NO[start]
    plate = (W // 2 - 70, H - 78, W // 2 + 70, H - 26)
    d.rectangle(plate, fill=(12, 12, 12, 230), outline=(240, 240, 240, 255), width=3)
    draw_text(layer, f"SUSPECT {no}", 30, ((plate[0] + plate[2]) / 2, (plate[1] + plate[3]) / 2 + 2),
              (245, 245, 245), stroke=0, shadow=0)


def text_frame(t, rng):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for f in FLASHES:  # a camera flash on each cut: under the words, capped so the picture shows through
        if 0 <= t - f < 4 / FPS:
            a = round(200 * (1 - (t - f) * FPS / 4))
            layer.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, a)))
    if 8.0 <= t < 14.0:
        lineup_chart(layer, t)
    for start, end, text, style in CARDS:
        if not start <= t < end:
            continue
        age, left = t - start, end - t
        pop = min(1.0, age / 0.12)
        scale = 1.25 - 0.25 * pop
        alpha = min(pop, 1.0, left / 0.1) if style != "logo" else 1.0
        jx, jy = rng.choice((-1, 0, 1)), rng.choice((-1, 0, 1))  # boiling cartoon line
        if style == "card":
            draw_text(layer, text, 50, (W / 2 + jx, H - 120 + jy), SLIME_GREEN, scale=scale, alpha=alpha)
        elif style == "card_top":
            draw_text(layer, text, 56, (W / 2 + jx, 70 + jy), SLIME_GREEN, scale=scale, alpha=alpha)
        elif style == "header":
            draw_text(layer, text, 46, (W / 2 + jx, 38 + jy), (245, 245, 245), stroke=4, shadow=4)
        elif style == "logo":
            slam = min(1.0, age / 0.15)
            s = 2.2 - 1.2 * (1 - (1 - slam) ** 2)
            shake = 10 * max(0.0, 1 - age / 0.5)
            sx, sy = rng.uniform(-shake, shake), rng.uniform(-shake, shake)
            cereal, killers = text.split("\n")
            logo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw_text(logo, cereal, 150, (W / 2 + jx, 140 + jy), CANDY, stroke=8, shadow=9, scale=s)
            draw_text(logo, killers, 164, (W / 2 - jx, 278 + jy), SLIME_GREEN, stroke=8, shadow=9, scale=s)
            logo = logo.rotate(5 * (1 - slam) - 4, resample=Image.BICUBIC, center=(W / 2, 210))
            layer.alpha_composite(logo, (round(sx), round(sy)))
        elif style == "tag":
            bounce = 1 + 0.35 * math.exp(-age * 9) * math.cos(age * 30)
            draw_text(layer, text, 44, (W / 2, 420), (255, 255, 255), stroke=5, shadow=5, scale=bounce)
    return layer


def titles():
    out = WORK / "titles"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    rng = random.Random(7)
    for i in range(round(LENGTH * FPS)):
        text_frame(i / FPS, rng).save(out / f"{i:04d}.png")
    return out


def sound():
    """Score plus two lifted sounds: the TV's cackle and Kige's verdict.
    The score ducks under the cackle and the voice. Limited, then finished at -16 LUFS (true peak -2 before AAC)."""
    graph = [
        f"[0:a]atrim=0:{LENGTH},asetpts=PTS-STARTPTS,"
        "volume='if(between(t,6.0,8.0),0.45,if(between(t,26.3,28.9),0.4,1))':eval=frame[mus]",
        "[1:a]atrim=59.8:61.8,asetpts=PTS-STARTPTS,volume=1.6,adelay=6000|6000[cackle]",
        "[2:a]atrim=29.05:31.45,asetpts=PTS-STARTPTS,highpass=f=90,volume=2.4,adelay=26370|26370[kige]",
        f"[mus][cackle][kige]amix=inputs=3:normalize=0:duration=first,"
        f"afade=t=out:st={LENGTH - 0.6}:d=0.6,alimiter=limit=0.7:level=false[mix]",
    ]
    raw = WORK / "mix_raw.wav"
    run("ffmpeg", "-v", "error", "-y", "-i", MUSIC, "-i", CRYPT, "-i", KIGE, "-filter_complex", ";".join(graph),
        "-map", "[mix]", "-ar", 48000, "-ac", 2, "-t", LENGTH, raw)
    probe = subprocess.run(["ffmpeg", "-i", str(raw), "-af", "loudnorm=I=-16:TP=-2:LRA=11:print_format=json",
                            "-f", "null", "-"], capture_output=True, text=True).stderr
    import json
    m = json.loads(probe[probe.rindex("{"):probe.rindex("}") + 1])
    final = WORK / "mix.wav"
    run("ffmpeg", "-v", "error", "-y", "-i", raw, "-af",
        f"loudnorm=I=-16:TP=-2:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
        f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true",
        "-ar", 48000, final)
    return final


def main():
    WORK.mkdir(exist_ok=True)
    shots = [cut(i, s) for i, s in enumerate(SHOTS)]
    (WORK / "shots.txt").write_text("".join(f"file '{p.name}'\n" for p in shots))
    picture = WORK / "picture.mp4"
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", 0, "-i", WORK / "shots.txt", "-c", "copy", picture)
    t = titles()
    mix = sound()
    out = HERE / "cereal_killers_promo.mp4"
    run("ffmpeg", "-v", "error", "-y", "-i", picture, "-framerate", FPS, "-i", t / "%04d.png", "-i", mix,
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-crf", 16, "-preset", "slow", "-c:a", "aac", "-b:a", "192k", "-t", LENGTH,
        "-movflags", "+faststart", out)
    print(out)


if __name__ == "__main__":
    main()
