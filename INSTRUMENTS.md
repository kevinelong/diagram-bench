# Instruments

Every script in this folder: what it measures, in what unit, what a good and a bad value look like, and the
calibration error it has already had. Every number below is quoted from the RESULT file named under **Recorded
in**; where nothing in this folder cites a script's output, the entry says **no recorded run**.

Two conventions:

- A *measure* is deterministic and re-runnable on an artifact that already exists. A *score* is a count of checks
  whose pass/fail rule was written down before the run.
- "Good" and "bad" are values actually observed in this corpus, not thresholds anyone would like to see. Where an
  observed value is good for one prompt and bad for another (axis alignment on a binary tree), the entry says so.

How to run anything here: see [README.md](README.md#running-the-toolchain) — the interpreter must be launched
through `cmd.exe`.

---

## `trace_score.py` — raster metrics for vector traceability

**Measures.** Rasterises nothing itself; it reads a PNG, traces it with `vtracer` at fixed parameters
(`mode=spline`, `filter_speckle=4`, `color_precision=6`, `layer_difference=16`, `corner_threshold=60`,
`length_threshold=4.0`), and reports:

| field | definition |
|---|---|
| `paths`, `nodes` | `<path` count in the trace, and a count of the characters `C` `L` `M` `Q` |
| `axis_pct` | share of `HoughLinesP` segments within 2° of horizontal or vertical |
| `straight_px` | share of Canny edge pixels lying on a detected long segment |
| `flat_pct` | share of pixels whose Sobel magnitude is below 30 |
| `colours`, `colours_major` | unique 5-bit quantised colours; bins holding ≥0.5 % of pixels |
| `ink_pct` | share of pixels more than 25 grey levels from the modal background |

**Unit.** Counts and percentages. `nodes` is a character count over the whole SVG text, not a path-command
parse — treat it as a proxy, and read it beside `paths`, never instead of it.

**Good.** Hand-drawn flowchart with known ground truth (4 boxes, 3 connectors, 4 labels): `paths 37`, `nodes 447`,
`axis_pct 100.0`, `straight_px 60.0`, `colours_major 2`, `ink_pct 3.0`, `17.1 KB` — the trace recovers what is
there and adds no speckle.

**Bad.** Soft radial gradient, nothing else: `paths 164`, `nodes 3,044`, `170 KB`, `colours_major 30`,
`straight_px 11.9 %`, `4 segments`. The scorer separates the two by up to 15x on every axis that matters, which is
the property that licensed it to rank models.

**Calibration errors already earned.**

1. **`flat_pct` was named as a gradient detector and is not one.** A smooth gradient has almost no sharp edges:
   the gradient scored **93.6 %** flat against the diagram's **97.7 %**. The gradient detector is
   `colours_major` (2 vs 30) and SVG size. `flat_pct` stays in the output as edge-harshness only. Recorded in
   [INSTRUMENT-VALIDATION-20261020.md](INSTRUMENT-VALIDATION-20261020.md) and listed among the track's instrument
   errors in [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md).
2. **On a vector-native arm it measured the exporter's colours, not the drawing.** The KiCad F.SilkS export is
   pale yellow on white, so `Canny(50,150)` found **0 detected straight segments twice** (1024x1024 letterboxed,
   then native aspect at 2x device scale) while OCR still read the text. A vector arm must be scored from the
   source. Recorded in [RESULTS-METHOD-E.md](RESULTS-METHOD-E.md).
3. **`paths` is a misleading axis for a code arm.** Method B's median of **69** is its worst case — what you get
   by rasterising its SVG and re-tracing it; its artifact is a 1-3 KB source file with real `<text>` and needs no
   tracing. Recorded as a footnote in [RESULTS-ARMS-20261020.md](RESULTS-ARMS-20261020.md).
4. **Cross-resolution numbers are not comparable.** Path economy scales with resolution (code 69 → 24,
   Z-Image 28 → 31.5), so a path count is only meaningful inside one resolution. Recorded in
   [RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md).

**Recorded in.** [RESULTS-METHOD-B.md](RESULTS-METHOD-B.md) (median 69 paths, 702 nodes, 100.0 % axis, 2 major
colours over 19 documents), [RESULTS-ARMS-20261020.md](RESULTS-ARMS-20261020.md),
[RESULTS-FOUR-ARMS.md](RESULTS-FOUR-ARMS.md), [RESULTS-KLEIN-SMOKE.md](RESULTS-KLEIN-SMOKE.md),
[RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md), [RESULTS-METHOD-E.md](RESULTS-METHOD-E.md).

---

## `score_arm.py` — one folder of renders, end to end

**Measures.** Every `*.png` in a folder, keyed back to `prompts-diagrams.json` by the `-s0` / `-s1` suffix, then
per image: `paths`, `nodes`, `traced_svg_kb`, `axis_pct`, `colours_major`, `ink_pct` (all from `trace_score`) plus
`ocr_exact` — the fraction of the prompt's required labels found by `rapidocr` as case-insensitive substrings of
recognised text. Summarises with medians and `ocr_exact_mean`, `ocr_full_match`.

**Unit.** Fraction for `ocr_exact`; counts for the rest.

**Good.** Method B: `ocr_exact_mean 0.876`, `14/19` images with every required label read.
**Bad.** FLUX.1-schnell at 4 steps: `ocr_exact_mean 0.552`, `3/20` fully labelled.

**Calibration errors already earned.**

1. **OCR under-reports a code arm.** The same 19 documents scored **0.876** by OCR and **0.971** (68/70 labels) by
   reading the SVG source, because small text does not survive the render. For a code arm the source is the
   authoritative artifact. Recorded in [RESULTS-FOUR-ARMS.md](RESULTS-FOUR-ARMS.md).
2. **The OCR number is resolution-bound.** Method B reads **0.876** at 1024 and **0.261** at 512 from the *same*
   SVGs rendered smaller; ranking code against a diffusion arm across resolutions reads a rendering artefact as a
   model difference. Recorded in [RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md).
3. Substring matching is deliberately lenient — a required label counts if it appears inside a longer recognised
   string. That leniency is not known to have produced a wrong verdict, and it is the reason `ocr_exact` is always
   reported beside the source-level label check.

**Recorded in.** [RESULTS-METHOD-B.md](RESULTS-METHOD-B.md),
[RESULTS-FOUR-ARMS.md](RESULTS-FOUR-ARMS.md), [RESULTS-ARMS-20261020.md](RESULTS-ARMS-20261020.md),
[RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md).

---

## `vlm_score.py` — structure checks and didactic questions by a vision model

**Measures.** Two batteries against a frozen rubric, one image at a time, image only (no prompt text shown):

- **structure** — the prompt's own yes/no `structure` checks, answered as
  `{"answer": "yes"|"no", "reason": "≤12 words"}`; score = checks passed / checks asked.
- **didactic** — the three frozen learner questions in `didactic-questions.json`, answered in ≤6 words, then
  judged against the reference answer by a second model call; score = answers correct / questions asked.

**Unit.** Checks passed out of checks asked. Method B is asked **57** checks over 19 images; the diffusion arms
**60** over 20 images, so the two totals are not the same denominator.

**Good.** Method B: `44/57 (77 %)` structure, `45/57 (79 %)` didactic.
**Bad.** klein-4B: `37/60 (62 %)` structure, `28/60 (47 %)` didactic.

**Calibration warning, written before the first run.** This measures the *scorer's* comprehension, not a
student's, and it can be fooled by text that repeats the answer. It is reported beside the structure checks, never
as a sole verdict; an arm that scores high here while failing structure is suspicious rather than good
([PROTOCOL-2D.md](PROTOCOL-2D.md)). The scorer is cloud `deepseek-flash`, identical for every arm so its bias
cancels between arms — and answers are stored raw so the judging can be re-read.

**Recorded in.** [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md) (method B scorecard),
[RESULTS-ARMS-20261020.md](RESULTS-ARMS-20261020.md), [RESULTS-FOUR-ARMS.md](RESULTS-FOUR-ARMS.md).

---

## `validate_structure.py` — countable structure in method B's SVG source

**Measures.** Per document, against `structure-expectations.json`: `boxes` (non-background `<rect>`), `connectors`
(`<line>` plus `<path>` with ≤2 curve commands), `labels` (non-empty `<text>`). Each expectation is
`<field>_exact`, `<field>_at_least`, or absent — absent means measured and not enforced. A mismatch can trigger
one repair round that re-asks the model for corrected counts.

**Unit.** Documents structurally correct / documents; plus one line per field.

**Good.** v2 expectations: **19/19** documents, and **10/10** on the five prompts whose counts are genuinely
pinned.
**Bad.** v1 expectations: **8/19**, and most of those "failures" were the instrument's.

**Calibration error already earned.** v1 applied **exact equality everywhere** on the assumption that a prompt
pins counts. It does not: `floor-1` specifies two bedrooms so `BEDROOM` appears twice (5 text elements is
correct); `timeline-1`'s axis plus four tick marks are connectors (5 is correct); `org-1`'s right-angle connector
is three segments (3 is correct); `blueprint-1` carries dimension text as legitimate extras; `process-1` is a
swimlane where exact counts cannot apply. Fix: three kinds of constraint, with the reason for each written into
`structure-expectations.json`. The acknowledged cost is that v2 now passes everything, and only has teeth on the
prompts whose structure is pinned. Recorded in
[RESULTS-STRUCTURE-VALIDATOR.md](RESULTS-STRUCTURE-VALIDATOR.md).

**Recorded in.** [RESULTS-STRUCTURE-VALIDATOR.md](RESULTS-STRUCTURE-VALIDATOR.md) (8/19 → 19/19),
[RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md) (10/10 on pinned prompts).

---

## `validate_shape.py` — shape proxies where counting is blind

**Measures.** For the five free-form prompts in `shape-rules.json` (`process-1`, `blueprint-1`, `circuit-1`,
`floor-1`, `tree-1`): long horizontal dividers and lane crossings, circles, parallel plates (gap and length), filled
dark wall bands, thick strokes, dark background, one-circle-above-the-rest nodal layout. Each rule is a
predicate plus the question it stands in for.

**Unit.** Documents passing every shape rule / documents (9 SVG documents), plus per-rule measurements.

**Good.** After the fourth calibration: **8/9** documents pass every check.
**Bad.** v1 as first written: **2/9**; v2: 5/9; v3: 7/9.

**Calibration errors already earned.** Four rounds, every one a defect in the proxy rather than in a drawing:

1. **Lane crossing measured against the canvas midline.** `process-1`'s crossing exists at `x=850, y=220 -> 440`
   while the lane divider sits near y≈360; a midline test can never see it. Fix: measure against the *detected*
   divider.
2. **Capacitor plates tested against an arbitrary 40 px gap and no length window.** The plates are **56 px** apart
   and **256 px** long on one seed against **64 px** on the other. Fix: gap becomes 0.08 x width; length window
   8-400 px.
3. **Walls tested as `stroke-width >= 4`.** `floor-1`'s walls are filled black rects **32 px** thick with no
   `stroke-width` attribute at all — the check was measuring a property the drawing never used. Fix: filled dark
   narrow rects.
4. **Dimension arrows tested as SVG `<marker>`.** `blueprint-1` emits none, so arrowheads are absent or separate
   primitives; a marker-based test cannot see them. Fix: dropped as unmeasurable, rather than scored as failure.
5. **A connector counted only if it spanned the boundary nearest the centre.** Fix: any detected lane boundary.

**Recorded in.** [RESULTS-SHAPE-CHECKS.md](RESULTS-SHAPE-CHECKS.md) (2/9 → 5/9 → 7/9 → 8/9).

---

## `validate_topology.py` — closure, crossing direction, arrowheads

**Measures.** Three properties the count and shape checks cannot reach:

- `rooms_closed` — flood-fill of the wall mask, counting enclosed regions.
- `crossing_direction` — connectors spanning a detected lane divider, and whether each goes downward.
- `arrowheads` — polygons, multi-vertex paths, and short segments that could form a head.

**Unit.** Per-document counts and pass/fail per property.

**Good.** Crossing direction: **2/2** documents pass (1 and 2 connectors spanning the divider, all downward).
**Bad.** Arrowheads on `blueprint-1-s1`: **0** polygons, 0 multi-vertex paths, 0 short segments — a real miss,
confirmed independently by OCR reading 0/2 labels.

**Calibration error already earned.** `rooms_closed` **contradicts the specification it was checking**: the prompt
asks for *one door gap in each room wall*, so rooms joined by their doors are correct, and the flood-fill scored 1
and 3 "enclosed" regions while the drawing was right about the thing being measured. The criterion is retired
with its reason recorded in `structure-expectations.json`. Its replacement is the label-multiset check below,
plus the door-gap scan. Recorded in [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md).

**Recorded in.** [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md).

---

## `validate_doors.py` — partitions and door gaps, from the raster

**Measures.** Rebuilds the wall mask from the SVG's dark-filled shapes, finds *partitions* (runs of columns or
rows where the mask covers ≥35 % of the perpendicular span) and *gaps* inside each partition (non-wall runs of at
least 10 px). Bands within 10 % of an edge are excluded as **perimeter**. A document passes with at least one
internal partition in each direction, each carrying one or two gaps.

