# AI TV

> **TODO:** add a checkbox to the generation web UI for running the final generation through
> retrovision.

> ## ⚠️ CRITICAL BUG: orphaned ComfyUI holds the GPU (found 2026-11-14)
>
> **Whoever reads this next: fix it, then delete this whole warning.**
>
> When the worker restarts itself in place after a code change (`os.execv` in `worker.serve`),
> the new process loses `comfy._server`, its handle on the ComfyUI it started. That ComfyUI keeps
> running as an orphan (parent PID 1) with its models loaded, and nothing ever stops it:
> `comfy.stop_if_idle()` and `comfy.shutdown()` only stop a server *this* process started. On
> 2026-11-14 one had been running for 1 day 15 hours, holding ~1 GB of the 4 GB GTX 970. That's
> enough to push lip-sync (~3.1 GB) or a live ErsatzTV NVENC encode out of memory.
>
> - **Stopgap in place:** `comfy.free_memory()` asks any running ComfyUI to unload its models over
>   its `/free` API (it doesn't stop the process). `lipsync.render` calls it before every job.
> - **Real fix (to do):** make an orphan stoppable. For example, write ComfyUI's PID to a file when
>   starting it and have `shutdown()`/`stop_if_idle()` stop that PID. Or call `/free` before
>   `execv` and track idleness from ComfyUI's own `/queue` instead of `_last_used`. Then check that
>   `nvidia-smi` shows no ComfyUI after an in-place restart plus the idle timeout.

> **NEXT UP: test Pipeline v2** (built 2026-11-15; editor, QC, reviewer, footage). See [`START_HERE_PIPELINE_V2.md`](START_HERE_PIPELINE_V2.md).

A modular, mostly-local pipeline for making slightly cursed television: commercials, trailers,
shorts, whatever you prompt it for. You write a prompt on the Make page (or let a format's dice
pick an idea); it writes it, casts it from a database of recurring voices, sets, brands and
footage, generates what's missing, and stitches it into a finished video.

It is a fun learning project. The target is "not intentionally bad, but the AI slop is part
of the joke". When polish and cheap-and-fast disagree, cheap and fast win.

> **Status: alpha.** Every stage from outline to publish is built, and productions run start to
> finish unattended. Pipeline v2 (editor, QC, reviewer) is built and mostly switched off until
> its last test; see below.

---

## What's nailed down, and what isn't

| Area | State | Notes |
| --- | --- | --- |
| Vision and scope | **Decided** | Idea → outline → screenplay → database → generate → stitch. One finished video file per production. No streaming. |
| Database | **Built** | SQLite, `aitv/schema.sql`. World entities, assets, ideas, productions, documents, shots, jobs, usage log. |
| Library ingest | **Built** | Upload or drop files, then hashing, de-duplication, thumbnails, waveforms, 480p proxies and voice reference clean-up. |
| Job queue and worker | **Built** | One job at a time, at low priority, resumable. Missing tools mark jobs `blocked`, not failed. |
| Web UI | **Built** | Private control room on the Tailscale network (below). |
| Make form | **Built** | Prompt, free-text format, tags, length (exact or a range, in m:ss), cast/voice/asset picks, a model per stage and a spending limit. Nothing is random: the writer gets only what you typed. See [The Make page](#the-make-page). |
| Dice and tags (format productions) | **Built** | Productions made from a format (`aitv produce`, an idea's "Make it") still draw weighted random tags per format, with tags you set winning, a stored seed, and least-recently-used subject picks. |
| Pipeline runner | **Built** | Walks a format's stage list and resumes where it stopped. A production runs start to finish with no one watching (first full run: Vanishing Cream, production 2, Nov 2026). |
| Writing (LLM) | **Built** | DeepSeek through OpenRouter (`openai_compat`): `deepseek-v4-pro` for writing, `v4-flash` for small jobs. Strict JSON-schema answers, one automatic repair round, and every call's price in the production log. Any OpenRouter model can be picked per stage on the Make page. The key lives in `config/secrets.env` (chmod 600, never committed). `manual` still works. See [Who writes the scripts](#who-writes-the-scripts). |
| Formats | **Sketched** | `freeform` (everything from the Make page: no description, no random tags), plus `sitcom_90s`, `parody_commercial` and `weird_short` for dice-made productions. Their tags and stage lists are a first guess. |
| Outline, screenplay, cast, voice stages | **Built** | Validated JSON; a new subject the dice ask for is saved to the World. Screenplays render as a script on the production page. Casting fills roles from the script and the World and asks only for the rest; every cast member becomes a World character. The voice stage speaks every line with Pocket TTS and makes a back-to-back dialogue draft. |
| Shot plan, render, assembly, look | **Built** | The shot plan is a writing stage: per shot a scene prompt, the recurring characters in it (with position and size), a plain-language action, the lines heard over it and a caption. See [Picture](#picture-how-a-shot-is-made). Assembly lays every line at its planned time and writes chapter markers at act breaks; the look stage is a clean 4:3 encode at -16 LUFS, no effects (see Next steps: the real look). Shots can also be `stock` footage or title `card`s (at least 2 s). |
| Video generation | **Built** | `tbc/mischief-1` through OpenRouter's `/videos` API (`[videogen]`): image to video, 3 or 5 s, 16:9, $0.005 per second at 480p (Nov 2026). It takes one image only, the first frame: no reference images (tested). Shots with recurring characters or their own sound (`clip_sound`) go to `heygen/heygen-video-1` instead (4:3, 480p, first frame, $0.02 per second, 2026-11-16), which makes the clip's own effects and on-camera voices. The Make page shows today's per-second prices, live from OpenRouter. |
| Sound stage and mix | **Built** | Effects and laughs: library first, then Openverse (matching cue words to sounds is crude, which is half the fun). Music: MIDIgen writes a fresh piece per cue in formats with `[music] source = "midigen"`, and scores the whole piece when the script has no music cue. The mix measures and levels every file, ducks the music under the voices and finishes at -16 LUFS. See [Mix](#mix). |
| Online sources | **Built** | Internet Archive (video, audio, images), Wikimedia Commons (images, audio), Openverse (audio, images). No keys or accounts. The **Find online** page searches and adds to the library; video is shrunk to 480 lines on arrival. Storage cap with a cleanup job (below). Licences are ignored on purpose: nothing here is ever shown publicly. |
| Downloads via archivist | **Planned** | `~/archivist` (added 2026-11-15) is the server's standard downloader: a paced, cookie-eating front end for yt-dlp. Anything ai_tv needs from YouTube and the other sites yt-dlp supports, such as stock footage, music or reference clips, should go through it. See [Archivist](#archivist-the-standard-downloader). |
| Voice cloning | **Built** | Pocket TTS on the CPU: every voice gets a ~15 s reference cut from the untouched original and a playable cloned test line. Model `english_2026-10` won the Nov 2026 tests (see `test_outputs/voice/`). |
| Image generation | **Built** | ComfyUI v0.19.1 + Realistic Vision 5.1 (SD 1.5) on the GTX 970: ~21 s per 640×480 image at 20 steps, ~7.6 s with the LCM speed-up (see `test_outputs/image/`). The worker starts ComfyUI on demand and stops it after 10 idle minutes. "Generate images" is on every World page (pre-filled prompt per type) and in the Library. Pins in `tools/comfyui/constraints-gtx970.txt`. |
| Image to image (OpenRouter) | **Built** (talkie shots) | `tencent/hy-image-v3.5-preview` through OpenRouter's `/api/v1/images` endpoint (not chat/completions, which returns a 404 for it). The body holds the prompt, `aspect_ratio` and the reference as `input_references`. It cost $0.024 per 1K image and took 15 s (Nov 2026). It kept Tarman's likeness from one reference photo in a new 4:3 scene. An earlier chat/completions run compared `google/gemini-2.5-flash-image`, `google/gemini-3.1-flash-lite-image`, `black-forest-labs/flux.2-pro`, `black-forest-labs/flux.2-klein-4b` and `bytedance-seed/seedream-5-0-flash`. See `test_outputs/image/2026-11-12_img2img_cheapest/` and `2026-11-12_openrouter_img2img/`. |
| Music (General MIDI) | **Built (beta)** | **MIDIgen** (beta, will change; github.com/mint-yogurt/MIDIgen) (`~/MIDIgen`, its own venv, `[tools] midigen`): an LLM writes a `midigen/1` JSON composition from a brief and a catalogue of the uploaded SoundFonts; it's validated, written as MIDI and rendered to WAV by FluidSynth 2.3.1, headless. SoundFonts are uploaded on the **SoundFonts** page. Part of the sound stage (see above). See [Music (MIDIgen)](#music-midigen). |
| Lip-sync | **Built** (job only) | `lipsync.render` worker job (`aitv/lipsync.py`): SadTalker or Wav2Lip makes a still say an audio clip, at the image's full size. It frees the GPU first (stops or `/free`s ComfyUI) and blocks if under ~3.3 GB is free. Used by newsgen's news segment (Phil the anchor). SadTalker `--still` won the 2026-11-14 test (`test_outputs/video/2026-11-14_phil_lipsync/`): ~13× real time on the GTX 970. |
| Transcription | **Built** (v2) | faster-whisper small on the CPU (`tools/whisper`), chunked, cached by file content in `transcripts`. Used by QC, the editor's word times and the footage index. |

---

## Using it

### The control room (web UI)

Open **http://&lt;server's Tailscale name or IP&gt;:8420** from any device on your Tailscale
network. Anything not coming from a Tailscale address is dropped, so it's never on the public
website.

The thin strip above the header is the **ChupTV top bar**. It links every station control room
(the director home page, newsgen, AI TV, ErsatzTV, Jellyfin). `templates/base.html` loads it
from the director (`//<host>:8430/topbar.js`, `~/chuptv_director`), so keep that script tag. If
the director is down, the page works the same, just without the strip.

Five sections, some with tabs:

| Section | Tab | What it's for |
| --- | --- | --- |
| Make | New | The Make page (below): start a production or save a draft. |
| | Drafts | Saved drafts and ideas, with their tags. "Make it" turns one into a production. |
| | Notepad | One plain scratchpad that saves as you type. Stored in `ideas/NOTEPAD.md`. |
| Productions | | Every production: its stages, tags, documents, spend, jobs, and Stop / Resume / Publish. |
| Library | Assets | Browse every image, voice, SFX, music and footage file, with thumbnails, players, tags and links to the World. |
| | World | Characters, voices, sets, brands, products, fake shows, hotlines: edit them, connect them (brand *makes* product), see their files and where they were used. |
| | Upload | Drag files in, say what they are, and optionally attach them to an existing or new character, voice, brand and so on. Shows progress. |
| | Find online | Search the Internet Archive, Commons and Openverse and add results to the library. |
| | SoundFonts | Upload `.sf2` SoundFonts for MIDIgen. Each font gets a page listing every preset, with measurements and a play button that renders a short audition. |
| Tests | | Listen to, watch or read anything in `test_outputs/`. A `.mid` shows as a piano roll that plays its rendered `.wav`. One card per folder, newest first; a `notes.md` shows above the files. |
| System | Status | Worker, GPU, RAM and disk status, what's queued or running, which local tools are set up. |
| | Jobs | Everything the worker did or is waiting on, with logs. Retry or cancel; "Retry blocked" after installing a tool. |
| | Formats | Read-only view of each format's config and prompts. |

### The Make page

- **Title, format, prompt.** Format is free text passed to the writer as typed (or left blank); ▾
  lists every format used before. Productions from this page use the `freeform` format, which adds
  no description and no random tags, so the prompt isn't steered toward "parody" or "weird".
- **Tags.** One per line, `name: value` or a word, passed to the writer as written. `type: product`,
  `hotline` or `fake show` also decides where the piece is filed on ChupTV; without it, the outline
  decides from the prompt.
- **Length** in minutes:seconds. *Exact* uses only the second box; *Range* enables the first as the
  shortest.
- **Cast, Voices, Images/footage/music, Models** are collapsed panels. Models has one box per stage
  (outline, screenplay, shot plan, editor, reviewer, publish description): type any OpenRouter model
  id or pick from ▾, with today's price per million tokens. Every model gets the same instructions and
  JSON schema; the list only offers models that support structured output and can see what the stage
  needs (stills for the editor, video for the reviewer). A pick applies to that production only and
  is checked at Start. Music isn't on it.
- **Video prices** (right-hand column): today's per-second rates for the video models in use, from
  OpenRouter's free model list, cached 10 minutes. The page works without it.
- **Spending limit** blocks the next paid call once reached; **Publish to ChupTV** files it when done.

**Uploading a new character's headshot:** Upload → category *image* → under "create a new
one" pick type *character*, type the name → role *headshot* → Upload. The character
appears in World with the picture.

**Making a voice from a recording:** Upload → category *voice*, and give it a name.
Alternatively, copy files to `library/inbox/voices/<name>/` on the server, plus an optional
`about.txt` with a one-line description. A ~15 s reference is cut straight from the
original recording, untouched and ending at a natural pause (longer references made the
clone stop mid-line). A voice with that name appears in World, and Pocket TTS speaks a test
line in it, playable on the voice's page (about 10 s). Its *transcribe* job waits as
`blocked` until Whisper is installed.

### Drop folders

Anything copied into `library/inbox/{images,voices,sfx,music,footage}/` (over SFTP, scp,
whatever) is picked up within a minute, or at once with "Scan inbox" on the Library page.
Files are moved into `library/store/`, named by content hash; duplicates are merged.

### Command line

```bash
cd ~/ai_tv && source .venv/bin/activate
aitv status                                   # machine and worker status as JSON
aitv scan                                     # pick up inbox files now
aitv add out.wav --category music --title "…" --tag midigen --meta '{…}'   # put a tool's output in the library
aitv produce parody_commercial --pin ad_type:hotline --no-run   # random tags, no run
python -m pytest tests                        # 78 tests, offline: manual LLM, no paid calls
```

---

## How it fits together

```
Make page (prompt + your tags) ─────────────┐
idea + format (random tags, yours win) ─────┴──► production ──► stages ──► final.mp4 ──► publish
                                                      │
                                                      ▼
            world database ◄──── library ◄──── uploads, inbox drops, generated files
```

- **Formats** live in `formats/<id>/`: `format.toml` (random tags, stages, default shot mix,
  look) plus `prompts/*.md`. A new show type is a new folder, not new code.
- **Random tags** (format productions only): every tag you didn't set is a weighted draw. Each
  draws from its own random stream seeded by the production's seed, so setting one never changes
  the others. Make-page productions skip this entirely.
- **Subjects**: when a commercial draws `subject: existing`, it picks a product, hotline or
  fake show already in the world, favouring the ones used least recently. The same fake
  detergent gets several ads over time without crowding out new ones.
- **Commercials are their own productions.** Shows only carry chapter markers at their act
  breaks, like a real broadcast master. Ads are never inserted into shows, ever.
- **The LLM is called at fixed stages** with short inputs (ids and one-line descriptions,
  never whole files), and every stage's output is saved, so a rerun never repeats a call.

### Audio

- **Sound effects: local first.** The sound stage uses SFX and music already in the library
  (matched on title and tags). For anything missing it searches Openverse, which also serves
  Freesound's preview MP3s with no key, and files what it downloads in the library, so it's
  local next time.
- **Music: General MIDI now, hardware later.** MIDIgen (below) composes original MIDI with an
  LLM and renders it with FluidSynth and your SoundFonts. The rack-mount ROMpler over a MIDI
  interface is still the plan for later. Every MIDIgen piece is a plain `.mid`, so it can be
  played out to hardware when that exists.
- **Voices** are cloned locally from your recordings with Pocket TTS, on the CPU, once it's
  installed.

### Music (MIDIgen)

A standalone tool in `~/MIDIgen` (full docs: `~/MIDIgen/README.md`) that the pipeline calls
through `[tools] midigen`. It never calls an LLM itself; AI TV's backends (manual, Anthropic,
DeepSeek) do the writing.

1. **SoundFonts.** Upload `.sf2` files on the SoundFonts page; they're stored in
   `library/soundfonts/`. MIDIgen reads each one's presets (bank, program, name, which keys
   have samples) and renders every preset once to measure it: attack, sustain, brightness and
   loudness. The composing LLM chooses from that, not from guesses about preset names.
   Listening notes in `~/MIDIgen/config/notes.toml` are added to the catalogue.
2. **Request.** `midigen prompt "brief" --seconds 15 --out <dir>` writes `music.request.md` and
   `music.schema.json` in the same shape as the manual writing stages. `--json` returns
   `system`/`prompt`/`schema` for `llm.complete_json`. The instructions (`midigen/prompts/system.md`)
   teach the format, MIDI timing, choosing sounds, arrangement, harmony, registers, endings and
   duration. Optional `--cue` (theme, jingle, sting, bed, credits) and `--style` (genre cards such as
   mallsoft, 80s smooth jazz) add steering, and `--context` passes on what the production knows.
3. **Validate and render.** `midigen render music.json --out <dir> --json` checks every track and
   note against the catalogue (unknown presets, silent keys, notes past the end, channel
   clashes…). It then writes `<name>.mid` and `<name>.wav`, the composition `.json` and a
   `.render.json` with levels and timings. If the composition is invalid, nothing is written and
   the exit code is 2. The errors are worded for sending back to the LLM.

4. **Library.** A finished WAV goes in with `aitv add <wav> --category music --tag midigen --meta
   '{"midigen": {...}}'`. It is ingested like any other music asset, with a waveform and a player.
   The asset page links to the `.mid` and the composition. A MIDI file isn't a library asset, because
   ingest only takes audio and video, so it stays next to its WAV.

Uploaded so far (Nov 2026): `gm.sf2`, Roland's 1996 GS set with looped samples, banks 0-9 and 9 drum kits.
That's the workhorse. Also two Roland JV-1080 sets: 69 patches each, not in General MIDI order, and
every patch a 2-second one-shot recorded at C4. They're good for stabs, bells and swells, but they
can't sustain chords. The catalogue says so, and the prompt tells the LLM.

**In the pipeline.** The sound stage calls it for every music cue when the format says
`[music] source = "midigen"` (parody commercials do). The cue card comes from the format (`cue`, and
`always_cue` for the score it adds when a script has no music), the style from the `music_style` tag,
and the context from the outline. Each piece is written `tail_s` longer than its slot and faded at the
cut. If the shots are retimed later and a piece no longer reaches its end, it's rewritten. A
composition MIDIgen rejects goes back to the LLM with the errors (up to `repairs` times); a failed
cue is tried twice and then skipped, never stopping the production. A scene gets one composed piece:
a later music beat in the same scene ("fades out with a final sting") is written into the first
beat's piece rather than becoming a second full-length song. MIDIgen refuses a piece in which no
track plays a note (patterns written but never placed with `play`) and a render that would need more
than `max_gain_db` (40) to reach its peak; both happened (productions 3 and 9) and came out as +127 dB
of noise.

Not built yet: choosing music from the library by mood, and the reuse/variant/fresh decision for
recurring shows (a sitcom keeps its theme; commercials get new music every time).

---

## Picture: how a shot is made

A generated shot takes one of two routes. Production 4 (Dr. Schweger, Nov 2026, in `keepers/`) was the
hand-run test that picked them: 71 s for about $0.85.

**Talkie route**: the shot has recurring characters, or `clip_sound` (effects, crowd noise, a line
said on camera that isn't a recorded one).

1. **First frame.** The image-to-image model (`image_model`) puts the characters on the set from their
   reference pictures, at 4:3. A shot with only `clip_sound` gets a 640×480 Stable Diffusion scene. If
   the image-to-image call fails, the characters are pasted and blended as in the silent route.
2. **Animate.** HeyGen Video 1 (`talkie_model`) animates that frame in one clip of at least
   `talkie_min_seconds`, with its own soundtrack. "Nobody speaks" is added unless `clip_sound` quotes
   a line. The soundtrack goes on the effects bus under the shot, `clip_db` down, unmeasured.

**Silent route**: everything else.

1. **Scene.** Stable Diffusion renders the shot's `prompt` at 856×480 (16:9, the video model's shape).
   Mistakes are accepted. It never contains the recurring characters.
2. **Characters.** Each World character in the shot's `characters` with a reference picture is cut out
   (rembg, local, cached in `library/store/derived/`) and pasted in at its position (left, center,
   right) and size (closeup, waist, full), inside the 4:3 middle. A waist-up photo is always anchored
   below the frame so its cut edge never shows.
3. **Blend.** One img2img pass over the whole frame at `blend_denoise` (0.25) matches light and grain.
   Above about 0.35 the face drifts. Steps 1 to 3 are local and free.
4. **Animate.** mischief-1 animates the frame from the shot's `action`, followed by `prompt_suffix`
   and the production's own style and era tags; no era is fixed in the prompt. A shot longer than 5 s is
   continued from the last frame of the piece before (up to `max_segments`). The 4:3 middle is
   cropped out; nothing is slowed down; a short shot holds its last frame.
5. **Content filter.** The service refuses some words ("zombie") and some images. A refused prompt is
   retried once with `safe_prompt`; a refused continuation frame is retried from halfway through the
   piece before, then from the shot's own frame. A shot with no clip at all gets a camera move.

The plan never uses a character's portrait as a shot by itself, and only characters with a reference
picture can be pasted; anyone else is described in the scene prompt. A `stock` shot whose footage
can't be found (library, then the Internet Archive) is generated instead when video generation is on.

## Feedback loops: abstract and fractal visuals

An experiment that works, with more experiments to come. It might go into the pipeline later, for
example as interstitials, idents or background visuals. Stable Diffusion draws one image from a
prompt, then that image is fed back through img2img with the same prompt, over and over; every
round is one frame. Each redraw adds its own mistakes and the next round builds on them, so the
picture drifts away from the prompt into SD's own artifacts. It all runs locally on ComfyUI and
costs nothing.

**The zoom-in method is the one that works.** Before each round, crop the middle of the last frame
by 3% and scale it back up to full size, then redraw it at denoise 0.65. The constant push forces SD
to invent new detail every round, so the picture never settles. It turns very abstract and fractal:
"underwater shopping mall nostalgia" went from a mall atrium, to neon shopfronts with fake lettering,
to mirrored reflections, and then to symmetric neon kaleidoscopes and mandalas.

| Run | Settings | Result |
|---|---|---|
| `test_outputs/image/2026-11-13_sd_feedback_loop/` | 640×480, denoise 0.45, no zoom, 40 frames at 4 fps | Gentle drift: colours oversaturate and odd things appear (a snorkeller). |
| `test_outputs/image/2026-11-13_sd_feedback_mallsoft/` (in `keepers/`) | 384×288, 0.55, no zoom, 360 frames at 12 fps | Settles into one endless repeating window grid and stays there. |
| `test_outputs/image/2026-11-13_sd_feedback_underwater_mall/` | 384×288, 0.65, **3% zoom per round**, 360 frames at 12 fps | Fully abstract, fractal and kaleidoscopic. The keeper method. |

Each folder has the `run.py` that made it and a `notes.md` with the full settings. The first frame
uses the `final` preset; every round after it uses `draft` (LCM, 6 steps). On the GTX 970 a round
takes about 3 s at 384×288 (about 8 s at 640×480), so a 30 s clip at 12 fps (360 frames) takes about
19 minutes. The prompt is never fully lost, but nothing literal survives (the "underwater" came out
as a pool and reflections). Add "people, person, nude" to the negative prompt: the 0.45 run grew a
topless snorkeller.

Still to try: other prompts, the zoom amount, rotation or sideways drift instead of a straight
zoom, changing the prompt partway through, and running the output through retrovision.

## Titles

Cards and captions are drawn with Pillow (`aitv/titles.py`), not FFmpeg's drawtext: every line is
centred on its own, wrapped by pixel width inside the middle 80% with even line lengths, and shrunk
until it fits. A card's first line is the title (shrunk a little to stay on one line before it
wraps); later lines are set smaller beneath it.

- **Styles.** Eight presets built from the fonts on this machine (URW base 35, Lato): `arthouse_serif`,
  `hairline`, `blockbuster`, `hard_sell`, `network_promo`, `warm_seventies`, `bulletin`,
  `luxury_script`. Each sets the font, case, letter spacing, outline, shadow or glow, colour, how
  captions stay readable (shadow or a dark band) and fade times. The planner picks one per production
  (`title_style` in the plan) to suit its tone, so a piece is consistent and the next one differs.
  Add a preset to `STYLES` and the planner sees its one-line `hint`.
- **Cards** are never on a flat colour. The lettering fades in and out over the nearest other shot's
  picture (the one before, else the one after), blurred and darkened; with no picture in the piece,
  over a gradient from the style's colour. A card carries no spoken lines except its own reading
  (the plan is sent back otherwise), and the editor's titles never go over one.
- **Direction labels are stripped** from card text, captions and the editor's titles ("logo with
  tagline: ..." becomes just the tagline), right after the screenplay and the plan are written
  (`titles.clean_text`).
- **Captions** are a transparent layer over the shot, faded in and out with an alpha fade.
- **Read aloud.** With `[voice] read_cards = true` in a format (ads have it), a screenplay title card in
  a scene with no spoken lines is read by the script's voiceover, else the World's Announcer, and the
  planner is told to put that line over the card. Captions the planner adds are not read: they're
  written after the voices.
- **Font archive: `library/fonts/`.** Every font downloaded for station graphics (promos, idents,
  title cards) is kept there for reuse, listed in `library/fonts/FONTS.md` with its source and where
  it was used. Each station promo gets its own font. First in: **Creepster**, from the Cereal Killers
  promo; expect to use it a lot.
- Bump `titles.VERSION` after changing the drawing, so cached cards and captioned clips are redrawn
  (re-rendering never re-buys video: the generated pieces are cached separately).

## Mix

Every voice line, effect and music file is measured (EBU R128) and brought to `voice_lufs` (-20)
first. TTS lines and downloaded effects come at any level. The music and effect buses then sit
`music_db` and `sfx_db` under the voices. The music is ducked by a sidechain compressor keyed on the
voices (`[mix.duck]`), and a gentle limiter keeps one loud transient from holding the whole programme
down. The look stage sets the final loudness (-16 LUFS, true peak -1.5) in two passes, so the balance
isn't changed. Measured on Vanishing Cream: music about 15 dB under the voices while anyone talks,
back up in the gaps, final -15.8 LUFS.

The LLM editor's `expr` duck is never shallower than `[mix] min_duck_db` (-10). After either duck,
the render measures every line against the bed under it (RMS over the line, as QC does) and turns
the bed down further for any line less than `[qc] min_voice_margin_db` + 1 dB (7) over it, ramped so
it's already down when the line starts. This also covers effects and talkie soundtracks, which the
sidechain never ducked. QC still checks the result: a line under the floor is a blocking issue,
and blocking issues hold publishing.

Prices and phone numbers are written out for the TTS (`tts.spoken`): `$3.99` is said "three
ninety-nine", `1-800-555-DROOL` "one, eight hundred, five five five, drool". The script and the
titles keep the figures.

## Who writes the scripts

DeepSeek through OpenRouter (Nov 2026). The pipeline doesn't depend on any one model: set
`[llm] backend` in `config/aitv.toml`:

| Backend | What happens | Cost |
| --- | --- | --- |
| `manual` | Each writing stage saves its request (instructions + the exact JSON shape) and the production waits. The production page shows the request with a box to paste the answer; it's checked against the shape before the run continues. You can also save the answer to `productions/<id>/writing/<stage>.json`. | Free |
| `anthropic` | An Anthropic API key (`ANTHROPIC_API_KEY`) | Per token |
| `openai_compat` (in use) | Any OpenAI-compatible API: DeepSeek, OpenRouter, a local server | Per token, cheap |

What the first full run cost (Vanishing Cream, a 30 s ad): outline $0.004, screenplay $0.017, shot
plan $0.017, music $0.026, video $0.165 (9 pieces). About 23 cents an ad, three quarters of it video.

Whichever writes it, every answer is saved in `productions/<id>/writing/`, so a stage is never
asked twice; "Rewrite" on the production page throws one away and asks again.

**Models per stage.** `[llm] model` / `small_model` are the defaults (DeepSeek v4 Pro / Flash);
`[llm.stage_models]` overrides them by stage in config (today: the editor runs Claude Sonnet 5.5,
the reviewer Gemini 3.8 Flash), and the Make page's Models panel overrides that for one
production. Replies are capped at each model's maximum output.

**There is no Claude Code / Claude subscription backend, on purpose.** Anthropic's Consumer
Terms don't allow scripted access to a consumer plan except through an API key or where
explicitly permitted, so the pipeline never calls `claude -p`. Claude models are used through
OpenRouter with an API key, or by hand through `manual`.

## This machine

i5-4690K (4 cores), 16 GB RAM shared with the website, GeForce GTX 970 (4 GB, Maxwell),
Debian 12. That shapes everything:

- **Local video generation is out.** The smallest serious model needs about 8 GB of VRAM.
  Video comes from mischief-1 over OpenRouter (cents a clip); with `[videogen] enabled = false`
  shots fall back to FFmpeg camera moves on stills. Stock footage and title cards are local.
- **PyTorch is pinned.** Maxwell support ends after PyTorch 2.14. Each local model gets its
  own environment under `tools/` (see `tools/README.md`).
- **The worker runs one job at a time**, at nice 10 with idle I/O priority and a soft 10 GB
  memory ceiling, so the website stays responsive.

---

## Operations

Two systemd services run the web UI and the worker. One-time install (until then,
port 8420 answers with a 502):

```bash
sudo cp ~/ai_tv/deploy/aitv-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aitv-web aitv-worker
```

Day to day:

```bash
systemctl is-active aitv-web aitv-worker
sudo systemctl restart aitv-web                  # after changing web code (the worker reloads itself)
journalctl -u aitv-worker -f                     # worker log
```

- **The worker reloads itself.** Between jobs, once `aitv/**/*.py` or `config/aitv.toml` has stopped
  changing for `inbox_settle_seconds`, it checks that the new code and config load in a fresh
  interpreter, then restarts in place with the same PID. If they don't load, it keeps running the old
  code and logs why. The web app doesn't reload itself.
- **Checks before a run.** Before its first stage, a run checks for:
  - stages that aren't built yet;
  - the API keys the writing and video steps need;
  - MIDIgen, when the format scores with it.

  Any problem blocks the run before money is spent. The reason shows on the production page; fix
  it and press Resume. A look that isn't built doesn't block: the look stage logs it and finishes
  with `clean`.
- **Stop.** The production page's Stop button (Cancel when the production isn't running) takes
  effect at the next stage, and before the next round of paid video calls is submitted. It never
  stops while submitted video jobs are still out, so nothing is paid for twice. The job ends as
  cancelled; Resume continues from the stopped stage.
- **Clear old errors** on the dashboard deletes failed and cancelled jobs, and blocked production
  runs; the production keeps its own status and note. A new run of a production also deletes that
  production's earlier blocked and cancelled runs. Jobs blocked on a missing tool (Whisper) stay,
  so they can be retried once it's installed.

Things outside this folder, and how to undo each:

| What | State | Undo |
| --- | --- | --- |
| Caddy site on :8420 | Added at the end of `/etc/caddy/Caddyfile`; the previous file is `/etc/caddy/Caddyfile.pre-aitv` | `sudo cp /etc/caddy/Caddyfile.pre-aitv /etc/caddy/Caddyfile && sudo systemctl reload caddy` |
| Web service | Installed by you, with the commands above | `sudo systemctl disable --now aitv-web && sudo rm /etc/systemd/system/aitv-web.service` |
| Worker service | Installed by you, with the commands above | `sudo systemctl disable --now aitv-worker && sudo rm /etc/systemd/system/aitv-worker.service` |

## Layout

```
aitv/            the Python package: db, ingest, jobs, worker, rolls, llm, prices, pipeline, web UI
config/          aitv.toml: ports, paths, LLM backend, tool locations
                 secrets.env: API keys                                            (not in git)
formats/         one folder per show type: format.toml + prompts/
deploy/          systemd units and the Caddy snippet
docs/            design notes (pipeline v2 plan)
tests/           pytest suite
keepers/         saved pieces: scripts and notes in git, the media not            (media not in git)
tools/           per-model environments (Pocket TTS, Whisper, ComfyUI), not in git
library/         inbox/ drop folders, store/ (content-addressed files), manual/,
                 soundfonts/ (uploaded .sf2 for MIDIgen),
                 fonts/ (font archive for station graphics, see FONTS.md)         (not in git)
ideas/           NOTEPAD.md                                                       (not in git)
productions/     per-production working files and final.mp4 (work in progress)    (not in git)
test_outputs/    results of tool and pipeline tests, shown on the Tests page      (not in git)
data/            aitv.sqlite3                                                     (not in git)
```

## Online downloads and storage

Downloads from the internet are capped at `[online] cap_gb` (20 GB). Every
`cull_every_hours` (6) the worker runs a cleanup that deletes online downloads that **no
production has used and no World entity links to**: anything older than `cull_after_days`
(30), then the oldest first while still over the cap. Anything a production used is never
deleted. A culled asset keeps its library row and source URL and shows **Fetch it again** on
its page. **Clean up now** on the Find online page runs it on demand. Other limits:
`max_download_mb` (150) per file and `max_video_minutes` (20).

### Archivist: the standard downloader

`~/archivist` (its own repo and venv; docs in `~/archivist/README.md`) is the server's one front end
for yt-dlp. Every ChupTV program that downloads video goes through it, so the sites see one paced
client instead of several bursts. It handles:
- **Cookies.** It eats the logged-in YouTube cookies that `~/cookie_shipper` ships from the
  workstation. Each run gets a private copy, and the YouTube JS challenge is solved.
- **Pacing per site**, with a daily cap.
- **Errors**, sorted into rate-limited, dead, rejected cookies and transient (retried).

```
archivist get URL --dest DIR      # download now (waits its turn)
archivist info URL                # metadata only
archivist list URL --limit N      # a channel or playlist listing
archivist status                  # queue, sites, cookies, alerts
```

From Python: `import archivist`, then `fetch_now`, `submit`, `info`, `listing`, `record`.

**For ai_tv:** `aitv/online.py` doesn't use it yet; the Internet Archive, Commons and Openverse are
plain HTTP. The plan is to send yt-dlp-shaped downloads through archivist: stock footage, old ads,
music and reference clips from YouTube and anywhere else yt-dlp reaches. Then add more kinds of
sources to archivist itself as the pipelines need them. A downloaded file still goes into the library
like any other online asset, with its source URL. Don't call yt-dlp directly from ai_tv.

## Finished media and keepers

- **Finished pieces are published, not kept here** (`publish` stage, `aitv/publish.py`, since
  2026-11-15). A production marked to publish (the make form's checkbox, or Publish on its page)
  is copied into the media library by kind (`[publish] places`):

  | Piece | Goes to | Catalog kind |
  |---|---|---|
  | product or hotline (parody commercial, or Make with that type) | `/mnt/media/commercials/<brand>/<title>.mp4` | commercial (brand series) |
  | fake show (parody commercial, or Make with that type) | `/mnt/media/chuptv/promos/trailers/` | promo |
  | anything else: weird short, sitcom, untyped Make pieces | `/mnt/media/chuptv/shows/ai_tv/` | generated |

  Then the director's own `chuptv catalog publish` scans it into chuptv.db with a profile (blurb,
  summary, era, brand, product) and tags from its vocabulary, written by one small-tier LLM call
  (≈ $0.0002). Republishing reuses the same file and catalog code. The published copy is the keeper;
  nothing new goes in `keepers/`. `productions/` stays a workshop that can be cleaned out.
- **Keep forever: production 1, "HamRest: Sleep Like You Just Ate"** (`productions/0001/`),
  both `final.mp4` and `HamRest v1 (no sound effects).mp4`, the original cut before sound existed.
  The first thing the station ever made, and proper cursed kino. Any cleanup of test material
  (test images, `test_outputs/`, old productions) skips it. `productions/0001/KEEP.md` says
  the same thing next to the files.
- **Keep forever: everything in `keepers/`.** Saved test pieces the user loved, starting with
  HeyGen Video 1's takes on "The Shart in Mart" trailer and "Vanishing Cream". Same rules as HamRest; `keepers/KEEP.md` lists them.

## Next steps

1. ~~Voice cloning~~ done. ~~Image generation~~ done (known issue: SD 1.5 draws modern flat-screen TVs in 1980s rooms).
2. ~~Outline and screenplay stages~~ done, written manually for now.
3. ~~Casting and voice stages~~ done.
4. ~~Shot plan, render, assembly, look~~ done (first pass). ~~First production~~: HamRest, a 30-second ad.
5. ~~Sound cues and online sources~~ done (first pass).
6. Generated shot stills go into the World library so later productions can reuse them.
7. Swapping a single sound or stock clip from the production page (today: delete the pick and run again from sound).
8. ~~Mixing~~ done (see [Mix](#mix)). Levels may still want tuning by ear.
9. **The real look.** The `vhs_late_night` stand-in is gone; the look stage is a clean encode
   (preset `clean`) until this is done. The plan is to fork and adapt proper, more authentic tools as the last step of the video pipeline: an NTSC
   signal filter, real interlacing, tape artefacts. Pick candidates, test them on HamRest, keep
   what looks genuinely right.
10. **Feedback-loop visuals, maybe.** More experiments first, then perhaps a pipeline stage for
    abstract filler (see [Feedback loops](#feedback-loops-abstract-and-fractal-visuals)).
11. **Casting by tags (needs more thinking).** Today a speaking role with no voice stops the run at
    casting, and actors with reference pictures are only used when the pitch names them. The
    screenwriter must never be handed a list of actors (that poisoned production 6 with SpacePirate
    and Grandpa Earl). Ideas:
    - A casting step that orchestrates actors: picks existing ones, or generates new ones with
      Stable Diffusion.
    - Tagged voice and actor libraries (male, muscular, old, raspy...).
    - Or the writer gets the list of tags (never names) and tags each character it writes
      ("male, muscular, ..."); casting then picks the voice and actor whose tags fit best.
12. **Sound that follows the screenwriter (to discuss).** Scoring every piece with MIDIgen would fill
    the silence, but stock sounds, room tone and noise matter too, and the script's own sound
    directions should lead.
13. ~~Spend tracking~~ done (Pipeline v2 part A; live prices on the Make page).
14. **A prompt per shot from the screenwriter (to discuss).** Today the screenwriter writes beats,
    and a separate plan call turns them into the image prompt, `action` and `clip_sound`; what's
    written and what's generated don't match.

## Pipeline v2: an editor, eyes and ears (built 2026-11-15; tests 1–9 passed, test 10 pending)

Full plan, prices and the tests still to run: [`docs/pipeline-v2-plan.md`](docs/pipeline-v2-plan.md).

**Why.** The hand-made ChupTV promos beat the pipeline's productions because of editing and checking, not
more generation:
- real footage;
- an edit made for each piece, cut on the music and timed to the word;
- every build watched, listened to and fixed.

The pipeline used to make one pass and never look at its result. v2 adds that loop. Every format's stage
list is now `... render, edit, assemble, look, qc, review`. With the shipped config only QC does
anything new (local, free): it reports, and if the final's peaks are over the look's target it finishes
it again with a peak limiter. The rest waits for its switch in `config/aitv.toml`. A saved edit or review
is tied to what it was made from: after a re-render it's asked for again; with nothing changed it's kept.

| Part | What | Code | Switch (shipped) | Cost |
|---|---|---|---|---|
| A | Every paid call is a row in `spend` (stage, model, tokens or seconds, `usage.cost`), summed into `productions.spent_usd`. A production's `budget_usd` blocks the next paid call once it's spent. Shown per stage on the production page. | `spend.py`, `StageContext.paid` | always on | free |
| B | `qc` stage after the look: each line's voice against the bed under it, Whisper on the final against the script, loudness (fixed by a second look pass, with a peak limiter if needed), unplanned cuts and flash frames, black, frozen picture and dead air the edit didn't ask for, title reading time and safe area, a contact sheet of every cut. Never stops a run. | `qc.py`, `editing.stage_qc` | `[qc] enabled = true` | free |
| C | The edit decision list (EDL): sources, picture events (in, dur, speed, camera move, grade, cut/dissolve/flash/dip), titles over the picture (fade, slam, slide, typewriter), audio on three buses, the mix (today's sidechain duck or the promos' subtle dip), chapters. A renderer that caches segments. | `edl.py` | `[edit] mode = "off"` | free |
| D | `edit` stage: an editor model sees a contact sheet of every clip, the script, each line's word times, the sounds, the music's beat grid and today's cut as an EDL, and writes the whole EDL. One repair round; an EDL that still doesn't validate falls back to today's cut. | `editing.stage_edit` | `mode = "llm"`; model `[llm.stage_models] edit` | ≈ $0.06–0.14 |
| E | `review` stage: a 360p copy of the final goes to a video model as base64 `video_url`; it scores the piece and writes notes with fixes as EDL patches. Patches that validate are applied, then assemble, look and QC run again, up to `max_rounds`. Every round's video, notes and EDL are kept. | `editing.stage_review` | `[review] enabled = false`; model `[llm.stage_models] review` | ≈ $0.01 a round |
| F | A model per stage: `[llm.stage_models]` overrides the tier model by stage name (`music_*` → `music`, `review_*` → `review`), and a Make-page pick overrides that for one production (never music). `llm.py` sends images and video. | `pipeline.stage_model`, `llm.py` | outline/screenplay lines commented out | ≈ $0.10 a call |
| G | Footage index: scenes with thumbnails, Whisper words, FTS5 search; a `footage` shot's prompt is a quoted line ("more power") or what's shown, cut to start on the first word with its own sound. The worker indexes one file per job and stops while someone is watching ChupTV. | `footage.py`, worker `footage.index` | `[footage] enabled = false`, `roots = []` | free (CPU) |

**Transcripts** (`transcribe.py`, `tools/whisper/transcribe.py`): faster-whisper small on the CPU in 2-minute
chunks, cached in the `transcripts` table by the director's file fingerprint, so either project can read
them (decision 5 left open: the table is shaped for sharing, nothing is shared yet).

**Turning it on, in order:**
1. `[edit] mode = "auto"`: the EDL that equals today's cut (test 3 checks it matches).
2. `[review] enabled = true` with mode `auto`: the reviewer can fix levels, trims and titles.
3. `[edit] mode = "llm"`: the editor.
4. `[footage] enabled = true` and add a root (one show) to index.
5. Uncomment the Sonnet lines under `[llm.stage_models]` for the writing.

**Not built:** the reviewer can't buy new clips. Shots it wants remade are flagged on the production page,
whatever `max_regen_usd` says.

**Tests:** `tests/test_v2.py` (synthetic media, fake models, no paid calls). Tests 1–9 on real productions ran on
2026-11-15 and are written up in `test_outputs/video/2026-11-15_pipeline_v2_tests/RESULTS.md`, with which reviewer
hears best and Sonnet against DeepSeek. Test 10 (a full ad, which generates video) waits for the user's OK.

**Saved edits and reviews** are tied to their inputs. Changing a shot's length in a patch ripples: the sounds and
titles after it move with it. `system.someone_watching()` (an `ersatztv-channel` under ErsatzTV) stays True about
90 s after the last viewer leaves.

## Known problems, to fix later

- **Ads run over their slot.** Vanishing Cream came out at 37.4 s for a 30 s slot. The spoken lines
  alone were 31 s, so the screenplay is too wordy for its length, and the 2 s minimum for title cards
  adds more. Probably a word budget per length in the screenplay prompt, and a check after the
  voice stage that sends an over-long script back for a trim. Still happening: Toxic Horror Droplets
  (production 9, 2026-11-16) came out at 43.6 s for 30 s. The log warned twice ("spoken lines alone
  run 37.2s", "the shots add up to 40.8s") and carried on; its last voiceover alone was 12.4 s.
  **Needs more research before building anything:** whether to enforce it at all (a hard limit,
  a tolerance, or just a warning you see), where (the screenplay's word count, a trim pass after the
  voice stage, or the editor cutting lines), and what it costs in paid rewrites.
- **Reasoning "low" barely helps.** DeepSeek v4-pro still thought for about 7,500 tokens on the shot
  plan and the music with `reasoning = "low"` (it costs cents, but it's slow: about 3 minutes a
  call). Try a lower cap, `v4-flash`, or no reasoning for music, and compare.
- **One reference picture per character.** The web UI takes one, so every Tarman shot uses the
  same waist-up pose and a "full" figure can't be shown. Characters need several references
  (front, side, full body), and the planner should pick the closest one per shot.
- **New speaking characters stop at casting.** A role the screenplay invents (MAN in The Shart in
  Mart) has no voice and waits for you. Auto-casting the least-used voice would let it run unattended.
- **No trailer format.** Trailers go through the ad format as a `fake_show`; its prompts are written
  for ads.

## Parked: build later

- **More Whisper uses.** It's installed (QC, the editor, the footage index). Still to use it for:
  regenerating lines with dropped or garbled words, subtitles with real timings, picking cleaner
  voice references, and labelling what each recording says.
- **Trained voice models (RVC)** if a voice ever has to be exact; needs a few minutes of audio.
- **Music from the rack ROMpler** over a MIDI interface (see Audio above). MIDIgen's `.mid` files
  are the input for that.
