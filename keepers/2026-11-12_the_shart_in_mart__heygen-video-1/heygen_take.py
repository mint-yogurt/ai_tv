"""The Shart in Mart trailer, HeyGen Video 1's take: one hand-written text-to-video call per screenplay
scene (production 0003's screenplay), the model's own picture, dialogue, SFX and on-screen titles.

    python3 heygen_take.py submit 1        # submit scenes (by index), recorded in manifest.json
    python3 heygen_take.py wait            # poll every pending job and download finished clips
"""
import json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
MODEL, RESOLUTION, ASPECT = "heygen/heygen-video-1", "768p", "4:3"
BASE = "https://openrouter.ai/api/v1"

STYLE = ("Shot on 35mm film in 1991, heavy grain, dim practical light, still and deliberate camera, "
         "deadpan and serious, an art-house horror film in the vein of David Lynch. "
         "No music or score of any kind: only room tone, ambience, sound effects and the dialogue described.")
NO_TEXT = "No on-screen text, captions, subtitles or logos."

SCENES = [
    (5, "Grainy black-and-white handheld footage inside a dimly lit, half-empty discount store at night. "
        "A worried family of three (mother, father, young daughter) moves slowly down a sparsely stocked aisle, "
        "peering ahead and flinching at shadows between the shelves. The fluorescent tubes hum. "
        "Over the footage, an off-screen female narrator whispers, eerily calm and unnervingly gentle: "
        "\"Have you ever felt... watched while shopping?\" " + NO_TEXT),
    (5, "Full colour. Extreme close-up of a sweating middle-aged man's face filling the frame, every pore visible, "
        "eyes wide and unblinking, staring just past the lens. A bead of sweat runs down his forehead. "
        "His mouth twitches, and barely moving his lips he whispers: \"It's here.\" "
        "A low subsonic hum vibrates under the silence. " + NO_TEXT),
    (5, "Stop-motion claymation, visibly animated frame by frame with handmade textures. In a grocery aisle "
        "lit by sickly green flickering fluorescent tubes, the shelves glisten as if sweating. A creature woven "
        "from translucent plastic shopping bags, damp curling receipt paper and spirals of price stickers "
        "slithers out from between dusty tin cans and exhales a thick fog. Sound: crackling plastic and a "
        "moist, shuddering exhale. " + NO_TEXT),
    (8, "A montage of three quick cuts, each held slightly too long. Cut one: a supermarket checkout conveyor "
        "belt rolls backward, slowly swallowing a cereal box into the dark gap at its end. Cut two: a dark "
        "aisle where a red glow pulses behind a hanging aisle sign marked with the number 9. Cut three: a "
        "faceless department store mannequin in a clothing display turns its head toward camera with a single "
        "click. Sound: distorted cash register beeps and looped, unintelligible whispering. "
        "No other on-screen text, captions or subtitles."),
    (5, "A title card on pure, impenetrable black. In large, elegant, widely spaced white serif capitals, centred, "
        "the title slowly fades in: \"THE SHART IN MART\". The letters seem to drip very slowly. A moment later, "
        "beneath it in smaller serif italics, the tagline fades in: \"Everything must go.\" "
        "Nothing else on screen, no other text. Sound: a lone, distorted cash-register ding, then a low, "
        "building rumble."),
    (5, "A plain black screen. In small, clean white serif lettering in the lower third, centred, fine print "
        "slowly materializes: \"RATED R\" and below it \"Coming soon to select theaters.\" It holds, then the "
        "text fades as the screen dissolves to total black. Nothing else on screen. Sound: a deep rumble that "
        "swells to swallow everything, then cuts to dead silence."),
]


def request(method, url, body=None, raw=False):
    key = os.environ["OPENROUTER_API_KEY"]
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {url} said {exc.code}: {exc.read()[:800].decode(errors='replace')}")
    return data if raw else json.loads(data)


def load():
    return json.loads(MANIFEST.read_text()) if MANIFEST.is_file() else {"model": MODEL, "jobs": []}


def save(m):
    MANIFEST.write_text(json.dumps(m, indent=1))


def submit(indices):
    m = load()
    for i in indices:
        seconds, text = SCENES[i]
        body = {"model": MODEL, "prompt": f"{text} {STYLE}", "duration": seconds,
                "resolution": RESOLUTION, "aspect_ratio": ASPECT}
        job = request("POST", f"{BASE}/videos", body)
        m["jobs"].append({"scene": i, "id": job["id"], "body": body, "submitted": time.time(),
                          "status": job.get("status", "submitted")})
        save(m)
        print(f"scene {i}: submitted {job['id']}", flush=True)


def wait():
    m = load()
    while True:
        pending = [j for j in m["jobs"] if j["status"] not in ("completed", "failed", "cancelled", "expired")]
        if not pending:
            break
        for j in pending:
            s = request("GET", f"{BASE}/videos/{j['id']}")
            if s["status"] == "completed":
                dst = HERE / "clips" / f"scene{j['scene']}_{j['id'][-8:]}.mp4"
                dst.write_bytes(request("GET", f"{BASE}/videos/{j['id']}/content?index=0", raw=True))
                j.update(status="completed", finished=time.time(), cost=(s.get("usage") or {}).get("cost"),
                         file=dst.name)
                print(f"scene {j['scene']}: done in {j['finished'] - j['submitted']:.0f}s, ${j['cost']}", flush=True)
            elif s["status"] in ("failed", "cancelled", "expired"):
                j.update(status=s["status"], finished=time.time(), error=s.get("error"),
                         cost=(s.get("usage") or {}).get("cost"))
                print(f"scene {j['scene']}: {s['status']}: {s.get('error')}", flush=True)
            save(m)
        time.sleep(15)


if __name__ == "__main__":
    if sys.argv[1] == "submit":
        submit([int(a) for a in sys.argv[2:]])
    else:
        wait()
