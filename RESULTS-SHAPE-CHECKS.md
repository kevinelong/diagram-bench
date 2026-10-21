# Shape checks: four calibrations before the instrument stopped measuring itself

The count-based validator went blind on the five free-form prompts, so `validate_shape.py` measures **shape**
instead: lane dividers, lane crossings, circles, parallel plates, filled wall bands, nodal layout. Rules frozen in
`shape-rules.json` **before** the first run. What follows is the full calibration history, because the history is
the finding.

## The iterations

| version | change | documents fully passing |
|---|---|---|
| v1 (as first written) | crossing measured against the canvas midline; plates within an arbitrary 40 px; walls as `stroke-width >= 4`; dimensions as SVG markers | **2/9** |
| v2 | crossing measured against the **detected** lane divider; plate gap becomes **0.08 x width**; walls measured as **filled dark narrow rects**; marker-based dimension check **dropped as unmeasurable** | **5/9** |
| v3 | a connector counts if it spans **any** lane boundary, not only the one nearest the centre | **7/9** |
| v4 | plate length window widened to 8-400 px (one seed draws 256 px plates, the other 64 px) | **8/9** |

**Every single correction in v2-v4 was to my proxy, not to a drawing.** Inspection of the actual SVGs showed:

- **process-1**'s crossing exists (`x=850, y=220 -> 440`) while the lane divider sits near y≈360, so a
  midline-based test could never see it.
- **circuit-1**'s capacitor plates are **56 px apart** (outside a 40 px limit) and **256 px long** on one seed
  against **64 px** on the other, so both the gap and the length window were wrong.
- **floor-1**'s walls are **filled black rects 32 px thick with no `stroke-width` attribute at all** - the check
  was measuring a property the drawing never used.
- **blueprint-1** emits no SVG `<marker>`, so arrowheads are either absent or drawn as separate primitives; a
  marker-based dimension check cannot see them, and calling that a failure would have been a false accusation.

## The one genuine failure

**`blueprint-1-s1` is missing labels A and B.** It fails the source-level check *and* the OCR on the render
(**0/2**), so two independent instruments agree. That is a real model miss, and it is the same document that
failed label validation before the repair round.

## What this establishes about the instrument, and about the method

- **A deterministic shape check is worth building**, because it is free, repeatable and needs no vision model:
  after calibration it judges nine documents in under a second and agrees with OCR wherever both can see the same
  thing.
- **But a proxy must be calibrated against the artifact before it is allowed to judge it.** Four rounds of
  "the model failed" turned out to be four rounds of "my threshold encoded an assumption about how the drawing
  would be built". The rules file now carries that reasoning, so the next person inherits the calibration and not
  just the thresholds.
- **The honest score for method B's structure and shape** is therefore: **8 of 9 documents pass every check**,
  with one real label omission on `blueprint-1`, on top of the earlier result that **all 19 documents have every
  required label in source** after one repair round.

## Not measured, and stated as such

- **Dimension arrows on blueprints** - no reliable SVG feature to test; recorded as unmeasurable rather than
  passed.
- **Wall closure** (do the walls actually enclose the rooms?) - a topological property that needs a polygon
  operation, not a count.
- **Lane semantics** (is the crossing in the right direction?) - the check proves a connector spans the divider,
  not that it means what the diagram claims.
