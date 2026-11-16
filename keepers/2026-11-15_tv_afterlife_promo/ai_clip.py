"""The one mischief clip for this promo: 5 s from the AI first frame. One submit, no continuation, no retry."""
import json
from pathlib import Path
from aitv import config, videogen

HERE = Path(__file__).parent
cfg = config.load()
prompt = ("very slow push in toward the television, mist drifts slowly between the trees, the static on the "
          "screen flickers and pulses, branches sway gently, still and quiet")
job = videogen.submit(cfg, HERE / "src" / "ai_first_frame.png", prompt, 5)
print("submitted", job, flush=True)
cost = videogen.wait(cfg, job["id"], HERE / "src" / "ai_clip.mp4", print)
(HERE / "src" / "ai_clip.json").write_text(json.dumps({"prompt": prompt, "job": job, "cost": cost}, indent=2))
print("done", cost)
