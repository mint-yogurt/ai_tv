"""Vanishing Cream, HeyGen Video 1's take: one hand-written text-to-video call per screenplay scene
(production 0002's screenplay). Tarman's World reference image (entity 10, asset 31, flattened onto grey)
goes in as input_references on his scenes; the model imagines everything else and does all the dialogue,
SFX and on-screen text.

    python3 heygen_take.py submit 1        # submit scenes (by index), recorded in manifest.json
    python3 heygen_take.py wait            # poll every pending job and download finished clips
"""
import base64, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
MODEL, RESOLUTION, ASPECT = "heygen/heygen-video-1", "768p", "4:3"
BASE = "https://openrouter.ai/api/v1"
REFERENCE = HERE / "tarman_reference.jpg"

STYLE = ("A 1987 American late-night TV commercial shot on broadcast video: slightly soft picture, warm saturated "
         "colour, flat bright sitcom lighting, sincere and earnest in tone even though the product is absurd. "
         "No music or jingle of any kind: only room tone, sound effects and the dialogue described.")
NO_TEXT = "No on-screen text, captions or subtitles."

WIFE = "a 1980s housewife with permed auburn hair, a pastel pink blouse and big earrings"
HUSBAND = "her husband, who has an enormous teased 1980s rock-star hairdo and a moustache"
ANNOUNCER = "an unseen male announcer with a deep, earnest, slightly gravelly voice"
TARMAN = ("Tarman, the man in the reference image: a zombie door-to-door salesman with glistening, tar-black, "
          "rotting skin, thin dreadlocked hair, yellowed eyes and a big toothy grin, wearing a stained khaki trench "
          "coat over a black shirt and a stained beige tie, the coat's lining stuffed with little trinkets. Black "
          "tar constantly drips from him. He speaks in a cheerful, raspy, gravelly salesman voice")
JAR = "a squat white jar of cream with a pink label reading \"Vanishing Cream\""

SCENES = [
    (6, False,
     f"Black and white. A suburban living room at night. {WIFE.capitalize()} sits on a floral couch glaring at "
     f"{HUSBAND}; he reads a newspaper beside her, oblivious. She frowns and slowly shakes her head at his hair. "
     f"Over this, {ANNOUNCER} says: \"Are you tired of a husband with too much hair?\" {NO_TEXT}"),
    (9, True,
     f"Colour, bright daytime. A doorbell rings. At the front door of a suburban house, {WIFE} opens the door to "
     f"find {TARMAN}. He holds up {JAR} and says: \"Tarman here! With Vanishing Cream, the cream that makes "
     f"things vanish!\" Tar drips onto her doorstep with a wet plop and he doesn't notice. He points at the jar "
     f"and winks: \"Warts, hair, husbands' hair!\" The wife stares at him. {NO_TEXT}"),
    (10, False,
     f"Colour, a dim suburban bedroom at night. {WIFE.capitalize()} tiptoes up to the bed where {HUSBAND} lies "
     f"asleep. She rubs a dab of cream from {JAR} onto his head. A subtle magical sparkle, and all his hair "
     f"vanishes, leaving a smooth bald scalp. Over this, {ANNOUNCER} says, smooth and amazed: \"Watch as a dab "
     f"on his head makes all that hair disappear.\" He wakes, pats his smooth scalp, looks in a hand mirror with "
     f"delighted surprise and gives a thumbs-up. She winks at the camera. Sound: a twinkling sparkle. {NO_TEXT}"),
    (14, True,
     f"Colour, bright daytime at the same suburban front door. Close-up of {TARMAN}. He holds up {JAR}. Large "
     f"blocky yellow TV text appears on screen: \"$19.95\" and \"1-800-VANISH\". Earnestly, with salesman energy "
     f"he says: \"Call now! Operators are standing by. Remember, Vanishing Cream, the easy way to lose what you "
     f"don't need.\" Small white fine print appears along the bottom of the screen reading \"Not for internal "
     f"use. Do not apply to pets without supervision.\" while he rattles off very fast: \"Not for internal use. "
     f"Do not apply to pets without supervision.\" Then he turns and shuffles away down the path, leaving black "
     f"tar footprints with squishy footsteps, and a yellow \"CAUTION: SLIPPERY\" sign briefly pops up beside "
     f"them. The commercial ends on a product shot of {JAR} on a white background with the words \"Vanishing "
     f"Cream\" and \"1-800-VANISH\" on screen. No other text."),
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
    image = "data:image/jpeg;base64," + base64.b64encode(REFERENCE.read_bytes()).decode()
    for i in indices:
        seconds, tarman, text = SCENES[i]
        body = {"model": MODEL, "prompt": f"{text} {STYLE}", "duration": seconds,
                "resolution": RESOLUTION, "aspect_ratio": ASPECT}
        if tarman:
            body["input_references"] = [{"type": "image_url", "image_url": {"url": image}}]
        job = request("POST", f"{BASE}/videos", body)
        if tarman:  # keep the manifest readable
            body = {**body, "input_references": [{"type": "image_url", "image_url": {"url": REFERENCE.name}}]}
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
