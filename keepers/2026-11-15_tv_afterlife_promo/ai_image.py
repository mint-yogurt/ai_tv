"""The one image API call for this promo: the first frame of the mischief clip (16:9, subject centred)."""
import base64, json, sys
from pathlib import Path
from aitv import config, videogen

HERE = Path(__file__).parent
cfg = config.load()
prompt = ("An old wood-grain CRT television set standing alone on the floor of a dark pine forest at night, "
          "in the centre of the frame. Its screen glows with grey-blue static, the only light in the scene, lighting "
          "the low mist and the bark of the tall fir trees around it. A frayed power cord runs off into the darkness. "
          "Pine needles on the ground. Eerie, still and dreamlike, deep black shadows, a 1990s 35mm film still, "
          "subtle film grain.")
body = {"model": videogen.settings(cfg)["image_model"], "prompt": prompt, "aspect_ratio": "16:9", "resolution": "1K"}
data = videogen._request(cfg, "POST", "https://openrouter.ai/api/v1/images", body)
out = HERE / "src" / "ai_first_frame.png"
out.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
(HERE / "src" / "ai_image.json").write_text(json.dumps({"prompt": prompt, "model": body["model"],
                                                        "usage": data.get("usage")}, indent=2))
print(out, data.get("usage"))
