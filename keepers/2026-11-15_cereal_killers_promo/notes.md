# CEREAL KILLERS: block promo (2026-11-15)

**`cereal_killers_promo.mp4`**: 30.0 s, 640×480 (4:3), 30 fps, -16 LUFS. Clean (no retrovision pass yet).

A true-crime cold open that turns into a Saturday-morning sugar rush. A kitchen crime scene sets it up:
an overturned bowl, a dog that won't talk, and the TV still on. Then comes a police lineup of the block's
cartoon characters, and the mastermind, Kige Ramsey with a box of Froot Loops. The same cereal jingle
then plays as full cartoon rock over a montage of the block's shows. It ends on a logo slam and Kige's
verdict, "kid tested, mother approved."

## Against the limits

| Limit | This promo |
|---|---|
| 10–30 s, promos trend long | 30.0 s |
| No times or dates | None on screen or in the audio (no "Saturday", "morning", etc.) |
| One AI clip, max 5 s, mischief | 1 × `tbc/mischief-1`, 5 s, **$0.025**; 4.0 s used (0:00–0:04). One submit, no retry. |
| One image API generation | 1 × `tencent/hy-image-v3.5-preview`, 16:9, **$0.024**: the clip's first frame |
| Unique font on title cards | **Creepster** (Google Fonts, archived in `library/fonts/`). Not used anywhere else at ChupTV |
| Cards overlaid on footage, never a plain colour | Every card is a transparent layer over moving footage or a pushing-in freeze frame |
| Local SD | Not used |
| MIDIgen | Score hand-written in-session (`music/compose.py` → `cereal_killers.json`), rendered by MIDIgen with `gm.sf2`. No LLM call |

**Total spend: $0.049.**

## Edit (bar = 2 s, every bar line is a cut)

| Time | Picture | Source (in point) | Card / sound |
|---|---|---|---|
| 0–4 | AI kitchen crime scene, push-in | `src/ai_clip.mp4` 0.0 | THE BOWL WAS EMPTY. Music box plays the jingle in a minor key over a heartbeat |
| 4–6 | Dog, eyes light up | Goosebumps S01E03, 24.4 | THE ONLY WITNESS ISN'T TALKING. |
| 6–8 | Cryptkeeper TV | Tales from the Cryptkeeper S01E02, 58.3 | AND THE TV WAS STILL ON. + the Cryptkeeper's cackle (same episode's audio, 59.8–61.8) |
| 8–14 | Lineup: freeze + push-in, height chart, camera flash, "SUSPECT n" | Pingu S01E01 82.6 · Toxic Crusaders 1x02 19.4 · Pee-wee E01 37.9 · Street Sharks S01E02 7.9 · Cryptkeeper 52.2 · Beast Wars S01E02 10.35 | THE USUAL SUSPECTS. Orchestra hit + clap/splash (the flash) rising a semitone per suspect |
| 14–16 | Kige with Froot Loops | kige_ramsey/Breakfast Cereal.mp4, 43.3 | THE MASTERMIND. Dun, dun, DUNNN |
| 16–26 | Montage, cuts on the beat, eighth-note cuts in the last bar | Street Sharks, Real Ghostbusters S01E02, Beast Wars, Toxic Crusaders, Cryptkeeper, ReBoot S1E10, Power Rangers S01E01, Super Mario Bros. Super Show 01, Pee-wee, Pingu | THEY TRAVEL IN PACKS. / THEY FIGHT DIRTY. / THEY ALWAYS COME BACK FOR SECONDS. Jingle as cartoon rock (A major) |
| 26–30 | CEREAL KILLERS logo slam over Goosebumps' slime | Goosebumps S01E03, 44.6 | Kige: "kid tested, mother approved" (Breakfast Cereal.mp4 29.05–31.45). ONLY ON CHUPTV, final button |

Exact in points are in `SHOTS` in `build.py`. Show paths are under `/mnt/hdd_1tb/tv`.

**Every sound, and where it's from:**
- The MIDIgen score, including the lineup's clap/splash "camera flash".
- The Cryptkeeper's cackle (Tales from the Cryptkeeper S01E02).
- Kige's line (Breakfast Cereal.mp4).

Nothing is synthesised or downloaded.

**Checked:**
- Scene detection on the final: every planned cut is there, and nothing leaks. The two extra cuts (16.7 s
  and 17.9 s) are the source's own cuts within the same action.
- First and last frames of every risky shot.
- Whisper on the final for the cackle and Kige's line.
- -15.8 LUFS, peak -1.7 dBFS.

## How it was made

- `ai_image.py` makes the one image call, and `ai_clip.py` makes the one mischief clip. Both use ai_tv's
  `videogen` helpers; neither ever runs again on a rebuild.
- `music/compose.py` writes the MIDIgen composition: 120 bpm, 15 bars = 30.0 s, cuts on bar lines.
- `build.py` rebuilds the whole thing in about 45 s:
  - cuts and grades: cold blue in act 1, crunchy in the lineup, saturated in the montage;
  - draws the title layer frame by frame with Pillow: pop-ins, a slight "boiling line" jitter, and the
    logo slam with a shake;
  - mixes and loudness-normalises the audio.
- Whisper (local, CPU) found Kige's line and the cackle.
- Run: `cd ~/ai_tv && .venv/bin/python test_outputs/video/2026-11-15_cereal_killers_promo/build.py`

## Fonts used so far (each promo gets its own)

1. Cereal Killers: Creepster
