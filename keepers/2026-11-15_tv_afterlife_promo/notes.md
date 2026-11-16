# TV AFTERLIFE: block promo (2026-11-15)

**`tv_afterlife_promo.mp4`**: 30.0 s, 640×480 (4:3), 30 fps, -16 LUFS. Clean (no retrovision pass yet).

The idea: every show you ever turned off is still playing, somewhere.
> **Flashing:** from 18.5 s the strobe flashes about 10–15 full-frame times a second (blown-out, dark and
> negative frames). That's well past the ~3 per second broadcast limit for photosensitive viewers. A gentler
> version (3–4 frame bursts, no full blow-outs) is a one-line change in `build.py`.

1. **The woods.** A TV sits alone in a pine forest at night, showing static. The block's shows drift past
   in slow motion, held frames, never interpolated:
   - Evangelion's power pole;
   - Space Ghost fading in, transparent, over his empty desk;
   - the empty train car;
   - Liquid Television's floating head;
   - a silhouette in the pampas grass.
2. **The strobe.** Electricity builds into a strobe that cuts every 1–4 frames, then half a second of dead
   silence.
3. **Into the screen.** Back in the woods, the camera rushes into the screen, and the static dreams its way
   into an endless red hallway (local SD).
4. **The logo.** TV AFTERLIFE, then the set switches off: the picture collapses to a line, the line to a
   dot.

A cheerful weatherman says all of it, slowed a touch and put in a room.

## Against the limits

| Limit | This promo |
|---|---|
| 10–30 s, promos trend long | 30.0 s |
| No times or dates | None on screen or in the audio. The strobe's Sightings camcorder shots were left out because they have burned-in dates |
| One AI clip, max 5 s, mischief | 1 × `tbc/mischief-1`, 5 s, **$0.025**. 0:00–0:04, then 4.0–5.06 again at 0:20 for the push into the screen. One submit, no retry |
| One image API generation | 1 × `tencent/hy-image-v3.5-preview`, 16:9, **$0.024**: the clip's first frame |
| Unique font, cards over footage | **IM Fell English SC** (Google Fonts, archived in `library/fonts/`). Every line sits over moving picture |
| Local SD | 96-round feedback loop (Realistic Vision 5.1, zoom 3% per round), seeded with the TV screen from the AI clip's last frame. Denoise ramps 0.3 → 0.62 over 24 rounds, so the static melts rather than jumps. Free, about 5 min on the 970 |
| MIDIgen: ambient and stings only | An F minor 9 / D-flat major 9 bed, a tremolo cluster under the strobe, and an F major 9 + tubular-bell sting for the logo. Hand-written, so no LLM call |
| A TTS voice from the library | **Weatherman** (Pocket TTS, local) |
| Online SFX and field recordings | 11 Openverse/Freesound sounds, now in the library (assets 58–69, tag `tv_afterlife_promo`) |

**Total spend: $0.049.**

## Edit

| Time | Picture | Source (in point, speed) | Words / sound |
|---|---|---|---|
| 0–4 | The TV in the woods | `src/ai_clip.mp4` 0.0 | "Every show you ever turned off..." Pine wind, crickets and an owl, the set's static |
| 4–6.5 | Power pole | Evangelion S01E04 935.6, ½ speed | Power-line hum |
| 6.5–9.5 | Empty desk; Space Ghost fades in, transparent | Space Ghost C2C S01E01 63.7, 0.6× | "...is still playing." Hotel-room tone (distant voices, low throb) |
| 9.5–11.5 | Empty train car | Evangelion S01E04 302.0, ½ | A distant train |
| 11.5–13.5 | Floating head on cyan | Liquid Television 101 806.0, ½ | Old fluorescent fixture, rising |
| 13.5–15.5 | Silhouette in the pampas grass | Evangelion S01E04 636.3, ½ | |
| 15.5–20.0 | Strobe: 1–4 frame flashes, blown out or negative | Evangelion 401/937/302, Space Ghost 92/98/66, Liquid TV 26/286/859, Witch Hunter Robin 01 27.6 | Jacob's ladder, bulb flicker, static, a tremolo cluster. A dead stop at 20.0 |
| 20.0–21.06 | Back in the woods, rushing into the screen | `src/ai_clip.mp4` 4.0 | 0.5 s of nothing, then wind |
| 21.06–29.3 | Static → kaleidoscope → doors → red hallway | `sd/fall_into_tv.mp4` | "Somewhere..." and a voice played backwards ("you never really turned it off"), reversed cymbal |
| 24–29.3 | TV AFTERLIFE (a flickering bulb) / only on chuptv | | Bell sting; "TV Afterlife." |
| 29.3–30 | The set switches off: line, dot, black | | Only the CRT's click; everything else stops |

