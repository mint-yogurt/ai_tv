"""Production 4 by hand: the pipeline's outline + screenplay (DeepSeek), then shots as the pipeline would
run them with the Tencent image-to-image step scripted in:
  actor shots:    hy-image-v3.5-preview places the reference actors on a set -> HeyGen Video 1 first_frame
  no-actor shots: local SD still -> mischief-1
Then MIDIgen for the two music cues, Poots for Dr. Schweger's lines, and a black-and-white stitch.

    python run.py frames | videos | music | assemble     (each step is cached in manifest.json)
"""
import base64, json, subprocess, sys, time
from pathlib import Path
from aitv import comfy, config, db, pipeline, titles, videogen

HERE = Path(__file__).resolve().parent
PROD = HERE.parent
MANIFEST = HERE / "manifest.json"
cfg = config.load()
STORE = cfg.path("library") / "store"
REF = {"schweger": STORE / "3f/3f98de79644f81822bab307985126bf34135229f5b059291a27f214b08ef50c4.png",
       "pirate": STORE / "15/15ece1160d70e12fac12256569fa00cac67187283973648425ebe6af4f56bde8.png",
       "kaiju1": STORE / "8c/8c8775381fe49b0ff577ef5a8a86fce3beda545de82cdedd5e6e49076010c2d8.jpg",
       "kaiju2": STORE / "29/2920ce1f9b9bff7919c3423409e9d7bbd195764a1cb4e21c3de14c741c16d284.jpg"}
LOOK = "A still from a 1950s black and white science fiction B-movie, cheap studio set, 35mm film. No text."
SD_LOOK = ", 1950s science fiction b-movie still, cheap studio model, black and white film photograph, film grain"
VLOOK = " A 1950s science fiction B-movie, cheap practical effects, 35mm film."
SILENT = " Nobody speaks; only sound effects."

# Frames placed by image-to-image: (refs, prompt)
FRAMES = {
    "cockpit": (["schweger"], "The man in the reference image, keeping his face, eye patch, stubble, robotic metal arm, "
                "stained lab coat, holster and boots exactly the same, sits at the controls of a cheap 1950s "
                "spaceship cockpit set with toggle switches, blinking dials and a round porthole full of stars, "
                "waist-up, hands on a chunky lever. " + LOOK),
    "pirates_board": (["pirate"], "Three identical space pirates exactly like the one in the reference image (gorilla "
                      "face, tricorn skull hat, red-and-white striped shirt, hook hand, short stature) burst through a "
                      "round metal airlock hatch into a cardboard spaceship corridor, snarling, full body. " + LOOK),
    "standoff": (["schweger", "pirate"], "The man in the first reference image (keep his face, eye patch, robotic metal "
                 "arm, lab coat, holster, boots exactly) aims a ray gun pistol at three identical space pirates exactly "
                 "like the second reference image, crowded in a round airlock doorway of a cardboard spaceship "
                 "corridor. Full body, wide shot. " + LOOK),
    "kick": (["schweger", "pirate"], "The man in the first reference image (keep his face, eye patch, robotic metal arm, "
             "lab coat, boots exactly) does a huge flying kick into three identical space pirates exactly like the "
             "second reference image, lined up one behind the other like bowling pins in a cardboard spaceship "
             "corridor before an open round airlock. Full body, wide shot. " + LOOK),
    "after": (["schweger"], "The man in the reference image (keep his face, eye patch, stubble, robotic metal arm, lab "
              "coat, holster, boots exactly) stands beside a closed round airlock hatch in a cardboard spaceship "
              "corridor, blowing smoke off a ray gun pistol, smug, waist-up. " + LOOK),
    "city": (["kaiju1", "kaiju2"], "The monster from the first reference image (round silver diving helmet, grey "
             "shirt, giant lobster claws, furry legs) and the monster from the second reference image (red devil "
             "mask, cardboard box body covered in gadgets, black bin bag legs, drill and toy ray gun) are giant men "
             "in suits wrestling clumsily in a miniature model of an ordinary 1950s American town, waist-high to the "
             "cardboard skyscrapers, with a dinner plate flying saucer hanging on a visible fishing line above. "
             "Wide shot. " + LOOK),
    "toe": (["kaiju1", "kaiju2"], "The monster from the first reference image (round silver diving helmet, grey shirt, "
            "giant lobster claws, furry legs) hops on one foot, a bright laser beam hitting its toe, toppling towards "
            "the monster from the second reference image (red devil mask, cardboard box body with gadgets, bin bag "
            "legs) in a miniature model of a 1950s town with cardboard skyscrapers. Wide shot. " + LOOK),
}
# SD stills for mischief (856x480, cropped to 4:3 later)
STILLS = {
    "plate_stars": "a white dinner plate flying saucer hanging on a visible fishing line in front of a black starfield backdrop",
    "plate_hooks": "a white dinner plate flying saucer with three grappling hooks on ropes caught on its rim, black starfield backdrop",
    "cheering": "tiny miniature model town street with crowds of small citizens cheering, cloud of white powder dust, cardboard buildings",
    "sunset": "a white dinner plate flying saucer on a fishing line flying towards a painted sunset backdrop over a model town",
}
# The screenplay's beats, in order, as shots: (id, model, start image, seconds, prompt, line file or None)
SHOTS = [
    ("s01", "card", None, 4, "DR. SCHWEGER'S TRIUMPHANT PATROL", None),
    ("s02", "mischief", "plate_stars", 5, "The dinner plate spaceship glides slowly across the stars, wobbling on its fishing line.", None),
    ("s03", "heygen", "cockpit", 5, "The pilot works the controls calmly and heroically, glancing out the porthole." + SILENT, "01"),
    ("s04", "mischief", "plate_hooks", 5, "The grappling hooks yank on the plate spaceship, which jerks and sways on its fishing line.", None),
    ("s05", "heygen", "pirates_board", 5, "The pirates storm in, waving their hooks. An alarm klaxon blares. The front pirate snarls in a "
     "gruff voice: \"Surrender, spaceman! Your patrol ends here!\"", None),
    ("s06", "heygen", "standoff", 5, "He fires the ray gun twice, pew pew, bright flashes; the pirates flinch and stumble." + SILENT, "02"),
    ("s07", "heygen", "kick", 6, "His mega-kick sends the first pirate crashing into the others; they all fly backwards and tumble "
     "out through the airlock like bowling pins. The airlock hisses shut." + SILENT, None),
    ("s08", "heygen", "after", 5, "He blows the smoke off his ray gun and holsters it, smug." + SILENT, "03"),
    ("s09", "heygen", "city", 8, "The plate saucer descends on its fishing line as the two monsters slowly and clumsily swat at each "
     "other, like an old Godzilla film, knocking over cardboard buildings while tiny citizens scream and flee." + SILENT, None),
    ("s10", "heygen", "cockpit", 5, "He grips the lever, squints with his good eye and fires: a single laser blast, pew." + SILENT, "04"),
    ("s11", "heygen", "toe", 8, "Struck on the toe, the monster wobbles, loses balance and crashes into the other one; both topple in "
     "slow motion, demolishing cardboard buildings in a cloud of talcum powder dust." + SILENT, None),
    ("s12", "mischief", "cheering", 5, "Tiny citizens emerge from the dust, cheering and waving.", None),
    ("s13", "mischief", "sunset", 5, "The plate saucer wobbles heroically on its fishing line as it flies off into the painted sunset.", "05"),
]
MUSIC = [(0, 1, "A stirring 1950s sci-fi fanfare", ["s01", "s08"]),
         (1, 9, "A triumphant march swells", ["s12", "s13"])]


