"""Local SD 1.5 feedback loop with a 3% zoom per round (the keepers/ method), seeded with the AI clip's last
frame, cropped to the TV screen: the camera falls into the static and it dreams its way somewhere else. Free (ComfyUI on the 970)."""
import subprocess
from pathlib import Path
from PIL import Image
from aitv import comfy, config

HERE = Path(__file__).parent
FRAMES = HERE / ".sd_frames"          # hidden from the Tests page
PROMPT = ("red velvet curtains, a dark empty hallway, a single flickering lamp, drifting smoke, "
          "television static, dreamlike, surreal, 1990s film still")
ROUNDS, FPS, DENOISE, ZOOM, SEED = 96, 12, 0.62, 1.03, 2611
RAMP = 24   # denoise climbs from 0.3 over the first RAMP rounds, so the static melts instead of jumping
W, H = 384, 288

FRAMES.mkdir(exist_ok=True)
cfg = config.load()
img = cfg.section("images")
neg = img["negative"] + ", people, person, nude, text, watermark"
seed_frame = FRAMES / "seed.png"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", str(HERE / "src" / "ai_clip.mp4"), "-frames:v", "1",
                "-vf", f"crop=ih*4/3:ih,crop=246:184:215:183,scale={W}:{H}", str(seed_frame)], check=True)
comfy.ensure_running(cfg, print)
prev = seed_frame
for i in range(ROUNDS):
    src = Image.open(prev)
    cw, ch = round(W / ZOOM), round(H / ZOOM)
    zoomed = FRAMES / "zoom_in.png"
    src.crop(((W - cw) // 2, (H - ch) // 2, (W + cw) // 2, (H + ch) // 2)).resize((W, H), Image.LANCZOS).save(zoomed)
    name = comfy.put_input(cfg, zoomed, "aitv_loop_in.png")
    out = comfy.render(cfg, comfy.img2img_workflow(img, "draft", name, PROMPT, neg, SEED + i,
                                                 0.3 + (DENOISE - 0.3) * min(1.0, i / RAMP), "loop"), FRAMES)[0]
    prev = out.rename(FRAMES / f"{i:03}.png")
    print(f"{i:03}", flush=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS), "-i", str(FRAMES / "%03d.png"),
                "-vf", "scale=640:480:flags=lanczos", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "-r", "30",
                str(HERE / "sd" / "fall_into_tv.mp4")], check=True)
print("done")