## Sounds (Freesound, via Openverse; library assets)

`build.py` reads the copies in `src/sfx/`, so a rebuild doesn't depend on the library: the 30-day cull deletes
online downloads that no production has used.

| Name | Title | Asset | Page |
|---|---|---|---|
| pine_wind | wind pine trees 03 | 58 | https://freesound.org/people/klankbeeld/sounds/463562 |
| crickets_owl | CricketsandTawnyOwl | 59 | https://freesound.org/people/raoul_slayer/sounds/203598 |
| powerlines | high_voltage_powerlines | 60 | https://freesound.org/people/833-45/sounds/11727 |
| hotel_room_tone | room tone hotel room, distant voices, low hum | 62 | https://freesound.org/people/kyles/sounds/406507 |
| fluorescent | Old Fluorescent Fixture | 63 | https://freesound.org/people/Jean_Sander/sounds/399365 |
| bulb_flicker | fluorescent flickering electric bulb | 64 | https://freesound.org/people/TRP/sounds/574100 |
| jacobs_ladder | jacobs ladder from side | 65 | https://freesound.org/people/parabolix/sounds/449013 |
| tv_static | vintage TV static noise | 66 | https://freesound.org/people/oliwoli/sounds/633174 |
| crt_off | TV, CRT, turning on off, static, click | 67 | https://freesound.org/people/TRP/sounds/616850 |
| distant_train | Distant Train Passing | 68 | https://freesound.org/people/simgo/sounds/380671 |
| reverse_glass | cymbal-reversed-glassy-resonance | 69 | https://freesound.org/people/burnttoys/sounds/9523 |

Also fetched but not used: a street-lamp hum (asset 61).

## How it was made

| Script | What it does |
|---|---|
| `ai_image.py`, `ai_clip.py` | The one image call and the one mischief clip. They never run again on a rebuild |
| `fetch_sfx.py` | Downloads the sounds into the library |
| `voice.py` | The narration, recorded with Pocket TTS. Two lines are later takes: "Is still playing." (the first said "It's") and "Somewhere..." (the first was too quiet to survive the mix) |
| `sd_loop.py` | The SD loop, written to `sd/fall_into_tv.mp4` |
| `music/compose.py` | The bed and the sting |
| `build.py` | Everything else, in about 1.5 min: the slow-motion shots, the strobe, the push-in, the title layer (IM Fell English SC with a soft glow, the logo flickering like a bad bulb), the switch-off, and a mix where the beds duck under the voice |

**Checks:**
- Scene detection outside the strobe: only the planned cuts, plus the SD's own morphs.
- First and last frames of every shot.
- Whisper on the final mp4: "Every show you ever turned off... is still playing." / "Somewhere." / "TV Afterlife." Lines
  1 and 2 were checked together, as they're heard; heard alone, the soft "is" is ambiguous.
- -15.9 LUFS, peak -1.3 dBFS.

Run: `cd ~/ai_tv && .venv/bin/python test_outputs/video/2026-11-15_tv_afterlife_promo/build.py`

Fonts so far: Cereal Killers, Creepster; TV Afterlife, IM Fell English SC.
