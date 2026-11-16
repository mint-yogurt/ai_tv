# tools/

Each local model gets its own folder and its own Python environment here, so their
dependencies (and PyTorch versions) can never break each other or the main app.

| Folder | Tool | Status |
| --- | --- | --- |
| `pocket_tts/` | Pocket TTS, CPU voice cloning | working: wired into the voice preview job |
| `whisper/` | Whisper, transcription of voice references | next step |
| `comfyui/` | ComfyUI v0.19.1 + Realistic Vision 5.1, on the GTX 970 | tested (`bench.py`); not yet a service |
| `~/MIDIgen` (outside this folder) | MIDIgen: General MIDI composition + FluidSynth (Debian package 2.3.1) | working: SoundFonts page, Tests page; `[tools] midigen` |
| `chatterbox/` | Chatterbox TTS + voice conversion, CPU | tried for Rasmir; Pocket TTS won; can be deleted |

Rules for this machine (GTX 970, Maxwell, 4 GB):

- PyTorch: the last prebuilt wheels that support Maxwell are 2.14 (cu126). The known-good
  stack for ComfyUI on a GTX 970 is torch 2.5.1+cu121. Pin it; never upgrade blindly.
- CPU-only tools (Pocket TTS, Whisper) install the CPU build of PyTorch, so they don't pull
  gigabytes of CUDA libraries they won't use.
- Once a tool works, put its command in `[tools]` in `config/aitv.toml` and press
  "Retry blocked" on the Jobs page.