**Unit.** Partitions per direction with gap counts; documents passing / documents.

**Good.** `floor-1-s1`: 2 vertical partitions with 1 and 1 gaps, 2 horizontal with 2 and 2 gaps — every internal
wall carries a door.
**Bad.** `floor-1-s0`: 2 vertical (2, 2) but **0 horizontal partitions**, so the two rows of rooms are open into
each other; **1/2** documents pass.

**Calibration errors already earned.** Both in v1, both mine: (1) the **outer walls were counted as partitions**
— they span the canvas and carry zero gaps, so both drawings failed; fixed by the perimeter exclusion. (2) v1
demanded **two vertical partitions and one horizontal**, which reversed the verdict on the drawing that is right
(2 vertical + 2 horizontal) and encoded a grid shape the prompt never asked for; the rule is now one internal
partition per direction and the content of the check is the gap count. Recorded in
[RESULTS-DOORS.md](RESULTS-DOORS.md).

**Recorded in.** [RESULTS-DOORS.md](RESULTS-DOORS.md).

---

## `validate_labels_multiplicity.py` — the multiset of text nodes

**Measures.** For every prompt carrying a `label_multiplicity` in `structure-expectations.json`, counts each
expected name in the SVG's `<text>` nodes with a whole-word, case-insensitive match and compares the multiset to
the spec.

