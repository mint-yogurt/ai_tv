"""The one mischief clip for this promo: 5 s from the AI first frame. One submit, no continuation, no retry."""
import json
from pathlib import Path
from aitv import config, videogen

HERE = Path(__file__).parent
cfg = config.load()
prompt = ("slow steady push in toward the kitchen table, milk drips off the edge of the table, the television "
          "screen flickers with bright colours, dust drifts in the light from the blinds, quiet and still")
job = videogen.submit(cfg, HERE / "src" / "ai_first_frame.png", prompt, 5)
print("submitted", job, flush=True)
cost = videogen.wait(cfg, job["id"], HERE / "src" / "ai_clip.mp4", print)
(HERE / "src" / "ai_clip.json").write_text(json.dumps({"prompt": prompt, "job": job, "cost": cost}, indent=2))
print("done", cost)
