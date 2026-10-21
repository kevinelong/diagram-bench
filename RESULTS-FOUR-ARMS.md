# Four arms, same corpus: the popular "champion" finishes last

Scores for FLUX.1-schnell (Q4_K_S, 4 steps, cfg 1.0) replace the third row of `RESULTS-ARMS-20261020.md`;
both files stay. Artifacts: `result-arm-flux*.json`, `result-vlm-flux.json`, `result-method-b-repair.json`,
`render/flux/`.

## The table

| | **B: code → SVG** | **klein-4B Q4_K_M** | **Z-Image-Turbo nvfp4** | **FLUX.1-schnell Q4_K_S** |
|---|---|---|---|---|
| steps / cfg | n/a (T=0) | 4 / 1.0 | 9 / 1.0 | 4 / 1.0 |
| images | 19 documents | 20/20 | 20/20 | 20/20 |
| wall, median | 13 s | 5.0 s | 5.1 s | 10.1 s |
| peak VRAM | n/a | 10,350 (1,877 free) **passes** | 11,748 (479 free) **fails floor** | 11,742 (485 free) **fails floor** |
| **labels: source / OCR** | **0.971 → 1.000 after repair** / 0.876 | — / 0.614 | — / 0.811 | — / **0.552** |
| **images with every label right** | 19/19 (source) / 14/19 (OCR) | 3/20 | 13/20 | 3/20 |
| paths, median | 69 * | **27.5** | 28.0 | **115.5** |
| axis alignment, median | 100 % | 100 % | 93.8 % | 100 % |
| structure checks | **44/57 (77 %)** | 37/60 (62 %) | 37/60 (62 %) | 35/60 (58 %) |
| didactic answers | **45/57 (79 %)** | 28/60 (47 %) | **36/60 (60 %)** | 27/60 (45 %) |
| deterministic at fixed seed | n/a | **no** | **no** | **no** |

\* B's 69 paths are its *worst case* (rasterise then re-trace); its real artifact is a 1-3 KB SVG with real
`<text>`.

## Three findings, in order of importance

1. **The claim in the briefs — "FLUX.1 is the undisputed local champion for 2D charts, blueprints and diagrams"
   — does not survive measurement in the configuration they imply.** At **4 steps, Q4_K_S**, schnell scores
   **0.552** on labels (START ok, then `VALID-DATE`, `TORE`, DONE), adds colour fills and drop shadows the prompt
   forbade, and traces into **115.5 paths** — four times the other diffusion arms. It is last on labels, last on
   traces, and last-ish on didactic.
2. **Z-Image-Turbo is the diffusion model that does this job**: 0.811 on labels, 13/20 fully correct, best
   didactic score of the three image models (60 %), 5.1 s, at **28 paths**. Its one flaw is hardware, not
   quality: **479 MiB free VRAM, under the 0.5 GiB floor**.
3. **The cheap harness fix worked and changed the ranking.** Source-level label validation found **68/70 = 0.971**
   in method B's SVGs where OCR on the render had said 0.876, and a single repair round took it to **70/70 =
   1.000** with all nineteen documents fully labelled. **OCR under-reported code by 10 points** because small
   text does not survive the render (tree-1's seven node numbers, network-1's one small label). For a code arm
   the source is the authoritative artifact; for a diffusion arm OCR is all there is.

## Why the FLUX result is not a refutation of FLUX

Stated plainly, because it matters: the usual praise for FLUX text rendering is for **FLUX.1-dev at 20-30 steps**,
and dev is **non-commercial and gated**. We measured **schnell at 4 steps, Q4_K_S**, which is schnell's intended
setting but the harshest text-rendering condition in the family. A fair follow-up would be dev at 20 steps, and
the honest sentence today is: *"the configuration the brief recommends for a 12 GB card is not the configuration
that earned the reputation."*

Two secondary observations support the same reading: FLUX was the only arm whose images carried drop shadows and
colour fills despite an explicit "solid monochrome fills, no shadows" suffix, and its path count is the direct
consequence - shading becomes hundreds of overlapping paths, exactly the failure the briefs warn about and warn
against with the same tool.

## What this changes about the recommendation

- **Keep method B as the production path for teaching diagrams**: 77 % structure, 79 % didactic, 100 % labels
  after repair, and an editable artifact. Nothing else on this machine is close on correctness.
- **Keep Z-Image as the image model to investigate** - it is the only diffusion arm whose labels approach code's,
  and the floor breach is a sizing problem (smaller encoder, fewer resident components) rather than a quality one.
- **Retire schnell for this job**, and do not spend further VRAM on it: it loses to a 4B model that is half the
  time and a quarter of the paths.
- **FLUX.1-dev remains untested** - it needs the licence decision and a gated download, and it is the only
  configuration that could still earn the claim.

## Method caveats, unchanged

n = 1-2 per prompt, one quantisation per model, one sampler (euler/simple), one style suffix, one scoring VLM
(`deepseek-flash`) whose answers are stored raw. Neither diffusion arm is deterministic at fixed seed, so
differences under a few points carry that noise. The corpus is ten prompts designed to be checkable, not a
representative sample of diagram requests.
