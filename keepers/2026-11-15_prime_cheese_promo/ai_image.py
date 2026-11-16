"""The one image API call for this promo: the first frame of the mischief clip (16:9, subject centred)."""
import base64, json, sys
from pathlib import Path
from aitv import config, videogen

HERE = Path(__file__).parent
cfg = config.load()
prompt = ("A 1990s luxury television commercial: a huge whole wheel of aged golden cheddar with a crumbly rind sits "
          "on a dark slate board in the centre of the frame, in a candlelit stone wine cellar. A crystal glass of red "
          "wine and a bunch of dark grapes beside it, a silver cheese knife resting against the wheel. Warm golden "
          "candlelight, rich deep shadows, shallow depth of field, soft bokeh of candles and wine bottles behind. "
          "Opulent, pretentious, glossy, a 35mm film still, subtle film grain.")
body = {"model": videogen.settings(cfg)["image_model"], "prompt": prompt, "aspect_ratio": "16:9", "resolution": "1K"}
data = videogen._request(cfg, "POST", "https://openrouter.ai/api/v1/images", body)
out = HERE / "src" / "ai_first_frame.png"
out.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
(HERE / "src" / "ai_image.json").write_text(json.dumps({"prompt": prompt, "model": body["model"],
                                                        "usage": data.get("usage")}, indent=2))
print(out, data.get("usage"))