**Unit.** Documents passing / documents; per-name found-versus-expected counts.

**Good.** `floor-1-s1`: `BEDROOM 2, BATH 1, KITCHEN 1, LIVING 1` — as specified.
**Bad.** `floor-1-s0`: `BEDROOM 2, BATH 2, KITCHEN 1, LIVING 1` against an expected `BATH 1`; **1/2** documents
pass.

**Calibration error already earned.** It exists *because* two other checks were blind: neither "at least N labels"
nor "every required string present" can see a **duplicate**, since every required string is present in a duplicate
drawing. The duplication was found by looking at the render after the closure test was retired. Recorded in
[RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md).

**Recorded in.** [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md).

---

## `check_containment.py` — are the balls inside the table?

**Measures.** From either artifact: the table rectangle (largest `<rect>` that is not a ≥90 % canvas background
and is larger than 50 px each way; for `.kicad_pcb`, the `gr_rect` start/end), and the balls (circles with
`6 <= r <= 30`, or `gr_circle` centre+radius). Reports `{table, balls, inside, outside, aspect}` and, for the
KiCad path, up to six `outside_centres`.

**Unit.** Ball counts; aspect as a dimensionless ratio.

**Good.** Method B's pool diagram: `{inside: 22, outside: 0}` with aspect `2.0`.
**Bad.** Method E's: `{inside: 10, outside: 12, outside_centres: [...]}` — the rack is beyond the right rail.

