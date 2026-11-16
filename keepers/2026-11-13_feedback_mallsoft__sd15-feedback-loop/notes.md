SD 1.5 feedback loop, mallsoft (Realistic Vision 5.1, local ComfyUI, no API spend).
Prompt: "empty 1990s shopping mall atrium, indoor fountain, potted palms, skylight, glass elevators,
teal and pink neon". Negative adds "people, person, nude". 384x288, seed 1313.
Frame 000: txt2img, final preset. Frames 001-359: previous frame fed back through img2img, draft
preset (LCM 6 steps), denoise 0.55, seed +1 per round. 360 frames at 12 fps = 30 s.
Timing (GTX 970): frame 000 ~10 s, rounds ~3.0 s each, whole run 1104 s (~18.5 min).
Drift seen: atrium with skylight and fountain -> flattens into an endless head-on grid of balconies
and windows with palms -> pink walls take over -> a swimming pool fills the floor near the end.
The neon never appears.
