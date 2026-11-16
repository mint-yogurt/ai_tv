"""SD feedback loop: txt2img once, then feed each output back through img2img, stitch to 30 s."""

import subprocess
import sys
from pathlib import Path

from aitv import comfy, config

PROMPT = "empty 1990s shopping mall atrium, indoor fountain, potted palms, skylight, glass elevators, teal and pink neon"
ROUNDS = 360         # frames, including the first txt2img
FPS = 12             # 360 frames / 12 fps = 30 s
DENOISE = 0.55
SEED = 1313
WIDTH, HEIGHT = 384, 288   # low-res 4:3

here = Path(__file__).parent
frames = here / "frames"
frames.mkdir(exist_ok=True)
cfg = config.load()
img = cfg.section("images")
neg = img["negative"] + ", people, person, nude"
comfy.ensure_running(cfg, print)

first = comfy.render(cfg, comfy.workflow(img, "final", PROMPT, neg, WIDTH, HEIGHT, SEED, 1, "loop"), frames)[0]
prev = first.rename(frames / "000.png")
print("000", flush=True)
for i in range(1, ROUNDS):
    name = comfy.put_input(cfg, prev, "aitv_loop_in.png")
    out = comfy.render(cfg, comfy.img2img_workflow(img, "draft", name, PROMPT, neg, SEED + i, DENOISE, "loop"), frames)[0]
    prev = out.rename(frames / f"{i:03}.png")
    print(f"{i:03}", flush=True)

subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "%03d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", str(here / "feedback_mallsoft.mp4")], check=True)
print("done", file=sys.stderr)
