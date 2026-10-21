# Method E driven by the model: two failed harnesses, then 3/3 first-attempt

Can a local coder model drive KiCad's `pcbnew` API to produce teaching diagrams? Yes - after the **prompt** was
given a verified example. The path there is the result.

## Three attempts at the harness

| harness version | what it did | outcome |
|---|---|---|
| **A** | task described the API in prose ("`SetStart`/`SetEnd`/`SetWidth` for lines") | **0/9** - every script died on `AttributeError: 'BOARD' object has no attribute 'SetWidth'` |
| **B** | varied the seed per attempt and raised temperature to 0.4 after the first failure | **still 0/9**, identical error nine times |
| **C** | put a **verified working example** in the prompt and an explicit prohibition ("never call `SetWidth` on `board`") | **3/3 clean on the first attempt** |

**What A and B taught, and it is not about the model.** The model's mistake was systematic: it wanted to "set board
dimensions" and invented `board.SetWidth(...)`. Re-sending that error at **temperature 0 with one seed reproduces
the same sample** - my retry loop was a no-op dressed as a fix. Varying the seed did not help either, because the
error is the model's reading of the API, not sampling noise. **Only changing the information in the prompt
changed the outcome.**

## Result: three prompts, first attempt each

| prompt | script | source SVG | labels in source | render |
|---|---|---|---|---|
| `flow-1` | exit 0, board written | **160 paths, 4 `<text>`** (START, VALIDATE, STORE, DONE), 9.9 KB | **4/4** | **axis 100 %, OCR 4/4** |
| `circuit-1` | exit 0, board written | 54 paths, 2 `<text>` (R1, C1), 3.9 KB | **2/2** | - |
| `blueprint-1` | exit 0, board written | 31 paths, 2 `<text>` (B, A), 3.2 KB | **2/2** | - |

Generation cost: **~15 s per script** (44.9 s for three prompts including export). Every artifact is a **true
vector with real text** - nothing to trace, and nothing that could be misspelled by a diffusion model's glyph
renderer.

## The measurement lesson inside this one

The recolouring step that makes the export judgeable failed on its first try: I searched for SVG **attributes**
(`stroke="#F2EDA1"`) while KiCad writes colour as **CSS inside `style`** (`stroke:#F2EDA1`). The first render stayed
pale yellow, edge detection found zero segments, and it would have been easy to record "KiCad diagrams cannot be
scored" - which would have been wrong. After fixing the recolour, the same diagram scores **axis 100 %, OCR 4/4,
62 traced paths**.

**Same rule as the rest of this track, a seventh time**: check the artifact before believing the instrument. The
list is now: cache-rung order, `flat_pct` vs gradients, exact structural counts, uncalibrated shape proxies,
closure vs door gaps, OCR at 512, and now a recolour that assumed the wrong syntax.

## Where method E stands against the others

| | method B (code→SVG) | **method E (code→KiCad)** | Z-Image (diffusion) |
|---|---|---|---|
| artifact | editable SVG, 1-3 KB | **true vector, 0.3-0.9 KB per shape, real text** | PNG, needs tracing |
| labels | 19/19 after repair | **3/3 first attempt, 4/4 on the one rendered** | 0.811 |
| geometry | axis 100 % (where not diagonal) | **axis 100 %** | 93.8 % |
| failure mode | omitted labels, layout | **script validity** | glyph errors, resolution |
| toolchain | none beyond Python | KiCad 10 + `pcbnew` | ComfyUI + 4-6 GB weights |

**Method E is the most precise and the most fragile**: it needs an installed CAD stack and a correct script, but
once those exist the output is exact, tiny and editable. For teaching diagrams that will later be *animated*,
that property - a parametric source rather than a picture - is what makes animation possible at all.
