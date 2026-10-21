# Troubleshooting: seventeen measurement errors, 2026-10-20/21

Every row is an error in **this instrument**, not in a model: either a wrong number was produced, or a correct
artifact was scored wrong. Each row names the RESULT file where the error, its diagnosis and its fix are recorded.
The order is roughly the order they were found.

Read this before changing a threshold. The pattern in all seventeen is the same: the check encoded an assumption
about how the drawing would be built, instead of measuring the requirement the prompt states.

| # | Symptom | What was wrong | Fix | Recorded in |
|---|---|---|---|---|
| 1 | A soft gradient scored **93.6 %** `flat_pct`, the clean diagram **97.7 %** — the metric could not tell them apart | `flat_pct` counts pixels without a *sharp* edge, and a smooth gradient has almost none. Its name promised gradient detection; it measured edge harshness | Gradient detection moved to `colours_major` (2 vs 30) and SVG size (17.1 KB vs 170 KB); `flat_pct` kept with its caveat written down | [INSTRUMENT-VALIDATION-20261020.md](INSTRUMENT-VALIDATION-20261020.md), listed in [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md) |
| 2 | Structure validator passed only **8/19** documents | v1 demanded **exact** counts everywhere. The prompts pin minimums or nothing: two bedrooms ⇒ `BEDROOM` twice, four tick marks are connectors, a right-angle connector is three segments, a swimlane is not countable | `<field>_exact` / `<field>_at_least` / unconstrained, with the reason for each written into `structure-expectations.json` ⇒ **19/19** | [RESULTS-STRUCTURE-VALIDATOR.md](RESULTS-STRUCTURE-VALIDATOR.md) |
| 3 | `process-1`'s lane crossing was not seen | Crossing was measured against the **canvas midline**; the real crossing is at `x=850, y=220 -> 440` while the lane divider sits near y≈360 | Measure against the **detected** lane divider | [RESULTS-SHAPE-CHECKS.md](RESULTS-SHAPE-CHECKS.md) |
| 4 | `circuit-1`'s capacitor failed a gap rule | The plate gap limit was an arbitrary **40 px** and there was no length window; the plates are **56 px** apart and **256 px** long on one seed against **64 px** on the other | Gap becomes **0.08 x width**; length window **8-400 px** | [RESULTS-SHAPE-CHECKS.md](RESULTS-SHAPE-CHECKS.md) |
| 5 | `floor-1`'s walls were "not thick enough" | Walls were tested as `stroke-width >= 4`; the drawing's walls are **filled black rects 32 px thick** with no `stroke-width` attribute at all | Walls measured as filled dark narrow rects | [RESULTS-SHAPE-CHECKS.md](RESULTS-SHAPE-CHECKS.md) |
| 6 | `blueprint-1` had "no dimension arrows" | The check looked for SVG `<marker>` elements; this drawing emits none, so arrowheads are absent or separate primitives | Dropped as **unmeasurable**, rather than scored as a failure | [RESULTS-SHAPE-CHECKS.md](RESULTS-SHAPE-CHECKS.md) |
| 7 | `floor-1` scored 1 and 3 "enclosed rooms" and was called wrong for it | A closure flood-fill **contradicts the prompt**, which asks for *one door gap in each room wall*: rooms joined by their doors are correct | Criterion retired with its reason in `structure-expectations.json`; replaced by a door-gap scan and a label multiset | [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md) |
| 8 | A second `BATH` in a two-bedroom plan went unnoticed | Neither "at least 4 labels" nor "every required string present" can see a **duplicate** — every required string is present | Compare the **multiset** of text nodes against the spec ⇒ `floor-1-s0` fails, `s1` passes | [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md) |
| 9 | The pockets check failed a table whose pockets were right, and passed one whose side pockets were on the short rails | v1 **counted** circles instead of locating them, and demanded `short_rail == 2` while counting corners separately | Rule becomes 2 long-rail side pockets + 4 corners + **0** short-rail, with positions measured | [RESULTS-PROMPT-V2.md](RESULTS-PROMPT-V2.md) |
| 10 | A cue drawn tip-to-butt read **-153.4°** and failed an angle window | The angle was taken without **normalising direction**; the line's *axis* is 26.6°, not -153.4° | Fold the angle into 0-90 and ignore elements within 2° of an axis | [RESULTS-PROMPT-V2.md](RESULTS-PROMPT-V2.md) |
| 11 | A well-formed table had "no table rectangle" | The table rect was discarded as **background** whenever the SVG's own width and height equalled the table's — which a correct drawing does | Keep the largest rect when it is the only candidate | [RESULTS-PROMPT-V2.md](RESULTS-PROMPT-V2.md) |
| 12 | Diamond count was zero on a drawing that had diamonds | They are **rotated `<rect>`s**, not `<polygon>`s, so counting polygons found none | Count rotated rects, honouring the transform | [RESULTS-V3-ACE-CONSTANTS.md](RESULTS-V3-ACE-CONSTANTS.md) |
| 13 | A cue inside a rotated group read **0°** | The checker read the child element's own attributes and ignored the enclosing `<g transform="rotate(30 25 25)">` | `walk()` accumulates group rotations down the tree | [RESULTS-V3-ACE-CONSTANTS.md](RESULTS-V3-ACE-CONSTANTS.md) |
| 14 | Three correct lines-only drawings were scored as failures | The acceptance demanded a cue within **20-45°** and `stroke-width >= 4`. Both invented: the prompt says only *"angled toward the cue ball"*, and the model omitted `stroke-width` (defaulting to 1) | Quote the criteria from the task ⇒ **3/3** pass, two at exactly **30.0°** | [RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md) |
| 15 | Code scored **0.261** on OCR at 512 against its own **0.876** at 1024 | OCR at 512 is not comparable to OCR at 1024: the same SVGs, rendered smaller, stop being legible | Resolution becomes a first-class variable; the 1024 corpus stays the reference, and 512 numbers rank diffusion arms only against each other | [RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md) |
| 16 | KiCad's SVG scored **0 detected straight segments**, twice | The export draws F.SilkS in pale yellow on white, so `Canny(50,150)` sees nothing while OCR still reads the text. Raster proxies are invalid for a vector-native arm | Score the arm **from the source** (paths, text nodes, labels, coordinates) — raster only measures the exporter's colours | [RESULTS-METHOD-E.md](RESULTS-METHOD-E.md) |
| 17 | After "fixing" the recolour, the export was still pale yellow and still scored zero segments | The recolour searched SVG **attributes** (`stroke="#F2EDA1"`) while KiCad writes colour as **CSS inside `style`** (`stroke:#F2EDA1`) | Rewrite the style value ⇒ same diagram scores axis **100 %**, OCR **4/4**, **62** traced paths | [RESULTS-METHOD-E-LLM.md](RESULTS-METHOD-E-LLM.md) |

