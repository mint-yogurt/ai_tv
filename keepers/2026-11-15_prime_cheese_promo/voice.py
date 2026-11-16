"""The narration, in the library's Mark_VHS_announcer voice (Pocket TTS, local, free).
The voice is loaded by asset (70), not reference_for_voice(25): the web UI linked the upload as 'reference'
instead of 'voice_reference' (see the README's Known problems). pocket-tts sometimes aborts on exit after
writing a good file, so a written file counts."""
import sys
from pathlib import Path
from aitv import config, db, tts

HERE = Path(__file__).parent
LINES = {
    "1_age": "Some things... only get better with age.",
    "2_aged": "Ripened for decades... in the finest family rooms in America.",   # "aged" came out two syllables ("age-id") in three takes,
    "3_audience": "In front of a live... studio... audience.",
    "4_name": "Prime Cheese.",
    "5_only": "Only on ChupTV.",
}
cfg = config.load()
conn = db.connect(cfg.db_path)
ref = tts.reference_for_asset(conn, cfg, conn.execute("SELECT * FROM assets WHERE id = 70").fetchone())
for name, text in LINES.items():
    if sys.argv[1:] and name not in sys.argv[1:]:
        continue
    out = HERE / "voice" / f"{name}.wav"
    out.unlink(missing_ok=True)
    try:
        took = f"{tts.speak(cfg, text, ref, out):.0f}s"
    except RuntimeError as e:
        if not out.is_file():
            raise
        took = f"(exit: {str(e)[:40]})"
    print(name, took, flush=True)
