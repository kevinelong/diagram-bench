# Grounded revision of the ten corpus prompts, 2026-10-21

The pool table was the only task in this track whose prompt had been rewritten against real numbers —
a 100x50 ACE grid, a 2.25-unit ball, 12.5-unit diamond spacing, a 30-unit cue at 30 degrees. It is also the
only task where a score can be read as *geometry* rather than *style*. This gives the other ten the same
treatment, and makes the grounding checkable instead of asserted.

## What exists now

| file | what it is |
|---|---|
| `grounded.py` | one **spec** per prompt: canvas, background, and every element as coordinates. The prompt text is **generated from the spec**, so there is no second copy of the numbers that can drift |
| `prompts-diagrams-grounded.json` | the ten prompts, same ids as the frozen corpus, 671–1286 characters (the originals were 130–190) |
| `grounded_ref/<id>.svg` | the reference drawing, also generated from the spec — "what the prompt asks for" and "what the checker is handed" are the same object |
| `grounded_ocr.py` | rasterises each reference in Chromium at 1024x1024 and reads it back with RapidOCR |

The frozen corpus (`prompts-diagrams.json`) and the frozen expectations are **untouched**. This is a second
revision, not an edit: a model arm can run against either and the diff is the prompt, not the instrument.

## Two deterministic proofs

1. **Structure** (`python grounded.py --selftest`): box counts against `structure-expectations.json` (exact where
   the prompt fixes a count), label strings **with multiplicity** (two `BEDROOM`s are two labels), connector
   endpoints landing on a box edge, one arrow actually spanning the swimlane divider, every coordinate inside the
   canvas, and every coordinate token present in the generated prompt. **10/10, 0 errors.**
2. **Legibility** (`python grounded_ocr.py`): every label read back **exactly** by a machine reader.
   **43/43 labels across the ten prompts**, reproduced on two consecutive runs.

## Three defects the proof found in my own work

- **A dimension line is not a connector.** `blueprint-1`'s dimension arrow tripped the "connector must join boxes"
  check. Lines now carry a role (`connector` / `axis` / `dimension`), and only connectors are counted — which is
  what `structure-expectations.json` was already assuming.
- **A label placed on an obstacle.** `120` sat on the right mounting hole's edge and was unreadable. Moved clear.
- **Draw order, and this one generalises.** The tree drew its **edges after its nodes**, so six connectors ran
  through seven circles and cut the digits apart: OCR read `4,5,6` and missed `1,2,3,7`. Drawing edges **first**,
  filled circles second, digits last fixed it to **7/7**. Any diagram generator that fills a node must paint it
  *after* its edges; a version that does not is not a legibility problem to blame on the model.

That last one is the strongest argument for building this instrument before running an arm: a model given the old
tree prompt could produce a perfectly correct tree, draw it in the wrong order, and be scored as a labelling
failure.

## What this does not establish

- **No model has seen these prompts.** The gap between "decodable" and "decoded by the model" is exactly the
  measurement the arms exist to make, and it is still unmeasured for all ten.
- The grounded prompts are **5-10x longer** than the originals. Length is itself a variable: a model that follows
  the grounded version better may be following *more tokens*, not *better structure*. A like-for-like arm has to
  report both prompt versions and the token counts.
- Nothing here changes any prior score. The frozen corpus's numbers stand as measured against the old prompts.

## Next action

Run one arm (Z-Image or FLUX.1-schnell, seed 1234, the corpus style suffix) against
`prompts-diagrams-grounded.json` and score it with the same validators used on the frozen corpus, so the only
difference is the prompt. That needs the inference slot and, for FLUX.1-dev, Kevin's licence ruling.
