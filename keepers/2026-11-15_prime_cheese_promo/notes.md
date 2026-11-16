# PRIME CHEESE: block promo (2026-11-15)

**`prime_cheese_promo.mp4`**: 30.0 s, 640×480 (4:3), 30 fps, -16 LUFS. Clean (no retrovision pass yet).

The idea: a pretentious luxury cheese advert that turns out to be about sitcoms.
1. **The advert.** Candlelit cellar, a knife through a wheel of aged cheddar, strings and a harp. The VHS
   announcer purrs over warm, glowing slow-motion portraits of the block's stars.
2. **The flip.** "...in front of a live studio audience." A record scratch, and the laugh track explodes.
3. **The grading.** Each show's own gag plays with its own sound, and a red wax stamp grades it like a cheese.
4. **The finale.** A strobe of grins and the Dance of Joy, then back to the cheese: PRIME CHEESE in gold
   leaf, and the audience applauds.

> **Flashing:** the strobe (21.7–23.5 s) cuts every 2–4 frames, with every third flash brightened. That's
> about 10 cuts a second, milder than TV Afterlife's (no negatives, no full blow-outs).

## Against the limits

| Limit | This promo |
|---|---|
| 10–30 s, promos trend long | 30.0 s |
| No times or dates | None. No "daytime", no days. The Dinosaurs opening's burned-in "61,000,003 B.C. / THURSDAY" card was avoided; every shot was checked for credits |
| One AI clip, max 5 s, mischief | 1 × `tbc/mischief-1`, 5 s, **$0.025**. 0:00–0:04.2, then 1.0–4.25 at half speed under the logo. One submit, no retry |
| One image API generation | 1 × `tencent/hy-image-v3.5-preview`, 16:9, **$0.024**: the clip's first frame |
| Unique font, cards over footage | **Cinzel Decorative Black** (Google Fonts, archived in `library/fonts/`). Every card sits over moving picture |
| Local SD | Not used |
| MIDIgen: ambient and stings only | A string-and-harp bed for the advert (silenced at the scratch) and a timpani roll into a D major tutti with a celesta "ding" for the logo. Hand-written, so no LLM call. Everything between them is the shows' own sound and laugh tracks |
| A TTS voice from the library | **Mark_VHS_announcer** (Pocket TTS, local) |
| Online SFX and field recordings | 17 Openverse/Freesound sounds (library assets 71–87, tag `prime_cheese_promo`), 14 used |
| Gentle ducking | The bed sits low (music at 0.6, where TV Afterlife's was 0.9). Under the narration everything else dips 2.5 dB (×0.75) with 0.3 s ramps, measured with a test tone through the mix's own gain curve. Under the logo the sting also eases from 0.6 to 0.42 over 0.8 s, so the names sit on top |

**Total spend: $0.049.**

## Edit

| Time | Picture | Source (in point) | Words / sound |
|---|---|---|---|
| 0–4.2 | Knife through the cheese wheel, slow push | `src/ai_clip.mp4` 0.0 | "Some things... only get better with age." Fire crackle, the cut, a wine pour, string bed |
| 4.2–9.6 | Slow, glowing portraits that dissolve into each other: Earl, Balki ("America or Burst"), Tim, ALF | Dinosaurs S01E03 31.0 · Perfect Strangers 2x02 39.3 · Home Improvement S01E02 12.6 · ALF S01E03 13.2, all ½ speed | "Ripened for decades... in the finest family rooms in America." THE RESERVE COLLECTION. Restaurant murmur, glass clink |
| 9.6–12.0 | The Tanners' living room, slow push | ALF S01E03 31.0, 0.6× | "In front of a live... studio... audience." |
| 12.0 | | | Record scratch, and the laugh track explodes |
| 12.0–14.4 | Baby takes the frying pan to Earl's head: "not the mama!" | Dinosaurs S01E03 201.55 | **SHARP** |
| 14.4–16.5 | Balki hangs off Larry: "It's a custom of my people." | Perfect Strangers 2x05 244.62 | **IMPORTED** |
| 16.5–18.2 | Mark on the mower: "More power!" | Home Improvement S01E02 86.65 | **EXTRA STRONG** |
| 18.2–19.3 | ALF in the kitchen window: "Grease fire!" | ALF S01E03 81.9 (sound from 79.95; he yells it over Kate's shot just before) | |
| 19.3–21.7 | Smoke pours out of the window: "Never mind the curtains. Put me out." | ALF S01E03 85.75 | **SMOKED** |
| 21.7–23.5 | Strobe: grins, takes, the Dance of Joy | the openings of all four shows | The big laugh. Timpani roll |
| 23.5–30 | The cheese again, half speed, pushing in. PRIME CHEESE in gold leaf with a glint; ONLY ON CHUPTV | `src/ai_clip.mp4` 1.0 | Tutti and harp. "Prime Cheese." / "Only on ChupTV." Applause, celesta ding |

Show paths are under `/mnt/hdd_1tb/tv`. Exact numbers are in `build.py`.

## Sounds (Freesound, via Openverse; library assets)

`build.py` reads the copies in `src/sfx/`, so the library's 30-day cull can't break a rebuild.

| Name | Title | Asset | Page |
|---|---|---|---|
| laugh_1 | Laugh Track 1 | 71 | https://freesound.org/people/LittleRainySeasons/sounds/395410 |
| laugh_2 | Laugh Track 2 | 72 | https://freesound.org/people/LittleRainySeasons/sounds/395409 |
| laugh_5 | Laugh Track 5 | 73 | https://freesound.org/people/LittleRainySeasons/sounds/395413 |
| crowd_laugh | Crowd Laugh | 74 | https://freesound.org/people/kelsey_w/sounds/467042 |
| laugh_claps | smallAudienceLaughsThenClaps | 75 | https://freesound.org/people/oniwe/sounds/32868 |
| cheer_clap | cheering and clapping crowd 1 | 76 | https://freesound.org/people/AlaskaRobotics/sounds/221568 |
| wine_pour | Pouring Glass of Wine from Bottle | 78 | https://freesound.org/people/nebulousflynn/sounds/220303 |
| fireplace | Fireplace Crackles | 80 | https://freesound.org/people/WavJunction.com/sounds/512685 |
| harp | Harp Glissando | 81 | https://freesound.org/people/everythingsounds/sounds/594961 |
| cheese_cut | cuttingMozarella | 82 | https://freesound.org/people/qubodup/sounds/87507 |
| stamp | stamp, rubber, plastic | 83 | https://freesound.org/people/15FPanska_KristynaHaupt/sounds/461888 |
| clink | Wine glasses cheers | 84 | https://freesound.org/people/Mega-X-stream/sounds/429028 |
| restaurant | restaurant ambience | 85 | https://freesound.org/people/soundmast123/sounds/504900 |
| record_scratch | Record Scratch #4 | 86 | https://freesound.org/people/musicvision31/sounds/431777 |

Also fetched but not used: aww (77), a champagne cork (79), a tape-rewind screech (87).

## How it was made

| Script | What it does |
|---|---|
| `ai_image.py`, `ai_clip.py` | The one image call and the one mischief clip. They never run again on a rebuild |
| `fetch_sfx.py` | Downloads the sounds into the library and copies them into `src/sfx/` |
| `voice.py` | The narration. It loads the voice by asset (70), because the web UI linked the upload as `reference`, not `voice_reference` (see the README's Known problems). Line 2 was "Aged for decades...", but the voice said "age-id" in three takes, so it became "Ripened for decades..." |
| `music/compose.py` | The bed and the sting |
| `build.py` | Everything else, in about 3 min: the grades (warm glowing advert, loud saturated sitcoms), the dissolves, the title layer (gold leaf with a moving glint, red wax stamps that slam on with a bounce), and the mix |

The catchphrases were found with local Whisper. Run on whole episodes it returned nothing for the AVI/AAC
files, so episodes are transcribed in 2-minute chunks (`.work/tx/`), and every in-point was then
word-timed and checked with peeks and scene detection.

**Checks:**
- Scene detection on the final: only the planned cuts, the Dinosaurs shot's own Earl-to-Baby cut (13.1),
  and the strobe.
- Edge frames at every montage cut.
- Whisper on the final mp4:
  - Narration: "Some things only get better with age, ripen for decades in the finest family rooms in
    America, in front of a live studio audience." and "Prime Cheese, only on Chuck TV" (the station name
    is fine to the ear; Whisper doesn't know it).
  - Show lines: "It's a custom of my people.", "More power!" and "Never mind the curtains, put me out!"
  - Baby's "not the mama" and ALF's "grease fire" are puppet voices that Whisper mangles even in the
    source. Listen for those two.
- -16.0 LUFS, peak -1.1 dBFS.

Run: `cd ~/ai_tv && .venv/bin/python test_outputs/video/2026-11-15_prime_cheese_promo/build.py`

Fonts so far: Cereal Killers, Creepster; TV Afterlife, IM Fell English SC; Prime Cheese, Cinzel Decorative.
