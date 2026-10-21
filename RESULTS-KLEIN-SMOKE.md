# klein-4B smoke test, 2026-10-20: geometry at the ceiling, and a misspelled label

The first diffusion image on this track, scored by the same instrument as method B. This was a **smoke test**
(one prompt, one seed, no VRAM sampling), not an arm - but it already answers the question the arm was meant to
ask.

## Setup

ComfyUI 0.18.2 headless on `127.0.0.1:8188`, **FLUX.2-klein-4B GGUF Q4_K_M** (2.60 GB) via `UnetLoaderGGUF`,
**qwen_3_4b_fp4_flux2** encoder via `CLIPLoader type=flux2` (3.85 GB), **flux2-vae** (0.34 GB), all on D:.
`KSampler` euler/simple, **4 steps, cfg 1.0**, seed 1234, 1024x1024, prompt `flow-1` with the corpus style suffix.

## Result

| | value |
|---|---|
| wall time, first image (includes model load) | **16.1 s** |
| traced paths | **37** - identical to the hand-drawn control |
| traced nodes | 1,042 |
| axis alignment | **95.8 %** |
| major colours | **2** (flat fills, white background, no gradient) |
| OCR | **3 of 4 labels**, and the miss is real |

## The finding

**The model wrote `VAILDATE`.** The OCR read it exactly as drawn, so the automated 0.75 is not an OCR weakness -
it is a genuine spelling error, a transposition of two letters inside a seven-letter word. Everything else about
the image is better than method B's: heavier strokes, arrows with heads, better vertical composition, and a path
economy (37) that matches a hand-drawn diagram.

So the two methods fail on **different axes**, and the smoke test settles the architecture question:

| | geometry / trace | label fidelity | editability |
|---|---|---|---|
| **method B** (code) | 69 paths median, axis 100 %, OCR **0.876** | exact when present; can omit a label entirely | full: real `<text>`, 1-3 KB source |
| **klein-4B** (diffusion) | 37 paths, axis 95.8 %, 2 colours | **hallucinates letters inside a requested word** | none: flattened pixels, re-tracing required |

That is the argument for the hybrid the brief described, but with the responsibilities assigned by evidence rather
than enthusiasm: **diffusion supplies the look, code supplies the text**, or the diffusion output is only
admissible when an OCR check passes and a repair pass fixes what it finds.

## What this does not establish

- **n = 1.** One prompt, one seed, 4 steps, cfg 1.0 - and those steps/cfg were a reasonable guess, not the
  model's recommended values, which still need to be read from its card.
- **No VRAM sampling and no determinism check** yet; the arm needs both, and the peak-VRAM sampler is already
  written for the LLM track.
- **The other nine prompts are unseen**, especially `text-3` (the number "55") and `tree-1` (seven labels), where a
  letter-transposition failure would be decisive rather than anecdotal.