**Calibration errors already earned.** None recorded for this file. The parser faults that shaped it are: KiCad 10
writes `.kicad_pcb` coordinates in **millimetres** (an early reader divided by 10^6 and produced nothing), and
nested S-expressions defeated regex, so shapes are extracted by balanced parentheses and selected by layer. Both
recorded in [RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md).

**Recorded in.** [RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md).

---

## `check_geometry_v2.py` — seven checks that locate instead of counting

**Measures.** Per pool-table SVG: table box, then seven checks — `table_2to1` (aspect within 1.8-2.2),
`pockets_long_rail` (exactly 2 long-rail side pockets, 4 corners, 0 short-rail), `rack_apex` (leftmost interior
ball within 6 % of the centre line), `rack_back_row` (five rightmost balls span no more than one ball diameter in
x), `cue_ball_spot` (centre line, at the head quarter ±10 % of width), `cue_angle` (20-45° off axis, from a
`<rect>` or `<line>` at least 80 px long), `label_CUE`.

**Unit.** Checks passed / 7.

**Good.** v2 prompt, bare: **6/7**.
**Bad.** v1 prompt, bare: **2/7** — side pockets on the short rails, apex off centre, back row not parallel, cue
at 0°.

**Calibration errors already earned.** Three, all before it could judge anything:

1. **The pockets rule demanded `short_rail == 2`** while counting corners separately, so a *correct* table failed
   it. Fix: 2 long-rail side pockets + 4 corners + **0** short-rail.
2. **Cue angle was read without normalising direction**: a cue drawn tip-to-butt reads **-153.4°**, whose axis is
   **26.6°**. Fix: fold into 0-90 and ignore elements within 2° of an axis (a horizontal line is not a cue at
   all).
3. **The table rect was discarded as "background"** whenever the SVG's own width and height equalled the table's,
   which a well-formed drawing does. Fix: keep the largest rect when it is the only candidate.

Known limitation, not an error recorded elsewhere: cue rotation is read from the element's own `transform` only,
so a cue inside `<g transform="rotate(...)">` reads 0° here. `check_ace_geometry.py` accumulates group rotations;
this file does not. Recorded alongside [RESULTS-PROMPT-V2.md](RESULTS-PROMPT-V2.md) and
[RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md).

**Recorded in.** [RESULTS-PROMPT-V2.md](RESULTS-PROMPT-V2.md), [RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md).

---

## `check_ace_geometry.py` — the app's own constants, asserted

**Measures.** In the app's coordinate system (`viewBox="-8 -8 116 66"`): playfield 2:1 within 0.06, four corner
pockets at `(1.5,1.5) (98.5,1.5) (1.5,48.5) (98.5,48.5)` within 1.5, two side pockets at `(50,1)` and `(50,49)`,
at least five of the six long-rail diamonds `{12.5, 25, 37.5, 62.5, 75, 87.5}` **and none at 50**, rack apex on
the foot spot `(75,25)`, at least four rack rows, back row parallel to the foot rail, all balls inside, cue axis
20-45°, a `CUE` text node, and — the eleventh — **the cue butt on the head side** (`butt x < 25`).

**Unit.** Checks passed / 11.

**Good.** v4: **10/11**. The one miss is the eleventh check, and it is the finding: the cue runs from `(25,25)` to
`(48.74,12.5)`, so its butt points **+x, toward the rack**, i.e. the shot is backwards.
**Bad.** v3: **9/10**, failing `back_row_parallel` — rows advance by 1.125 in x (one radius) instead of along y,
so the rightmost five balls span **3.38 units** of x where a back row must span 0.

**Calibration errors already earned.**

1. **Diamonds are rotated `<rect>`s, not `<polygon>`s** in this model's output, so counting polygons found zero.
2. **The cue sat inside `<g transform="rotate(30 25 25)">`** and the checker read the child's own attributes,
   reporting 0°. Fix: `walk()` accumulates group rotation down the tree.
3. Both were the same mistake: **trusting a structure the artifact did not use**.
4. The **20-45° cue window and its primitives** are the invented acceptance criteria described in
   [RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md). They are still in this file; the prompt specifies *"angled
   toward the cue ball"* and no degrees.

**Recorded in.** [RESULTS-V3-ACE-CONSTANTS.md](RESULTS-V3-ACE-CONSTANTS.md),
[RESULTS-V4-FINAL-PASS.md](RESULTS-V4-FINAL-PASS.md), [RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md).

---

## `ocr_check.py` — one image, one required-label list

**Measures.** Runs `rapidocr` over one PNG, prints every recognised string, and for each required label reports
whether it appears (case-insensitive substring); `ocr_exact` is the fraction found.

**Unit.** Fraction.

**Good.** `1.0` — every required string read verbatim.
**Bad.** `0.0` — `blueprint-1-s1` scored 0/2 on labels A and B.

