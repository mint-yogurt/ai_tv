# The Shart in Mart: trailer, HeyGen Video 1's take

**Model:** heygen/heygen-video-1 on OpenRouter, text to video at 768p (1024x768), 4:3. The script is `heygen_take.py`; the request bodies and job ids are in `manifest.json`.

**Inputs:**
- Production 0003's screenplay, as one hand-written prompt per scene. The model made all the picture, dialogue, sound effects and on-screen titles.
- The only addition is production 0003's MIDIgen music cues, laid underneath by `stitch.sh`.

**Output:** `the_shart_in_mart_trailer__heygen-video-1.mp4`, 33.9 s long, mixed at -17.3 LUFS.

| Scene | Asked | Generation time | Cost |
|---|---|---|---|
| 0 | 5 s | 22 s | $0.090 |
| 1 | 5 s | 19 s | $0.090 |
| 2 | 5 s | 22 s | $0.090 |
| 3 | 8 s | 22 s | $0.144 |
| 4 | 5 s | 21 s | $0.090 |
| 5 | 5 s | 21 s | $0.090 |

**Total:**
- Cost: $0.594 over 6 calls, with no failures or retries.
- Generation time:
  - The scene 1 pilot took 19 s.
  - The other five ran in parallel and took 26 s.
  - The whole run, from the first submit to the last download, took 76 s. That includes the pause while the pilot's audio was checked.

For comparison, the pipeline's version (productions/0003/final.mp4) cost about $0.26 in total, with $0.145 of that spent on video.
