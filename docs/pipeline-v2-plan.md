# Pipeline v2: an editor, eyes and ears (plan, 2026-11-15)

> **Built 2026-11-15** with the default decisions: Sonnet 5.5 edits, Gemini 3.5 Flash Lite reviews,
> Sonnet for writing is ready but commented out, a $0 regeneration cap, and a transcript cache shaped to be
> shared with the director (nothing shared yet). What's where, and how to switch each part on: README →
> "Pipeline v2". Unit tests: `tests/test_v2.py`. **Tests 1–9 ran on 2026-11-15** ($0.28; 7 bugs found and
> fixed): see `test_outputs/video/2026-11-15_pipeline_v2_tests/RESULTS.md`. Test 10 (it generates video) waits for
> approval. One gap: the reviewer flags shots for new clips but can't buy them, whatever `max_regen_usd` says.

**Why.** The hand-made ChupTV promos came out far better than the pipeline's productions. They
didn't use more generation; they used **editing and checking**:
- real footage;
- an edit made for each piece, cut on the music and timed to the word;
- every build checked by eye and ear (Whisper, scene cuts, loudness, edge frames), then fixed and rebuilt.

Today the pipeline makes one pass (write → generate → template assembly) and nothing ever watches the
result. This plan adds the missing loop. The parts run in build order. Each part works on its own,
and each can be switched off in `config/aitv.toml`, which brings back today's behaviour.

## Prices (OpenRouter, read from `/api/v1/models` on 2026-11-15, $ per million tokens in / out)

| Model | In | Out | Takes | Fit |
|---|---|---|---|---|
| `google/gemini-3.5-flash-lite` | 0.30 | 2.50 | text, image, **video, audio** | **Reviewer (default)**: watches the mp4; whether it hears the audio track is unconfirmed (test 6) |
| `qwen/qwen3.8-omni-flash` | 0.15 | 0.47 | text, image, video, audio | Reviewer, cheaper alternative |
| `google/gemini-3.8-flash` | 0.825 | 4.125 | text, image, video, audio | Reviewer (stronger) / editor |
| `google/gemini-3.1-pro-preview` | 2.00 | 12.00 | text, image, video, audio | Reviewer, top end |
| `anthropic/claude-sonnet-5.5` | 2.00 | 10.00 | text, image | **Editor (default)** and writer |
| `anthropic/claude-opus-5.5` | 4.00 | 20.00 | text, image | Writer, top end |
| `openai/gpt-5.6-terra` | 2.00 | 12.00 | text, image | Editor/writer alternative |
| `deepseek/deepseek-v4-pro` (today) | 0.90 | 1.50 | text | Writer today; stays for plan and music |

**Video input** (OpenRouter docs): a `{"type": "video_url", "video_url": {"url": "data:video/mp4;base64,..."}}`
part in chat/completions; mp4, mov, webm. Gemini through AI Studio only accepts YouTube links as
URLs, so we always send **base64**. A 360p review proxy of a 30 s piece is about 1–2 MB.

**Token estimates** (to be measured in the tests):
- Gemini bills video at about 258 tokens per frame at 1 fps, plus 32 per second of audio, so
  roughly **300 tokens per second**. That's about 9k tokens for 30 s and 36k for 2 min.
