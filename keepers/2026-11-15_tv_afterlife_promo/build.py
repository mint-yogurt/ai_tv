"""TV AFTERLIFE: a 30 s block promo for ChupTV. Rebuilds tv_afterlife_promo.mp4 from the sources.

Picture: 640x480 (4:3), 30 fps. No beat grid this time: it drifts, then strobes, then stops dead.
Run from ~/ai_tv with the venv:  .venv/bin/python test_outputs/video/2026-11-15_tv_afterlife_promo/build.py
"""
import json, math, random, shutil, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
WORK = HERE / ".work"  # hidden: the Tests page skips dot folders
FONT = str(HERE.parents[2] / "library" / "fonts" / "IMFeENsc28P.ttf")  # the font archive
W, H, FPS, LENGTH = 640, 480, 30, 30.0
OFF = 29.3  # the TV switches off
TV = Path("/mnt/hdd_1tb/tv")
EVA = TV / "Neon Genesis Evangelion (1995 - 1996) Complete Series/Neon Genesis Evangelion S01E04 Hedgehog's Dilemma.mkv"
SG = TV / "Space Ghost Coast To Coast S01-S08 (1994-)/Season 1/SGCtC S01E01 Spanish Translation.mp4"
LTV = TV / "Liquid Television/Liquid Television - 101 [OVERKiLL].avi"
WHR = TV / ("Witch Hunter Robin (DVDRip 720x480p x265 HEVC AC3x2 2.0x2)(Dual Audio)[sxales]/"
            "Witch Hunter Robin - 01 (DVDRip 720x480p x265 HEVC AC3x2 2.0x2)(Dual Audio)[sxales].mkv")
AI = HERE / "src" / "ai_clip.mp4"
SD = HERE / "sd" / "fall_into_tv.mp4"
MUSIC = HERE / "music" / "tv_afterlife.wav"
SFX = {k: v["path"] for k, v in json.loads((HERE / "src" / "sfx.json").read_text()).items()}
VOICE = HERE / "voice"

NIGHT = "eq=saturation=0.8:contrast=1.1:brightness=-0.02"
DREAM = "eq=saturation=1.15:contrast=1.15:gamma=0.9"
COLD = "eq=saturation=0.6:contrast=1.2,colorbalance=bs=0.15:bm=0.08"

# (start, length, source, in point, speed, grade). speed < 1 is slow motion (frames are held, not invented).
SHOTS = [
    (0.0, 4.0, AI, 0.0, 1.0, NIGHT),          # the TV in the woods
    (4.0, 2.5, EVA, 935.6, 0.5, DREAM),       # power pole against the sky
    (6.5, 3.0, SG, 63.7, 0.6, COLD),          # an empty desk; Space Ghost fades in, see-through
    (9.5, 2.0, EVA, 302.0, 0.5, COLD),        # the empty train car, one passenger
    (11.5, 2.0, LTV, 806.0, 0.5, DREAM),      # a floating head on cyan
    (13.5, 2.0, EVA, 636.3, 0.5, DREAM),      # a silhouette in the pampas grass at dusk
]
# The strobe, 15.5-20.0: a few frames of each, cycling, every other burst blown out or negative.
STROBE_POOL = [(EVA, 401.0), (SG, 92.3), (LTV, 285.7), (LTV, 26.0), (WHR, 27.6), (SG, 97.9), (EVA, 937.0),
               (LTV, 858.8), (EVA, 302.5), (SG, 66.2)]
STROBE_START, STROBE_END = 15.5, 20.0
AFTER = [
    (20.0, 1.06, AI, 4.0, 1.0, NIGHT),        # back in the woods, dead quiet, and the camera rushes into the screen
]
# Into the screen (centre ~338,275 in the 4:3 frame), accelerating, to land on the static the SD loop starts from.
PUSH_IN = (",scale=1280:960,zoompan=z='1+2.2*pow(on/31\\,2.5)':x='max(0\\,min(iw-iw/zoom\\,676-iw/zoom/2))'"
           ":y='max(0\\,min(ih-ih/zoom\\,550-ih/zoom/2))':d=1:s=640x480:fps=30")
SD_START = 21.06                              # the camera falls into the static (local SD loop), stretched to OFF

CARDS = [
    (1.2, 3.95, "Every show you\never turned off", "line"),
    (6.8, 9.4, "is still playing.", "line"),
    (21.4, 23.6, "Somewhere.", "line"),
    (24.0, OFF, "TV AFTERLIFE", "logo"),
    (27.0, OFF, "only on chuptv", "tag"),
]
BONE = (238, 230, 212)


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def frames_between(a, b):
    return round(b * FPS) - round(a * FPS)


