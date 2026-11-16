# 🚨🚨🚨 START HERE: TEST PIPELINE V2 🚨🚨🚨

**Built 2026-11-15. Tests 1–9 done ($0.28, 7 bugs fixed): `test_outputs/video/2026-11-15_pipeline_v2_tests/RESULTS.md`. Next: your decisions there, then test 10 (video, ≈ $1, needs your OK), then the web UI overhaul.**

## Where things stand

- Parts A–G are built and wired in, with 13 unit tests (`tests/test_v2.py` and one page test) on synthetic media with fake
  models. All 55 tests pass.
- **No real production has gone through it yet. No paid call has been made.**
- **What's on now:**
  - spend tracking;
  - QC after every look (local, free, with Whisper).
- **What's off:**
  - `[edit] mode = "off"`: today's assembly;
  - `[review] enabled = false`;
  - `[footage] enabled = false`;
  - Sonnet for the writing (commented out).
- Docs: README → "Pipeline v2"; full plan and tests: `docs/pipeline-v2-plan.md`.

## Decisions taken with the defaults

1. **Editor:** Claude Sonnet 5.5 (`[llm.stage_models] edit`)
2. **Reviewer:** Gemini 3.5 Flash Lite (`[llm.stage_models] review`)
3. **Writing:** Sonnet 5.5 for the outline and screenplay. Ready, but commented out until test 8.
4. **Regeneration cap:** $0. The reviewer only flags shots for new clips; buying them isn't built.
5. **Whisper cache:** one `transcripts` table keyed by the director's file fingerprint, so it can be
   shared. **Your call** whether the director reads it.

## The tests, in order (free ones first; each paid one gets your OK)

- [x] 1. Spend row and budget block (free)
- [x] 2. QC on HamRest and Vanishing Cream, then a deliberately broken copy (free)
- [x] 3. Default EDL (`mode = "auto"`) on production 2 matches today's code (free)
- [x] 4. Renderer features on a test EDL (free)
- [x] 5. LLM editor on production 7, Sonnet against Gemini 3.8 Flash (≈ $0.20)
- [x] 6. Reviewer on the broken copy and a keeper; can it hear? (≈ $0.08)
- [x] 7. Full 2-round review loop (≈ $0.03)
- [x] 8. DeepSeek against Sonnet writing (≈ $0.25)
- [x] 9. Index one show and search five catchphrases (free)
- [ ] 10. One full ad, end to end, against today's pipeline (≈ $0.85)

## Also pending

- **Sons of Rasmir guild ad:** paused. Needs the footage source and the voice choice.
- **retrovision passes on the approved promos:** whenever you ask.

Delete this file once v2 is tested and switched on.
