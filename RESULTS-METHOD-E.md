# Method E (KiCad) harness: a true-vector diagram, and why raster proxies do not apply to it

The brief's method E is "drive CAD with scripts for exact vector geometry". This establishes the **harness** for
that arm on this machine: KiCad 10.0.6, `pcbnew` 10.0.6, `kicad-cli pcb export svg`. The script was written by me
here, to prove the pipeline; the model-written variant for the frozen corpus is the next step.

## What works, measured

| step | result |
|---|---|
| `pcbnew` board construction from Python | works - 23 drawings on F.SilkS (4 boxes, 3 connectors, 4 labels) |
| `kicad-cli pcb export svg --mode-single` | works, and needs `--mode-single` plus a **file** path (`-o dir` silently produced nothing) |
| `--exclude-drawing-sheet` | works, and is **required**: without it the export carries a 33-group drawing frame with 23 extra `<text>` elements |
| artifact | **172 paths, 4 `<text>` elements, 10.6 KB**, from a 60x138 mm board |
| OCR on a render | **4/4 labels** |

**The artifact is a true vector with real text and no tracing step at all** - which is precisely the property the
whole track is chasing, and it is achieved by construction rather than earned by a model.

## The measurement problem this exposed

Rasterising that SVG for the trace metrics gave **0 detected straight segments twice** - once at 1024x1024
(letterboxed, strokes sub-pixel) and once at native aspect with 2x device scale. The reason is not geometry: the
export draws F.SilkS in **pale yellow on white**, so `cv2.Canny(50,150)` sees nothing while the OCR still reads the
text.

**So the raster proxies are invalid for method E, exactly as OCR-at-512 was invalid for cross-resolution
comparison.** A vector-native arm must be scored **from the source**: path count, node count, text-node count,
file size, label completeness, and geometric properties read directly from coordinates - not from a picture of it.
Trying to render it only measures the exporter's colour choices.

## What method E therefore answers, and what it does not

- **It answers geometry and editability perfectly**: exact coordinates, real `<text>`, 10.6 KB, every path a
  primitive rather than a traced contour. No diffusion arm or traced SVG comes close on the axes that matter for
  teaching diagrams.
- **It does not answer semantics**: whether the diagram teaches anything. That is the didactic score's job, and it
  needs a render a human or model can read - which is why the exporter's colour and the page frame have to be
  controlled before the VLM can judge these images.
- **The cost is different in kind**: a KiCad script is a program that must run. Where method B can fail by omitting
  a label, method E can fail by not running at all - the same validity axis measured for the LLM's SVG.


## The model-driven arm: the script failed and the artifact succeeded

Asked `qwen3-coder:30b` for a `pcbnew` script drawing the `circuit-1` prompt (RC filter, R1 in series, C1 to
ground, terminal dots), then ran whatever it produced with KiCad's own Python.

| fact | value |
|---|---|
| generation | **48.9 s**, 4,683 characters of Python |
| script exit code | **1** - `NameError: name 'OK' is not defined`, from `print OK` (Python 2 syntax) |
| board written anyway | **yes**, 4,522 bytes, 14 drawing primitives |
| SVG export | **48 paths, 2 `<text>` elements** whose content is exactly `R1` and `C1` |
| OCR on the render | **2 / 2** |

**The script crashed after saving the board.** A harness that trusts the exit code calls this a failure; a harness
that trusts file existence calls it a success. Both are wrong: the artifact is correct, and the reason it exists is
that `SaveBoard` ran before the bad `print`.

This is the **mirror image of the FLUX blur** from earlier today - there, the job returned success and the image was
broken; here, the job returned failure and the drawing was right. Together they make the rule concrete: **the exit
status is not evidence about the artifact, in either direction.** Every arm in this track is therefore scored by
inspecting what it produced.

### What this says about method E as a production route

- **A coder model can drive the CAD API on the first attempt**, getting the symbols, the labels and the topology
  into a real vector file - and the resulting artifact is **48 paths with genuine `<text>`**, which is leaner than
  anything traced and needs no tracing at all.
- **Its failure mode is script validity, not geometry**: the model's Python is right where it matters and wrong in
  a trivial way at the end. A one-line retry rule ("run it, if it fails, fix the reported error and rerun") would
  have converted this into a clean success - that is a harness change, not a model improvement.
- **The comparison axis for method E is therefore validity and editability**, exactly as recorded when the arm was
  declared, and the raster metrics stay out of its column.

## Next for this arm

1. **Recolour the export** (or plot a different layer) so the raster is judgeable by the VLM and by edge detection -
   otherwise method E cannot enter the same table as the others.
2. **Have the local model write the `pcbnew` script** for the `circuit-1` and `blueprint-1` prompts, which is the
   actual method-E claim: can a coder model drive a CAD API reliably?
3. **Score from the source** first (paths, text, validity) and only then through a render.