**Calibration errors already earned.** None for this script itself; it is the raw probe. The OCR errors that
matter are recorded against the arms: reading 512x512 is not comparable to 1024 (code 0.876 → 0.261,
[RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md)), and a low score is not always an OCR weakness — klein's `0.75`
was a real misspelling, `VAILDATE`, read exactly as drawn
([RESULTS-KLEIN-SMOKE.md](RESULTS-KLEIN-SMOKE.md)).

**Recorded in.** no recorded run of this script as such. The identical `rapidocr` read is recorded in
[RESULTS-METHOD-E.md](RESULTS-METHOD-E.md) (4/4 labels) and
[RESULTS-METHOD-E-LLM.md](RESULTS-METHOD-E-LLM.md) (2/2 and 4/4).

---

## `hard_arm.py` — method B on the pool-table task

**Measures.** Asks a local Ollama coder model for one pool-table SVG (optionally with a worked example), then
reports `svg_bytes`, `rects`, `circles`, `small_circles` (r ≤ 12), `filled_circles`, `ball_sized` (6 ≤ r ≤ 30),
`ball_rows` (distinct `round(cy/20)`), `angled_shapes` (rects with `transform="rotate"`, plus lines with both
|dx| and |dy| > 60), `outer_aspect`, `text_values`, `has_CUE`.

**Unit.** Counts; aspect as a ratio.

**Good.** `outer_aspect 2.0`, `ball_rows 5`, `has_CUE` true — the global geometry holds.
**Bad.** `angled_shapes 0` while fifteen racked balls and a cue ball are present: the cue is a horizontal
rectangle. That is the recorded defect, scored **6/7** on the task's own checks.

**Calibration errors already earned.** `angled_shapes` only sees a `transform="rotate"` on a rect or a line that
is diagonal in **both** axes, so a cue rotated by a *group* transform, or one nearly horizontal, is invisible and
the cue reads 0°. That is why `rescore_angles.py` exists; after it honoured `rotate(theta cx cy)`, the same four
configurations measured `0°, 0°, -15.0°, 30.0°` instead of `0°` everywhere. Recorded in
[RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md).

**Recorded in.** [RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md) (6/7, cue horizontal),
[RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md).

---

## `hard_arm_kit.py` — the same task with a primitive kit, and with `<rect>` banned

**Measures.** Same generation path, but with the `line_at(x, y, degrees, length)` kit and optional
`--lines-only` which forbids `<rect>` entirely. Four acceptance criteria per attempt, over three attempts:
`aspect` (1.7-2.3), `containment` (every ball inside), `cue_angle` (20-45°), `label_CUE`; failures are fed back as
text on the next attempt.

**Unit.** Criteria passed per attempt; attempts passing / attempts.

**Good.** With `<rect>` banned: **3/3** attempts off-axis, at `-11.3°, 30.0°, 30.0°`.
**Bad.** With `<rect>` allowed: `0°` in **3/3** attempts, and the model used no `<line>` at all; containment held
on the first attempt and failed on the next two (`22/22`, then `20/22` twice, in `result-hard-kit.json`).

**Calibration errors already earned.** The first scoring of the lines-only run was a **false failure**: it demanded
a cue within **20-45°** and `stroke-width >= 4`. Both were invented — the prompt asks only for *"angled toward the
cue ball"* and no stroke width, and the model omitted `stroke-width` (defaulting to 1). Under the prompt's actual
requirement all three attempts pass, two at exactly **30.0°**. Recorded in
[RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md). The same invented window survives in
`check_geometry_v2.py` and `check_ace_geometry.py`.

**Recorded in.** [RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md),
[RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md).

---

## `hard_v2.py` — prompt v2 through the seven-check geometry scorer

**Measures.** Generates with `hard-task-pooltable-v2.json` (optionally with the kit appended to the prompt) and
scores the result with `check_geometry_v2.analyse`, recording `aspect`, pocket classes, apex, back row, cue-ball
spot, cue angle and the CUE label.

**Unit.** Checks passed / 7, plus generation seconds.

**Good.** v2 bare: **6/7**, with apex and back row both correct — the two the v1 prompt could not express.
**Bad.** v2 kit: **5/7** (apex, back row); v1 kit: **3/7**.

**Calibration errors already earned.** None of its own; it inherits `check_geometry_v2`'s three, which were found
and fixed while producing this table. Recorded in [RESULTS-PROMPT-V2.md](RESULTS-PROMPT-V2.md).

**Recorded in.** [RESULTS-PROMPT-V2.md](RESULTS-PROMPT-V2.md), `result-hard-v2.json`.

---

## `hard_v3.py` — prompt v3 through the ACE constants

**Measures.** Generates with `hard-task-pooltable-v3.json` (app constants in the prompt) and scores with
`check_ace_geometry.analyse`: ten checks, generation seconds, and the full measurement dict written to
`result-hard-v3.json`.

**Unit.** Checks passed / 10.

**Good.** **9/10** — every constant honoured except the rack.
**Bad.** `back_row_parallel` fails: the rack is a rhombus with x steps of 1.125 (one radius) and y steps of 1.949.

**Calibration errors already earned.** None of its own; the three detector corrections made while producing this
result belong to `check_ace_geometry.py` and are recorded in
[RESULTS-V3-ACE-CONSTANTS.md](RESULTS-V3-ACE-CONSTANTS.md).