def clip(out, src, at, frames, speed=1.0, grade="null", extra=""):
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    vf = f"{fit},fps={FPS},setpts=PTS/{speed},fps={FPS},{grade}{extra},format=yuv420p"
    run("ffmpeg", "-v", "error", "-y", "-ss", at, "-i", src, "-an", "-vf", vf, "-frames:v", frames,
        "-c:v", "libx264", "-crf", 14, "-preset", "fast", out)
    return out


def picture():
    parts = []
    for i, (start, length, src, at, speed, grade) in enumerate(SHOTS):
        parts.append(clip(WORK / f"a{i:02d}.mp4", src, at, frames_between(start, start + length), speed, grade))
    rng = random.Random(23)
    t, k = STROBE_START, 0
    while t < STROBE_END - 1e-6:
        n = rng.choice((2, 3, 3, 4)) if t < 18.5 else rng.choice((1, 2, 2))  # it speeds up
        n = min(n, frames_between(t, STROBE_END))
        src, at = STROBE_POOL[k % len(STROBE_POOL)]
        look = (",negate" if k % 5 == 3 else ",eq=brightness=0.35:contrast=1.6" if k % 2 == 0 else ",eq=brightness=-0.25")
        parts.append(clip(WORK / f"s{k:03d}.mp4", src, at + rng.uniform(0, 1.5), n, 1.0, DREAM, look))
        t = round(t * FPS + n) / FPS
        k += 1
    for i, (start, length, src, at, speed, grade) in enumerate(AFTER):
        parts.append(clip(WORK / f"b{i:02d}.mp4", src, at, frames_between(start, start + length), speed, grade, PUSH_IN))
    sd_frames = frames_between(SD_START, LENGTH)
    sd_len = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                            "-of", "csv=p=0", str(SD)]))
    parts.append(clip(WORK / "c00.mp4", SD, 0, sd_frames, sd_len / (OFF - SD_START), DREAM))
    (WORK / "parts.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = WORK / "picture.mp4"
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", 0, "-i", WORK / "parts.txt", "-c", "copy", out)
    return out


def tracked(draw, xy, text, font, fill, tracking):
    """Text with extra letter spacing, left edge at xy."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def tracked_width(text, font, tracking):
    return sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1)


def centred(layer, text, size, cy, alpha, tracking=2, glow=True):
    f = ImageFont.truetype(FONT, size)
    w = tracked_width(text, f, tracking)
    while w > W - 64:  # never wider than the frame, less a margin
        size -= 2
        f = ImageFont.truetype(FONT, size)
        w = tracked_width(text, f, tracking)
    x = (W - w) / 2
    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    tracked(ImageDraw.Draw(ink), (x, cy - size * 0.6), text, f, BONE + (255,), tracking)
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    tracked(ImageDraw.Draw(shade), (x + 2, cy - size * 0.6 + 3), text, f, (0, 0, 0, 230), tracking)
    shade = shade.filter(ImageFilter.GaussianBlur(4))
    out = Image.alpha_composite(shade, ink)
    if glow:
        halo = ink.filter(ImageFilter.GaussianBlur(9))
        halo.putalpha(halo.getchannel("A").point(lambda a: a // 2))
        out = Image.alpha_composite(halo, out)
    out.putalpha(out.getchannel("A").point(lambda a: round(a * alpha)))
    layer.alpha_composite(out)


def text_frame(t, rng):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for start, end, text, style in CARDS:
        if not start <= t < end:
            continue
        fade = min(1.0, (t - start) / 0.45, (end - t) / 0.3) if end < OFF else min(1.0, (t - start) / 0.9)
        if style == "line":
            rows = text.split("\n")
            for j, row in enumerate(rows):
                centred(layer, row, 48, H - 96 - 50 * (len(rows) - 1 - j), fade)
        elif style == "logo":
            flicker = 1.0 if rng.random() > 0.08 else rng.uniform(0.35, 0.8)   # a bad bulb
            centred(layer, text, 62, 196, fade * flicker, tracking=5)
        elif style == "tag":
            centred(layer, text, 36, 270, fade, tracking=6, glow=False)
    return layer


def titles():
    out = WORK / "titles"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    rng = random.Random(5)
    for i in range(round(OFF * FPS)):
        text_frame(i / FPS, rng).save(out / f"{i:04d}.png")
    return out


def switch_off(last_frame, out_dir):
    """The old-set power-off: the picture collapses to a bright line, the line to a dot, the dot fades."""
    src = Image.open(last_frame).convert("RGB")
    n = frames_between(OFF, LENGTH)
    for i in range(n):
        p = i / (n - 1)
        frame = Image.new("RGB", (W, H), (0, 0, 0))
        if p < 0.4:                                    # squash to a line, getting brighter
            q = p / 0.4
            h = max(2, round(H * (1 - q) ** 3))
            band = src.resize((W, h)).point(lambda v: min(255, round(v + 200 * q)))
            frame.paste(band, (0, (H - h) // 2))
        elif p < 0.7:                                  # the line shrinks to a dot
            q = (p - 0.4) / 0.3
            w = max(4, round(W * (1 - q) ** 2))
            ImageDraw.Draw(frame).rectangle(((W - w) // 2, H // 2 - 1, (W + w) // 2, H // 2 + 1), fill=(255, 255, 255))
        else:                                          # the dot dies
            q = (p - 0.7) / 0.3
            v = round(255 * (1 - q))
            dot = Image.new("RGB", (W, H), (0, 0, 0))
            ImageDraw.Draw(dot).ellipse((W // 2 - 4, H // 2 - 4, W // 2 + 4, H // 2 + 4), fill=(v, v, v))
            frame = dot.filter(ImageFilter.GaussianBlur(1.5))
        frame.save(out_dir / f"{i:03d}.png")


def sound():
    """Field recordings, effects and voice, all placed by hand; the score is only a bed and a sting.
    Everything cuts dead at 20.0 (the strobe stops) and at OFF (the set switches off)."""
    inputs, chains = [], []

    def add(path, chain):
        inputs.append(path)
        chains.append(chain.replace("[IN]", f"[{len(inputs) - 1}:a]"))

    def bed(path, src_at, start, end, vol, fade_in=0.4, fade_out=0.4):
        add(path, f"[IN]atrim={src_at}:{src_at + end - start},asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,"
                  f"afade=t=in:d={fade_in},afade=t=out:st={end - start - fade_out}:d={fade_out},volume={vol},"
                  f"adelay={round(start * 1000)}:all=1")

    add(MUSIC, "[IN]aformat=channel_layouts=stereo,volume=0.9")
    bed(SFX["pine_wind"], 5.0, 0.0, 20.0, 0.7, 1.5, 0.01)
    bed(SFX["pine_wind"], 30.0, 20.0, OFF, 0.6, 0.6, 0.01)
    bed(SFX["crickets_owl"], 10.0, 0.0, 15.5, 0.8, 1.0, 1.0)
    bed(SFX["crickets_owl"], 40.0, 20.15, OFF, 0.8, 0.8, 0.01)
    bed(SFX["tv_static"], 3.0, 0.0, 4.0, 0.22, 1.0, 0.3)
    bed(SFX["tv_static"], 10.0, 20.0, 21.06, 0.2, 0.05, 0.3)
    bed(SFX["powerlines"], 8.0, 3.8, 6.8, 1.6, 0.3, 0.5)
    bed(SFX["hotel_room_tone"], 30.0, 6.3, 15.6, 2.5, 0.6, 0.4)
    bed(SFX["distant_train"], 8.0, 9.3, 12.0, 1.4, 0.5, 0.8)
    bed(SFX["fluorescent"], 3.0, 11.3, 15.5, 6.0, 1.0, 0.05)
    add(SFX["jacobs_ladder"], f"[IN]atrim=0:{STROBE_END - STROBE_START},asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,"
                              f"afade=t=in:d=2.5:curve=exp,volume=0.8,adelay={round(STROBE_START * 1000)}:all=1")
    bed(SFX["bulb_flicker"], 4.8, 16.2, 17.8, 0.9, 0.01, 0.1)
    bed(SFX["bulb_flicker"], 11.8, 18.6, 19.6, 0.9, 0.01, 0.1)
    bed(SFX["tv_static"], 20.0, 17.0, 20.0, 0.5, 2.0, 0.01)
    add(SFX["reverse_glass"], f"[IN]aformat=channel_layouts=stereo,volume=0.55,adelay={round((24.0 - 3.15) * 1000)}:all=1")
    crt = len(chains)
    add(SFX["crt_off"], f"[IN]atrim=1.45:3.6,asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,afade=t=out:st=1.6:d=0.5,"
                        f"volume=1.3,adelay={round(OFF * 1000)}:all=1")
    world = len(chains)  # everything so far is the world; the voices get their own bus
    # Each take is levelled first (the TTS comes out anywhere from -17 to -29 dB), then lowered a touch and
    # given a room. The station name stays nearly dry: an echo turns "TV" into a stammer.
    for name, at, vol, echo in (("1_every_show", 1.3, 1.0, "180|420:0.35|0.22"), ("2_still_playing", 7.0, 1.0, "40:0.08"),
                                ("3_somewhere", 21.5, 1.1, "140:0.22"), ("4_name", 25.8, 1.1, "60:0.15")):
        inputs.append(VOICE / f"{name}.wav")
        chains.append(f"[{len(inputs) - 1}:a]loudnorm=I=-16:TP=-2,aresample=24000,asetrate=22080,aresample=48000,aecho=0.8:0.6:{echo},volume={vol},"
                      f"aformat=channel_layouts=stereo,adelay={round(at * 1000)}:all=1")
    inputs.append(VOICE / "5_backwards.wav")
    chains.append(f"[{len(inputs) - 1}:a]areverse,asetrate=21000,aresample=48000,aecho=0.8:0.7:300|700:0.4|0.3,"
                  "volume=0.8,aformat=channel_layouts=stereo,adelay=23000:all=1")
    labels = [f"[x{i}]" for i in range(len(chains))]
    graph = [c + l for c, l in zip(chains, labels)]
    click = labels[crt]
    worlds = [l for i, l in enumerate(labels[:world]) if i != crt]
    voices = labels[world:]
    duck = "+".join(f"between(t,{a},{b})" for a, b in ((1.3, 3.6), (7.0, 8.8), (21.5, 23.0), (25.8, 27.9)))
    # The world ducks under the voice. Two dead stops: half a second of nothing after the strobe, and after
    # OFF nothing but the set's own click.
    graph.append("".join(worlds) + f"amix=inputs={len(worlds)}:normalize=0:duration=longest,"
                 f"volume='if(between(t,{STROBE_END},{STROBE_END + 0.5})+gte(t,{OFF}),0,if({duck},0.3,1))':eval=frame[world]")
    graph.append("".join(voices) + f"amix=inputs={len(voices)}:normalize=0:duration=longest,"
                 f"volume='if(gte(t,{OFF}),0,1.6)':eval=frame[voice]")
    graph.append(f"[world][voice]{click}amix=inputs=3:normalize=0:duration=first,atrim=0:{LENGTH},"
                 "alimiter=limit=0.7:level=false[pre]")
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
        f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true",
        "-ar", 48000, final)
    return final


def main():
    WORK.mkdir(exist_ok=True)
    pic = picture()
    t = titles()
    comp = WORK / "comp.mp4"
    run("ffmpeg", "-v", "error", "-y", "-i", pic, "-framerate", FPS, "-i", t / "%04d.png", "-filter_complex",
        f"[0:v]trim=0:{OFF},setpts=PTS-STARTPTS[p];[p][1:v]overlay=0:0:format=auto:eof_action=pass,"
        "noise=alls=7:allf=t,vignette=PI/4.5,format=yuv420p[v]", "-map", "[v]",
        "-c:v", "libx264", "-crf", 14, "-preset", "fast", comp)
    off = WORK / "off"
    shutil.rmtree(off, ignore_errors=True)
    off.mkdir()
    last = WORK / "last.png"
    run("ffmpeg", "-v", "error", "-y", "-sseof", "-0.04", "-i", comp, "-frames:v", 1, last)
    switch_off(last, off)
    run("ffmpeg", "-v", "error", "-y", "-framerate", FPS, "-i", off / "%03d.png", "-c:v", "libx264", "-crf", 14,
        "-pix_fmt", "yuv420p", WORK / "off.mp4")
    (WORK / "final.txt").write_text("file 'comp.mp4'\nfile 'off.mp4'\n")
    joined = WORK / "joined.mp4"
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", 0, "-i", WORK / "final.txt", "-c", "copy", joined)
    mix = sound()
    out = HERE / "tv_afterlife_promo.mp4"
    run("ffmpeg", "-v", "error", "-y", "-i", joined, "-i", mix, "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
        "-crf", 16, "-preset", "slow", "-c:a", "aac", "-b:a", "192k", "-t", LENGTH, "-movflags", "+faststart", out)
    print(out)


if __name__ == "__main__":
    main()
