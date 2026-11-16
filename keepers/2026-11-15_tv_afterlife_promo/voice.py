"""The narration, in the library's Weatherman voice (Pocket TTS, local, free)."""
from pathlib import Path
from aitv import config, db, tts

HERE = Path(__file__).parent
LINES = {
    "1_every_show": "Every show you ever turned off...",
    "2_still_playing": "Is still playing.",   # a later take: the first came out as "It's still playing"
    "3_somewhere": "Somewhere...",   # the second take: the first ("Somewhere.") was too weak to survive the mix
    "4_name": "TV Afterlife.",
    "5_backwards": "You never really turned it off.",   # played reversed, under the logo
}
cfg = config.load()
conn = db.connect(cfg.db_path)
ref = tts.reference_for_voice(conn, cfg, 23)  # Weatherman
for name, text in LINES.items():
    took = tts.speak(cfg, text, ref, HERE / "voice" / f"{name}.wav")
    print(name, f"{took:.0f}s")