**Recorded in.** [RESULTS-V3-ACE-CONSTANTS.md](RESULTS-V3-ACE-CONSTANTS.md), `result-hard-v3.json`.

---

## `hard_v4.py` — prompt v4 (rack as a lattice formula)

**Measures.** Generates with `hard-task-pooltable-v4.json` and scores with `check_ace_geometry.analyse`; writes
`hard/pooltable-v4-bare.svg` and `result-hard-v4.json`.

**Unit.** Checks passed / 10 (or / 11 with the direction check).

**Good.** **10/10** on the ten geometric checks.
**Bad.** **10/11** once the eleventh check exists: angle, tip position, length and containment all pass while the
cue butt points at the rack.

**Calibration errors already earned.** The ten checks themselves were the error of omission — they measured
geometry and none measured **direction**, so a semantically inverted diagram scored perfect. Fix: assert that the
butt's x is less than the cue ball's x. Two other recorded traps for this file's prompt: the model's first reply
was a bare code fence until the prompt said *"output the raw SVG, starting at `<svg`, do not use code fences"*,
and a proportion ("row spacing 1.95") is not a lattice (`x = 75 + k*1.9493`). Recorded in
[RESULTS-V4-FINAL-PASS.md](RESULTS-V4-FINAL-PASS.md).

Cosmetic defect worth knowing: this file's docstring is copy-pasted from `hard_v3.py` and its output filename is
hard-coded to `pooltable-v4-bare.svg`.

**Recorded in.** [RESULTS-V4-FINAL-PASS.md](RESULTS-V4-FINAL-PASS.md), `result-hard-v4.json`.

---

## `cad_arm.py` — method E across the corpus

**Measures.** Asks a local coder model for a `pcbnew` script, runs it with KiCad's own Python, feeds any error
back for up to `--attempts` rounds, exports SVG with `kicad-cli pcb export svg --mode-single
--exclude-drawing-sheet`, then scores **from the source**: path count, `<text>` node count, and required-label
completeness. It also writes a recoloured copy (strokes to black) so a raster/OCR/VLM pass is possible at all —
recorded as a rendering step, never as part of the artifact.

**Unit.** Exit code, path count, text-node count, labels present / required.

**Good.** `flow-1`: exit 0, **160 paths, 4 `<text>`** (START, VALIDATE, STORE, DONE), 9.9 KB, **4/4** labels, and
on the render axis 100 % / OCR 4/4. `circuit-1`: 54 paths, 2 text, 3.9 KB. `blueprint-1`: 31 paths, 2 text,
3.2 KB — **3/3 first attempt**, ~15 s per script.
**Bad.** Harness A and B (prose descriptions of the API): **0/9** scripts each, all failing with
`AttributeError: 'BOARD' object has no attribute 'SetWidth'`.

**Calibration errors already earned.**

1. **The recolour assumed the wrong syntax.** It searched SVG **attributes** (`stroke="#F2EDA1"`) while KiCad
   writes colour as **CSS inside `style`** (`stroke:#F2EDA1`); the render stayed pale yellow, edge detection found
   zero segments, and the easy-but-wrong conclusion was "KiCad diagrams cannot be scored". Fix: rewrite the style
   value. After it, the same diagram scored axis 100 %, OCR 4/4, 62 traced paths. Recorded in
   [RESULTS-METHOD-E-LLM.md](RESULTS-METHOD-E-LLM.md).
2. **Raster proxies applied to a vector-native arm.** See `trace_score.py`, error 2.
3. **Retrying at temperature 0 with one seed re-sends the same sample** — the retry loop in harness B was a no-op
   dressed as a fix; only changing the *prompt's information* changed the outcome (0/9 → 3/3).

**Recorded in.** [RESULTS-METHOD-E-LLM.md](RESULTS-METHOD-E-LLM.md), `result-method-e-cad.json`,
[RESULTS-METHOD-E.md](RESULTS-METHOD-E.md).

---

## `generic_arm.py` — any diffusion arm, any resolution, one code path

**Measures.** Drives ComfyUI's HTTP API with one of four frozen profiles (`klein`, `zimage`, `schnell`, `dev` —
each a pinned workflow graph and step/CFG set), at `--size` 1024 or 512, recording wall time per image and peak
VRAM sampled every 250 ms, plus the output path per image.

**Unit.** Seconds; MiB used (and MiB free); image counts.

**Good.** Z-Image at 512: median **2.0 s**, peak **9,269 MiB (2,958 free)** — comfortably above the floor.
**Bad.** FLUX.1-dev at 512: **11,803 MiB (424 free)** — a floor breach; the arm ran anyway, reported as a breach
rather than silently accepted.

**Calibration errors already earned.** The floor claim itself: peak VRAM is **resolution-dependent**, so a claim of
compliance must name the resolution (Z-Image passes at 512 and is marginal at 1024). Recorded in
[RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md).

**Recorded in.** [RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md).

---

## `klein_arm.py`, `flux_arm.py`, `zimage_arm.py` — the first three diffusion arms

**Measures.** The same protocol as `generic_arm.py`, one file per model: per-image wall time, peak VRAM sampled at
250 ms, workflow parameters, output path, and a determinism check (same prompt and seed twice, PNG sha256
compared). A failed job is recorded, never retried silently.

