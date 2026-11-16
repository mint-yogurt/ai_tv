"""The one image API call for this promo: the first frame of the mischief clip (16:9, subject centred)."""
import base64, json, sys
from pathlib import Path
from aitv import config, videogen

HERE = Path(__file__).parent
cfg = config.load()
prompt = ("A 1990s suburban kitchen just after sunrise, seen from table height. In the centre of the frame a "
          "kitchen table with an overturned bowl of bright neon sugary cereal, loops and marshmallow shapes "
          "scattered across the table, a pool of spilled milk running to the table edge and dripping onto the "
          "linoleum floor, a spoon lying on the floor. Behind the table on the counter a small wood-grain CRT "
          "television glows with saturated cartoon colours. Yellow police tape is strung across the doorway in "
          "the background. Cold blue morning light through venetian blinds, long shadows, dust in the air. "
          "Moody cinematic 35mm film still from a 1990s crime thriller, shallow depth of field, film grain.")
body = {"model": videogen.settings(cfg)["image_model"], "prompt": prompt, "aspect_ratio": "16:9", "resolution": "1K"}
data = videogen._request(cfg, "POST", "https://openrouter.ai/api/v1/images", body)
out = HERE / "src" / "ai_first_frame.png"
out.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
(HERE / "src" / "ai_image.json").write_text(json.dumps({"prompt": prompt, "model": body["model"],
                                                        "usage": data.get("usage")}, indent=2))
print(out, data.get("usage"))
