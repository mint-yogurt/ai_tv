# Vanishing Cream: HeyGen Video 1's take

**Model:** heygen/heygen-video-1 on OpenRouter, text to video at 768p (1024x768), 4:3. The script is `heygen_take.py`; the request bodies and job ids are in `manifest.json`.

**Inputs:**
- Production 0002's screenplay, as one hand-written prompt per scene.
- Tarman's World reference (`tarman_reference.jpg`), sent as `input_references` on his two scenes.
- The model invented the sets, the wife and the husband, and made all the dialogue, sound effects and on-screen text.
- Production 0002's music, laid underneath by `stitch.sh`.

**Output:** `vanishing_cream__heygen-video-1.mp4`, 40.5 s long, mixed at -16.2 LUFS.

| Scene | Tarman reference | Asked | Generation time | Cost |
|---|---|---|---|---|
| 0 | no | 6 s | 21 s | $0.108 |
| 1 | yes | 9 s | 32 s | $0.450 |
| 2 | no | 10 s | 20 s | $0.180 |
| 3 | yes | 14 s | 34 s | $0.700 |

**Total:**
- Cost: $1.438 over 4 calls, with no failures or retries.
- Generation time:
  - The scene 1 pilot took 32 s.
  - The other three ran in parallel and took 38 s.
  - The whole run, from the first submit to the last download, took 98 s. That includes the pause while the pilot was checked.

**Pricing:** scenes with a reference image cost about $0.05 a second, against $0.018 a second without one.

For comparison, the pipeline's version is productions/0002/final.mp4.