**Unit.** Seconds; MiB; paths/nodes via `trace_score`; OCR exact via `score_arm`.

**Good.** klein smoke: first image **16.1 s** (includes model load), **37** traced paths — identical to the
hand-drawn control — **1,042** nodes, axis **95.8 %**, 2 major colours, though only **3/4** labels read.
**Bad.** klein on the corpus: OCR **0.614** with **3/20** fully labelled; the miss is a real misspelling
(`VAILDATE`), not an OCR weakness.

**Calibration errors already earned.** A low label score was nearly booked as an OCR limitation; reading the
image showed the model had transposed two letters. The instrument was right and the model was wrong — which is
the exception, not the rule, in this folder. Also: none of the three arms is deterministic at fixed seed (PNG
hashes differ between identical runs), so every small difference between arms is read against that noise.
Recorded in [RESULTS-KLEIN-SMOKE.md](RESULTS-KLEIN-SMOKE.md) and
[RESULTS-ARMS-20261020.md](RESULTS-ARMS-20261020.md).

The usage lines in `flux_arm.py` and `zimage_arm.py` still say `klein_arm.py` — copy-paste, not behaviour.

**Recorded in.** [RESULTS-KLEIN-SMOKE.md](RESULTS-KLEIN-SMOKE.md), [RESULTS-ARMS-20261020.md](RESULTS-ARMS-20261020.md),
[RESULTS-FOUR-ARMS.md](RESULTS-FOUR-ARMS.md), [RESULTS-512-VARIANT.md](RESULTS-512-VARIANT.md),
`result-smoke.json`, `result-arm-*.json`.

---

## `svg_arm.py` — generation only, for method B

**Measures.** Runs the frozen diagram corpus through a local Ollama model and stores what it returns verbatim,
recording whether the reply contained a parseable `<svg>` document. Scoring is deliberately *not* here, so a
generation run never depends on the scorer being right.

**Unit.** Valid documents / attempts; generation seconds.

**Good.** **19/20** valid documents, 8-22 s per image.
**Bad.** One **180 s** timeout returned no `<svg>` at all.

**Calibration errors already earned.** None of its own. Its output is where the `VAILDATE` miss and the omitted
`blueprint-1` labels came from — both real model failures, confirmed by two independent instruments. Recorded in
[RESULTS-METHOD-B.md](RESULTS-METHOD-B.md) and [RESULTS-SHAPE-CHECKS.md](RESULTS-SHAPE-CHECKS.md).

**Recorded in.** [RESULTS-METHOD-B.md](RESULTS-METHOD-B.md).

---

## `repair_labels.py` — validate labels in source, re-ask once

**Measures.** For each SVG, the required labels present as `<text>`, the missing ones, then one repair round
(`seed 4242`, temperature 0) asking for the complete corrected document with the missing labels added; records
before/after label counts per document and in total.

**Unit.** Labels present / labels required; documents fully labelled / documents.

**Good.** After one round: **70/70** labels, **19/19** documents fully labelled, no new weights.
**Bad.** Before: **68/70** (0.971) by source, and 0.876 by OCR on the render.

**Calibration errors already earned.** None of its own — it is the correction for `score_arm.py`'s OCR
under-report. Its value is that it moved the measurement off the render and onto the source, which is where a
code arm's text actually lives. Recorded in [RESULTS-FOUR-ARMS.md](RESULTS-FOUR-ARMS.md).

**Recorded in.** [RESULTS-FOUR-ARMS.md](RESULTS-FOUR-ARMS.md), [RESULTS-TOPOLOGY.md](RESULTS-TOPOLOGY.md),
`result-method-b-repair.json`.

---

## `compose_compare.py` — two renders side by side, highlights measured not eyeballed

**Measures.** Nothing about a model. It crops each render to its non-white bounding box, scales both panels to a
common width, and draws a highlight box whose coordinates come from measured geometry — `cue_bbox_svg()` finds
the cue in the SVG source (a rect ≥100 px wide and 4-20 px tall, or a near-horizontal thick line) rather than
guessing pixels.

**Unit.** Pixels, for annotation.

**Good / bad.** It has no verdict; it makes a verdict checkable by eye. The artifact is
`hard/pooltable-compare.png`: method B's horizontal cue on the left, method E's rack past the right rail on the
right.

**Calibration errors already earned.** None recorded. It exists *because* the alternative — highlighting from
guessed pixels — is indistinguishable from a correct highlight when it happens to land right.

**Recorded in.** [RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md).

---

## `compose_grid.py` — the four-configuration 2x2 progression

**Measures.** Pixels only: crops four renders to content, resizes to a fixed cell width, captions each with a
hard-coded title and subtitle, writes `hard/pooltable-progression.png`.

**Unit.** Pixels.

**Good / bad.** No verdict of its own. Its captions state the cue angle per panel; panel 3's `−15.0°` is the value
in `result-hard-angles-rescored.json` for the kit-with-rects configuration.

**Calibration errors already earned.** The captions are literals, not read from any result file, so the image
cannot be cited as evidence — only the JSON can. Treat the picture as a pointer to the numbers.

**Recorded in.** no recorded run. No RESULT file cites `hard/pooltable-progression.png`; the measurements it
illustrates are in [RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md) via `rescore_angles.py`.