def load():
    return json.loads(MANIFEST.read_text()) if MANIFEST.is_file() else {"frames": {}, "clips": {}, "music": {}, "cost": {}}


def save(m):
    MANIFEST.write_text(json.dumps(m, indent=1))


def uri(p: Path) -> str:
    mime = "jpeg" if p.suffix == ".jpg" else "png"
    return f"data:image/{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def frames():
    m = load()
    out = HERE / "frames"; out.mkdir(exist_ok=True)
    for name, (refs, prompt) in FRAMES.items():
        dst = out / f"{name}.png"
        if dst.is_file():
            continue
        body = {"model": "tencent/hy-image-v3.5-preview", "prompt": prompt, "aspect_ratio": "4:3", "resolution": "1K",
                "input_references": [{"type": "image_url", "image_url": {"url": uri(REF[r])}} for r in refs]}
        d = videogen._request(cfg, "POST", "https://openrouter.ai/api/v1/images", body)
        dst.write_bytes(base64.b64decode(d["data"][0]["b64_json"]))
        m["cost"][f"frame_{name}"] = (d.get("usage") or {}).get("cost")
        save(m); print("frame", name, m["cost"][f"frame_{name}"])
    images = cfg.section("images")
    comfy.ensure_running(cfg, print)
    for name, prompt in STILLS.items():
        dst = out / f"{name}.png"
        if dst.is_file():
            continue
        g = comfy.workflow(images, "final", prompt + SD_LOOK, images["negative"], 856, 480, 4, 1, f"aitv4_{name}")
        comfy.render(cfg, g, out)[0].rename(dst)
        print("still", name)


