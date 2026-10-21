# Hard task: a top-down pool table in shot setup - both methods score 6/7, failing different checks

Task declared in `hard-task-pooltable.json` before any arm ran: a 2:1 table, six pockets, fifteen balls in a
5-row triangle rack, a cue ball labelled CUE, and a cue stick **angled** toward it. Kept out of the main corpus so
the earlier comparisons stay valid.

Side-by-side render with each failure boxed: **`hard/pooltable-compare.png`** (left = method B, the
horizontal cue; right = method E, the rack past the right rail). Composed by `compose_compare.py`, which anchors
each highlight to measured geometry rather than eyeballed pixels.

## The scorecard

| check | measurement | method B (code → SVG) | method E (code → KiCad) |
|---|---|---|---|
| table aspect 2:1 | outer rect w/h | **2.0** ✓ | **2.0** (80 × 40 mm) ✓ |
| six pockets | small circles | ✓ | ✓ |
| 16 balls | ball-sized circles | 15 racked + cue ✓ | 15 + cue ✓ |
| triangle rack | rows present | ✓ 1/2/3/4/5 | ✓ |
| **balls inside the table** | circle centres vs table rect | **22 / 22 inside** ✓ | **10 inside, 12 outside** ✗ |
| **angled cue stick** | rotated shape or long diagonal | **✗ horizontal** | **✓ 30°** |
| CUE label | text node | ✓ | ✓ |
| **totals** | | **6 / 7** | **6 / 7** |

**They fail differently, which is the useful part.** Method B holds the global geometry - everything inside its
frame, rack correctly placed - and flattens the one thing that needed an angle. Method E computes the angle
explicitly (`15 * cos(30°)`, `15 * sin(30°)`) and then places the rack **beyond the right rail** at x = 86.5-89.5 mm
on a table that ends at 85 mm.

A combined pipeline - code for the geometry of each element, plus a containment check before accepting - would
have caught both. That is now measurable rather than aspirational: `check_containment.py` reports
`{inside: 22, outside: 0}` for one artefact and `{inside: 10, outside: 12, outside_centres: [...]}` for the other.

## Four instrument faults found while building this test

None of these were model errors, and each one would have produced a wrong verdict:

1. **The few-shot example had no circle helper.** Method E's first attempt drew the balls as **tiny line dots**,
   because the prompt's verified example only demonstrated `seg`, `rect` and `text`. Adding `circ()` produced
   **22 circles** on the next run. The example is the interface.
2. **`--page-size-mode 2` fits the *board area*, which is empty without Edge.Cuts**, so the drawing rendered as a
   **54 × 64 px speck** on a page. `post_outline.py` now measures the drawings with `pcbnew` and adds an outline,
   giving a 93.3 × 44.4 mm export that fills its viewBox.
3. **Cropping the SVG by rewriting its viewBox failed** - I assumed uppercase absolute path commands and produced
   a 21 × 26 mm box that cut the drawing away. The raster is cropped by non-white pixels instead
   (`crop_render.py`), which needs no assumptions about the exporter's syntax.
4. **The `.kicad_pcb` parser was wrong twice**: KiCad 10 writes coordinates in **millimetres**, not nanometres (I
   divided by 10^6), and nested S-expressions defeated regex, so a balanced-parenthesis extractor replaced it and
   shapes are selected by layer.

## What this test adds that the corpus did not

The ten-prompt corpus asks for labels, counts and simple relations. This one asks for a **proportion, a triangular
arrangement and an angle simultaneously**, and it separated the two methods on the axes their own code tends to get
wrong: **global containment versus local trigonometry**. Both are machine-checkable, both were caught here, and
neither would have been caught by the OCR/structure/didactic scores that dominate the main table.