## The three rules that keep producing these

1. **Look at the artifact before believing the number.** Fifteen of the seventeen were caught by opening the SVG
   or the render, not by re-reading the code.
2. **A threshold that is not in the prompt is an invention.** Rows 4, 6 and 14 are all a number nobody asked for.
   The 20-45° window from row 14 is still baked into `check_geometry_v2.py` and `check_ace_geometry.py`; treat a
   cue-angle failure there as *informative*, not authoritative.
3. **The measurement axis is not the model axis.** Rows 1, 15 and 16 are all cases where the metric was fine and
   its name or scope was wrong.

## Also recorded, outside the seventeen

These are real, recorded faults, but they are harness and tooling defects rather than measurements of a model, so
they are not counted above and are kept so nothing is lost:

- **The few-shot example is the interface.** Method E's first attempt drew balls as tiny line dots because the
  verified example only demonstrated `seg`, `rect` and `text`; adding `circ()` produced **22 circles** on the next
  run ([RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md)).
- **`--page-size-mode 2` fits the board area, which is empty without Edge.Cuts**, so the drawing rendered as a
  **54 × 64 px speck**; `post_outline.py` measured the drawings and added an outline for a 93.3 × 44.4 mm export
  ([RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md)).
- **Cropping by rewriting the SVG `viewBox` failed** — it assumed uppercase absolute path commands and produced a
  **21 × 26 mm** box that cut the drawing away; the raster is cropped by non-white pixels instead
  ([RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md)).
- **`.kicad_pcb` parsing was wrong twice**: KiCad 10 writes **millimetres**, not nanometres, and nested
  S-expressions defeated regex, so a balanced-parenthesis extractor replaced it
  ([RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md)).
- **Exit status is not evidence about the artifact, in either direction.** Method E's model script exited **1**
  (`NameError: name 'OK' is not defined`) and still wrote the right board — **48 paths, 2 `<text>`** worth of
  correct export ([RESULTS-METHOD-E.md](RESULTS-METHOD-E.md)); FLUX.1-dev's job returned success and produced a
  blurred image at Laplacian variance **1.5** ([RESULTS-DEV-BLOCKED.md](RESULTS-DEV-BLOCKED.md)). Both are why
  every arm here is scored by inspecting output.
- **Two controls were launched concurrently against a single-slot Ollama server**, so their wall times
  (**59.3 s** and **82.9 s**) are contaminated and used nowhere; only content and validity are read from that run
  ([RESULTS-CONTROLS.md](RESULTS-CONTROLS.md)).
- **A path count is not an axis you can compare across methods.** Method B's median of **69** is what you get by
  re-tracing its SVG; its artifact is a 1-3 KB source file with real `<text>`
  ([RESULTS-ARMS-20261020.md](RESULTS-ARMS-20261020.md)).