def videos():
    m = load()
    out = HERE / "clips"; out.mkdir(exist_ok=True)
    jobs = {}
    for sid, model, image, seconds, prompt, _ in SHOTS:
        if model == "card" or sid in m["clips"]:
            continue
        frame = HERE / "frames" / f"{image}.png"
        if model == "mischief":
            jobs[sid] = videogen.submit(cfg, frame, prompt + VLOOK, seconds)["id"]
        else:
            small = frame.with_name(frame.stem + "_640.png")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(frame), "-vf", "scale=640:480", str(small)], check=True)
            body = {"model": "heygen/heygen-video-1", "prompt": prompt + VLOOK, "duration": seconds,
                    "resolution": "480p", "aspect_ratio": "4:3",
                    "frame_images": [{"type": "image_url", "image_url": {"url": uri(small)}, "frame_type": "first_frame"}]}
            jobs[sid] = videogen._request(cfg, "POST", "https://openrouter.ai/api/v1/videos", body)["id"]
        print("submitted", sid, model)
    for sid, jid in jobs.items():
        try:
            cost = videogen.wait(cfg, jid, out / f"{sid}.mp4")
        except videogen.VideoGenError as exc:
            print("FAILED", sid, exc); continue
        m["clips"][sid] = str(out / f"{sid}.mp4"); m["cost"][f"clip_{sid}"] = cost
        save(m); print("clip", sid, cost)


def music():
    m = load()
    conn = db.connect(cfg.db_path)
    prod = conn.execute("SELECT * FROM productions WHERE id = 4").fetchone()
    ctx = pipeline.StageContext(conn, cfg, pipeline.load_format(cfg, "weird_short"), prod, print)
    for scene, beat, text, (a, b) in MUSIC:
        key = f"{scene}_{beat}"
        if key in m["music"]:
            continue
        ids = [s[0] for s in SHOTS]
        seconds = sum(s[3] for s in SHOTS[ids.index(a):ids.index(b) + 1])
        card = "theme" if scene == 0 else "credits"
        asset = pipeline._compose(ctx, scene, beat, text, seconds + 2, card)
        row = conn.execute("SELECT path FROM assets WHERE id = ?", (asset,)).fetchone()
        m["music"][key] = str(cfg.path("library") / row["path"]); save(m)


def assemble():
    m = load()
    work = HERE / "work"; work.mkdir(exist_ok=True)
    parts, t, lines, beds = [], 0.0, [], []
    for sid, model, image, seconds, prompt, line in SHOTS:
        dst = work / f"{sid}.mp4"
        if model == "card":
            png = titles.card(prompt, "blockbuster", work / f"{sid}.png")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(seconds), "-i", str(png), "-f", "lavfi",
                            "-t", str(seconds), "-i", "anullsrc=r=48000:cl=stereo", "-vf", "fps=30,fade=in:d=0.5,"
                            f"fade=out:st={seconds - 0.5}:d=0.5,format=yuv420p", "-c:v", "libx264", "-c:a", "aac",
                            "-shortest", str(dst)], check=True)
        else:
            src = m["clips"][sid]
            has_audio = model == "heygen"
            vf = (f"scale=-2:480,crop=640:480,setsar=1,fps=30,tpad=stop_mode=clone:stop_duration={seconds},"
                  f"trim=duration={seconds}")
            ain = ["-i", src] if has_audio else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, *ain, "-map", "0:v", "-map", "1:a", "-vf", vf,
                            "-af", f"aresample=48000,apad,atrim=duration={seconds}", "-ac", "2", "-c:v", "libx264",
                            "-crf", "16", "-c:a", "aac", "-t", str(seconds), str(dst)], check=True)
        parts.append(dst)
        if line:
            lines.append((PROD / "audio/lines" / f"{line}_dr_schweger.wav", t + 0.6))
        for (scene, beat, _, (a, b)) in MUSIC:
            if sid == a:
                beds.append((m["music"][f"{scene}_{beat}"], t))
        t += seconds
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    picture = work / "picture.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(picture)], check=True)
    # mix: clip audio (sfx, the pirate) + Dr. Schweger's lines + music, then black and white
    ins, chains, labels = ["-i", str(picture)], [], ["[0:a]volume=0.7[a0]"]
    n = 1
    for f, at in lines:
        ins += ["-i", str(f)]; labels.append(f"[{n}:a]aresample=48000,adelay={int(at * 1000)}:all=1,volume=1.6[a{n}]"); n += 1
    for f, at in beds:
        ins += ["-i", str(f)]; labels.append(f"[{n}:a]aresample=48000,adelay={int(at * 1000)}:all=1,volume=0.35[a{n}]"); n += 1
    mix = ";".join(labels) + ";" + "".join(f"[a{i}]" for i in range(n)) + \
        f"amix=inputs={n}:normalize=0:duration=first,alimiter=limit=0.7,loudnorm=I=-16:TP=-1.5[a]"
    final = PROD / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex",
                    mix + ";[0:v]hue=s=0,format=yuv420p[v]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "16", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", str(final)], check=True)
    print("final", final, round(t, 1), "s; spent", round(sum(v or 0 for v in m["cost"].values()), 3))


if __name__ == "__main__":
    {"frames": frames, "videos": videos, "music": music, "assemble": assemble}[sys.argv[1]]()
