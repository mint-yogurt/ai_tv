# Keep forever

Since 2026-11-15 nothing new goes here: a finished production is kept by publishing it (the
`publish` stage copies it into the media library and catalogs it; see the README). What's below stays.

Everything in this folder is a keeper the user asked to save. Never delete it in a cleanup of test
material (`test_outputs/`, scratch, old productions). Copy the finished videos to the media library's
output folder once that exists, and keep this folder anyway.

- `2026-11-12_the_shart_in_mart__heygen-video-1/`: "The Shart in Mart" trailer as made by
  heygen/heygen-video-1, from production 0003's screenplay with that production's music underneath.
  The user's reaction: "this is exactly what i had in mind with ai_tv."
- `2026-11-12_vanishing_cream__heygen-video-1/`: "Vanishing Cream" as made by heygen/heygen-video-1
  from production 0002's screenplay, with Tarman's World reference image sent as input_references and
  that production's music underneath. The user: "truly remarkable."
- `2026-11-12_dr_schwegers_triumphant_patrol__tencent-heygen-mischief/`: "Dr. Schweger's Triumphant
  Patrol", production 0004 (1950s sci-fi weird short, 71 s, black and white). Pipeline outline and
  screenplay, then hand-run shots: tencent/hy-image-v3.5-preview first frames from the World reference
  images into heygen/heygen-video-1, SD stills into mischief-1, Poots voice, MIDIgen music. `run.py` is
  the script that made it. The test for wiring the Tencent image-to-image step into the pipeline.
- `2026-11-13_feedback_mallsoft__sd15-feedback-loop/`: 30 s SD 1.5 feedback loop (local ComfyUI,
  Realistic Vision 5.1): a 90s mall atrium txt2img fed back through img2img 359 times at denoise 0.55,
  384x288, 12 fps. `run.py` made it; `notes.md` has the prompt, settings and timing.
- `2026-11-15_cereal_killers_promo/`, `2026-11-15_tv_afterlife_promo/`, `2026-11-15_prime_cheese_promo/`:
  the first three ChupTV block promos (30 s each). Copied from `test_outputs/video/` without `.work/`;
  each `notes.md` is the full ledger, and the test_outputs copies stay as the working folders for later
  retrovision passes.

## On air (copied into the media library, 2026-11-15)

Copies, not moves: this folder stays as it is. Catalogued in `~/chuptv_director/data/chuptv.db`
(origin `ai_tv`, hand-written profiles and tags).

| Keeper | Media copy | Catalog |
|---|---|---|
| Cereal Killers promo | `/mnt/media/chuptv/promos/blocks/cereal_killers_promo.mp4` | `0unzfw`, promo |
| TV Afterlife promo | `/mnt/media/chuptv/promos/blocks/tv_afterlife_promo.mp4` | `7reoos`, promo |
| Prime Cheese promo | `/mnt/media/chuptv/promos/blocks/prime_cheese_promo.mp4` | `a3cjp6`, promo |
| The Shart in Mart | `/mnt/media/chuptv/promos/trailers/the_shart_in_mart_trailer.mp4` | `6jekl5`, promo |
| Vanishing Cream | `/mnt/media/commercials/vanishing cream/vanishing_cream.mp4` | `1kzwet`, commercial |
| HamRest (`productions/0001/final.mp4`) | `/mnt/media/commercials/hamrest/hamrest.mp4` | `q2ekwr`, commercial |
| Dr. Schweger's Triumphant Patrol | `/mnt/media/chuptv/shows/ai_tv/dr_schwegers_triumphant_patrol.mp4` | `90wpr9`, generated |
| Feedback Mallsoft | `/mnt/media/chuptv/backgrounds/feedback_mallsoft.mp4` | `8t3pt7`, bumper |