---

## `compose_v34.py` — v3 and v4 side by side

**Measures.** Pixels plus hard-coded captions (`v3 · 9/10`, `v4 · 10/10 (10/11 with the direction check)`),
cropping each panel to content and printing the caption block underneath.

**Unit.** Pixels.

**Good / bad.** No verdict of its own; the scores in the captions are the ones recorded in the RESULT files.

**Calibration errors already earned.** Two things to know before reading it: the captions are literals rather
than values read from `result-hard-v3.json` / `result-hard-v4.json`, and the file carries a dead `to_px()`
function that returns a meaningless `(px, 0)` tuple — it is not called, and it must not be reused.

**Recorded in.** no recorded run. The scores it captions are in
[RESULTS-V3-ACE-CONSTANTS.md](RESULTS-V3-ACE-CONSTANTS.md) and
[RESULTS-V4-FINAL-PASS.md](RESULTS-V4-FINAL-PASS.md).

---

## `crop_render.py` — crop a render to its content

**Measures.** The bounding box of non-white pixels in a render, plus a 12 px pad, using PIL rather than the
exported XML.

**Unit.** Pixels; output dimensions.

**Good / bad.** Good: the drawing fills its frame. Bad: cropping by rewriting the SVG `viewBox` — which was tried
first — produced a **21 × 26 mm** box that cut the drawing away, because KiCad's export mixes relative and
absolute path commands. Fix: crop the raster by non-white pixels, which needs no assumption about the exporter's
syntax.

**Calibration errors already earned.** The viewBox-rewrite assumption, above. Recorded in
[RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md).

**Recorded in.** [RESULTS-HARD-TASK.md](RESULTS-HARD-TASK.md).

---

## `sharp.py` — Laplacian variance, the blur detector

**Measures.** For each PNG: `laplacian_var` (variance of the Laplacian of the greyscale image), `mean|grad|`, and
`p99|grad|`. Higher variance means more high-frequency content.

**Unit.** Variance in grey-level² units; it is a relative measure and is only meaningful between images of the same
prompt and size.

**Good.** Working arms on `flow-1`: klein `731`, Z-Image `834`, schnell `299`; hand-drawn control `1,200`.
**Bad.** FLUX.1-dev on this stack: `1.5` at 20 steps, `5.4` at 30 steps, `1.1` with the fp8 checkpoint, `0.6` with
classic CFG — two to three orders of magnitude below a working arm.

**Calibration errors already earned.** The instrument was fine; the *interpretation* was resolution-dependent.
The same dev weights measure `1.1` at 1024 and `236.6` at 512, so the blur is a 1024x1024 failure in this stack,
not a property of the model family, and the arm is recorded as **blocked**, never as zero. Recorded in
[RESULTS-DEV-BLOCKED.md](RESULTS-DEV-BLOCKED.md).

**Recorded in.** [RESULTS-DEV-BLOCKED.md](RESULTS-DEV-BLOCKED.md).

---

## Also in this folder

- **`make_synthetic.py`** — draws the `flow-1` prompt by hand with known ground truth (4 boxes, 3 vertical
  connectors, 4 labels, 2 ink colours, everything axis-aligned). It is the positive control for `trace_score.py`
  and the reason that scorer is trusted at all: it recovered `paths 37`, `nodes 447`, `axis 100.0 %`,
  `colours_major 2`. Recorded in [INSTRUMENT-VALIDATION-20261020.md](INSTRUMENT-VALIDATION-20261020.md).
- **`rescore_angles.py`** — re-measures the cue angle for the four pool-table configurations while honouring
  `transform="rotate(theta cx cy)"`, which the earlier detector ignored. Output: `0°, 0°, -15.0°, 30.0°` in
  `result-hard-angles-rescored.json`, and `-11.3°, 30.0°, 30.0°` for the three lines-only attempts in
  `result-hard-kit-lines-rescored.json`. Recorded in
  [RESULTS-KIT-EXPERIMENT.md](RESULTS-KIT-EXPERIMENT.md).
- **`pocket_rack.py`** — classifies edge circles as corner / long-rail side / short-rail side pockets and infers
  which way the rack points from the ball cluster. It was the scratch instrument behind the v1 pocket defect.
  **no recorded run**: no RESULT file cites its output, and its verdicts were superseded by
  `check_geometry_v2.py`, which writes a scored table.

## score_arm.py label matching: exact tokens, corrected 2026-10-21

`ocr_hits` originally asked whether a required label was *anywhere inside* a read string:
`any(label.lower() in found.lower())`. That credits `7` inside `70`, `120` inside `1120`, and `PC1` inside `PC10`.
On 2026-10-21 a tree render whose nodes were labelled `70, 40, 66, 30, 212, 15` - the prompt's own radius,
rows and coordinates - still scored 5/7, and the frozen one-liner scored 6/7 on the same task; the true values are
0/7 and 1/7. Matching is now on whole alphanumeric tokens (`re.split` on non-alphanumerics), which is what
"label read exactly" was always meant to say.

Consequence: **scores written before 2026-10-21 are inflated** wherever a required label is short or numeric, and
are not comparable with re-scored arms without a re-run. The four arms of 2026-10-21 were all re-scored; their
documents carry both numbers and the retraction explicitly.