- Our real writing calls (production 2's log) are about 2.5k tokens in and 8–11k out, mostly thinking.
- A contact-sheet image costs about 1–1.5k tokens.

| Call | Size | Cost per call |
|---|---|---|
| Review, 30 s piece, Flash Lite 3.5 | 9k in, 2k out | **≈ $0.008** |
| Review, 30 s piece, Gemini 3.8 Flash | 9k in, 2k out | ≈ $0.016 |
| Review, 2 min short, Flash Lite 3.5 | 36k in, 3k out | ≈ $0.018 |
| Edit, Sonnet 5.5 (≈20 contact sheets) | 30k in, 8k out | **≈ $0.14** |
| Edit, Gemini 3.8 Flash | 30k in, 8k out | ≈ $0.06 |
| Writing call, Sonnet 5.5 | 2.5k in, 10k out | ≈ $0.105 |
| Writing call, Opus 5.5 | 2.5k in, 10k out | ≈ $0.21 |
| Writing call, DeepSeek v4 pro (today) | | $0.017–0.077 (logged) |

**Per 30 s ad.** Today it costs about $0.23, three quarters of that video.

| Setup | Extra | Total |
|---|---|---|
| Recommended: Sonnet 5.5 writes the outline and screenplay and edits; Flash Lite reviews, 2 rounds | +$0.38 | **≈ $0.60** |
| Cheap: DeepSeek writing, Gemini 3.8 Flash editing, Flash Lite review | +$0.08 | ≈ $0.31 |

Re-editing never buys video again. Regenerating a shot is the only thing that costs video money, and it's
capped (part A).

---

## Part A: Spend tracking and caps (needed first)

Every paid call goes into a new `spend` table: production, stage, model, tokens or seconds, and
cost from OpenRouter's `usage.cost`, or the per-second price for video. It also feeds
`productions.spent_usd`.
- **Caps:**
  - `budget_usd` per production: a stage that would go over it blocks.
  - `[review] max_regen_usd`, the money the review loop may spend re-generating shots. **Default
    0:** the loop can only re-edit, and a shot it wants regenerated is flagged for you.
- The production page shows spend per stage.

This closes README next step 13.

## Part B: QC, automatic checks after every render (local, free)

New stage `qc` after `assemble`. It runs these checks on the final mix and its stems:
1. **Lines heard.**
   - faster-whisper small (`tools/whisper`, CPU int8, in 2-minute chunks; whole-file runs came back empty
     on some files) transcribes the final, and each screenplay line is fuzzy-matched in its window.
   - Separately, the voice stem is measured against everything else in each line's window. A
     margin under the threshold means the line is masked. This works even where Whisper can't
     read a voice: puppets, accents, made-up words.
2. **Loudness:** integrated loudness, true peak and loudness range against the targets. A wrong
   level is fixed in code (re-run the look with a corrected gain or limiter), with no LLM.
3. **Cuts:** scene detection at `-v info` against the edit's planned cuts. It flags unplanned
   cuts (a source's own edit inside a shot) and flash shots shorter than 3 frames that weren't asked for.
4. **Dead picture and sound:** `blackdetect`, `freezedetect` (held last frames, frozen clips) and
   `silencedetect` (dead air).
5. **Titles:**
   - every card and caption box sits inside the 4:3 safe area (`titles.py` knows the boxes);
   - every one is on screen long enough to read: about 0.3 s per word, at least 1.5 s.
6. **Picture:** a frame on each side of every cut, saved as a contact sheet for the editor and
   reviewer, and for you on the production page.

The output is `qc.json`: pass or fail per check, and issues with a time, a category and a hint.
Mechanical fixes happen in code. Content problems (a masked line, a bad cut) go to the edit stage.
A QC failure never stops a run: the piece finishes and the issues show on the production page.

## Part C: The edit decision list (EDL) and its renderer (local)

The heart of the change. `build.py` from the promos becomes a general renderer that reads a JSON
**EDL**, and `assemble` renders that EDL instead of laying clips end to end.
- **Picture events:**
  - a source (rendered clip, library footage, still, card), with in and out points and the start
    on the timeline;
  - speed (setpts), a push-in or pan (zoompan), a grade preset;
  - the transition in (cut, dissolve, flash, dip).
- **Title events:** text, `titles.py` style, the animation (fade, slam with bounce, typewriter,
  slide) and position.
- **Audio events:**
  - voice lines (by line id) with start and gain;
  - effects and music with in and out, start, fades and a gain envelope;
  - the clip's own sound (on or off, gain).
- **Mix settings:** bed level and a subtle duck (depth and ramps), measured as on the promos.

The renderer's rules:
- It is deterministic and validated before rendering: every source exists, in and out points fit
  the source, there are no gaps, and the length is within the format's bounds.
- It caches segments by key, so a one-event change re-renders one segment, not everything.
- It ends with the same two-pass loudness and limiter as today.

**Today's assembly stays untouched.** `stage_assemble` as it is now remains the `[edit] mode = "off"` path
(the default), so nothing changes until you switch the new path on. With `mode = "auto"`, code builds an EDL from
`plan + render` that matches today's assembly; with `mode = "llm"`, part D writes it. To match, the EDL also
carries the chapter markers at act breaks, the card-reader voice placement, and today's sidechain duck as an
option next to the volume-expression duck. Beat times come from
MIDIgen's composition (bpm, bars), so cuts can snap to the music.

## Part D: The edit stage, an LLM editor (paid, ≈ $0.06–0.14)

New stage `edit` between `render` and `assemble`, with `[edit] mode = "llm"`. The editor is
briefed with:
- the screenplay and the format's editing notes (pacing, house style), as a new `prompts/edit.md`
  per format;
- the voice lines, with word timestamps from Whisper on the TTS takes (exact, since we have each line on its own);
- a **contact sheet per rendered clip** (6 frames with times) and per footage candidate (part G);
- the sound and music list with lengths, the music's beat grid, and the title styles;
- the default EDL from part C, as a starting point.

It answers with a full EDL: what to trim, reorder, hold, speed, push, where the cuts land on the
beat, where titles slam in. A schema check and one repair round apply, as with today's writing stages.
- **Default model:** `anthropic/claude-sonnet-5.5`.
- **Alternative:** `google/gemini-3.8-flash`.

The editor can't buy new shots. It can only ask for one, and that goes through part E's regen cap.

## Part E: The review stage, a model watches the result (paid, ≈ $0.01–0.02 a round)

New stage `review` after `qc`:
1. A 360p review proxy of the final (with sound) is sent as base64 video to the reviewer
   (default `google/gemini-3.5-flash-lite`), with the screenplay, the EDL and `qc.json`.
2. It returns structured notes:
   - a score per criterion: lines audible, titles readable, pacing, cut quality, does the gag
     land, matches the script, looks finished;
   - a list of problems, each with a time, what's wrong, and a **fix written as EDL patch
     operations** (move a cut, trim, raise a line, swap a sound, extend a title).
3. Patches are validated and applied, then the piece is re-assembled and QC runs again. That's
   one round. It stops after `max_rounds` (default 2), or when nothing is marked blocking.
4. A note that needs a new shot (regeneration) is only acted on inside `max_regen_usd` (default
   0, so it's flagged for you).

**If the reviewer turns out not to hear the audio** (test 6 checks), QC's stem margins and the
Whisper transcript go into its brief as text. Part B already covers whether lines are heard.

Every round's video, notes and EDL are kept, so you can compare round 0 and round 2 on the
production page.

**Limits to know:** Gemini samples video at about 1 frame per second, so it can miss one-frame
problems and very fast cuts. QC (part B) covers those frame-accurately; the reviewer is for
"does it work", not frame checks.

## Part F: Better models for writing (paid, per-stage choice)

- `[llm.stage_models]` maps a stage to a model, so the stronger model goes only where it pays.
  - Recommended: `outline` and `screenplay` on `anthropic/claude-sonnet-5.5`, which is where the
    concept and the comedy come from.
  - `plan` and `music_*` stay on DeepSeek v4 pro.
- An optional Opus 5.5 for the screenplay at ≈ $0.21 a call.
- `llm.py`'s `openai_compat` backend already speaks OpenRouter and logs cost. It gains an image
  and video content-part helper (contact sheets for part D, video for part E), and the per-stage
  model lookup.

## Part G: Library footage and sounds as real sources (local, free)

The promos worked because of real footage, found by what's said and shown in it.
1. **A footage index.** A background worker job, CPU, low priority, that skips while someone is
   watching ChupTV. For each source it records:
   - the scenes (scene detection), each with a keyframe thumbnail;
   - a chunked Whisper transcript with word times;
   - the tags.

   It's stored in a new `footage` table, plus an FTS5 index on the transcripts.
   - **Sources:** ai_tv's library video assets and Internet Archive downloads.
   - **Opt-in** folders from the ChupTV media library, by path in `[footage] roots`. Read only;
     the director's database is never written.
2. **Footage shots in the plan.** A shot can be `footage` with a query: a line said ("more
   power"), a description ("90s sitcom living room, wide") or a show.
   - Code finds the top candidates: transcript text matches first, tags second.
   - The editor (part D) sees their contact sheets and picks the in and out points.
   - In-points are word-timed from the Whisper words.
3. **Sounds.** Effects and ambiences already come from the library first, then Openverse. The
   editor now sees each candidate's length and title and places them itself (layered field
   recordings, room tone), instead of one sound per cue word.

**Open question for you:** the director plans Whisper as its catalog's `transcribe` stage (run as ai_tv
worker jobs, saved in `chuptv.db`'s `texts` table). A separate footage index here would transcribe the same
episodes twice on a 4-core machine. Options:
- **one shared transcript cache**, keyed by file fingerprint, written by ai_tv's worker and read by both projects;
- or keep them separate.

The first full index of a whole TV folder is slow (CPU Whisper runs at a few times real time). So
roots are added one show at a time, and the job only re-indexes new files.

---

## Build order

1. A: spend and caps
2. B: QC
3. C: EDL and renderer, default EDL equals today's output
4. D: edit stage
5. E: review loop
6. F: per-stage models
7. G: footage index and footage shots

Parts A–C cost nothing to run, and the pipeline's output doesn't change until D is switched on.
Each part gets its config switch and a README section as it lands.

**During the build:** imports, schema checks and unit-level checks only. No renders and no paid calls until the tests below.

## Tests to run after the build (not run yet; each paid one needs your OK first)

| # | Part | Test | Cost |
|---|---|---|---|
| 1 | A | A fake paid call writes a `spend` row, and a budget of $0.01 blocks the next stage | free |
| 2 | B | QC on HamRest and Vanishing Cream: lines found, loudness, cuts, no false alarms. Then a deliberately broken copy (a line buried under music, a black gap, a frozen clip, a title off the edge) where every fault must be caught. Watch for false "masked line" alarms with `[edit] mode = "off"`: there the bed is worked out as the mix (after the 0.35 limiter) minus the voice bus (before it), so where the limiter acts some voice is counted as bed | free |
| 3 | C | Default EDL for production 2 renders the same as a fresh render through today's code from its saved writing (not the old `final.mp4`, made by older code): same length, cuts within a frame, loudness within 0.5 LU. A one-event change re-renders one segment | free |
| 4 | C | Renderer features one by one on a test EDL: dissolve, push, speed, slam title, duck depth measured with a test tone | free |
| 5 | D | The edit stage on an already-rendered production (7, Submerged Food Court), Sonnet 5.5 against Gemini 3.8 Flash, compared side by side with the original by you | ≈ $0.20 |
| 6 | E | The reviewer on the broken copy from test 2 and on a good keeper: does it find the planted faults (including one you can only hear: a buried line over clean picture), and does it leave the keeper alone? Flash Lite 3.5 against Qwen 3.8 Omni Flash against Gemini 3.8 Flash. Measure the real video tokens per second | ≈ $0.08 |
| 7 | E | A full review loop (2 rounds) on test 5's edit, patches applied, round 0 and round 2 kept for you to judge | ≈ $0.03 |
| 8 | F | The same pitch written by DeepSeek and by Sonnet 5.5 (outline and screenplay only), read by you | ≈ $0.25 |
| 9 | G | Index one show; search five catchphrases from the promos and check the in-points against the times found by hand. Also check `system.someone_watching()`: True with a viewer on the stream, False without (the encoder might be a child of `ersatztv-channel` rather than of ErsatzTV) | free |
| 10 | all | One full new 30 s ad end to end with everything on, against the same pitch through today's pipeline | ≈ $0.85 |

Total for the paid tests: about $1.40.
