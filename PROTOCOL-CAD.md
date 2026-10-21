# CAD-driven vector arms (methods D and E), declared 2026-10-20

The goal changed shape with this brief: the deliverable is **great teaching diagrams**, and later **animations**.
That reframes the choice. It is not "artistic vs technical"; it is **what the diagram has to explain**, because
each tool's geometry is exact by construction and the differences are in *what it can express*, *how editable
the result is*, and *whether it can be animated*.

## The decision, stated as a rule rather than a preference

| content | tool | why |
|---|---|---|
| Flowcharts, timelines, org charts, network/topology, swimlanes, annotated 2D diagrams | **LLM writes SVG directly (method B)** | exact geometry, real `<text>`, layered and editable, animatable later with CSS/SMIL or Lottie, **no install and no download** |
| 3D scenes, exploded views, camera moves, anything that becomes **animation** | **Blender `bpy` (method D)** | keyframes, camera paths and parametric objects are the point; 2D output via orthographic render, Freestyle or curve export |
| Electrical schematics, PCB topology, board outlines, silkscreen art | **KiCad `pcbnew` (method E)** | S-expressions and a real layout engine; nothing else in this list can express a netlist |
| Illustrative/photographic 2D where exactness is not the goal | diffusion + trace (method A) | already protocolised; weakest on editability, which is exactly what teaching material needs most |

**So: B first, D when motion or 3D enters, E only when the content is electrical.** KiCad is not a teaching-diagram
tool; using it for a flowchart would be a category error, and using diffusion for a labelled diagram throws away
the one property that matters when a label needs fixing at 3 a.m. - editability.

## What these arms cost, measured today

- **Blender: not installed.** Installer ~350-400 MB, plus the `bpy` module is only meaningful inside Blender's own
  Python (its bundled interpreter, not the system one). A headless run is `blender --background --python script.py`.
- **KiCad: not installed.** Installer ~1.2-1.5 GB, `kicad-cli` for exports and `pcbnew` only importable from KiCad's
  Python. There is no pip path worth using.
- **Method B: nothing to install.** This box already has local coder models, and the scoring toolchain is built.

## Metrics these arms add, and the subset rule

Exact geometry is free in D and E, so scoring them on axis alignment and path count would be measuring the tool's
own compiler. The axes that actually separate them:

| metric | question | method that should win |
|---|---|---|
| **validity** | does the script run first time, and does the document parse and render? | A (always produces an image) -> D/E/B (can fail outright) |
| **editability** | text as `<text>` or as outlines; named/layered objects; one path per semantic element | B and D; **A loses by construction** (flattened trace) |
| **animation-readiness** | is there a parameter for time - keyframes, named nodes, groups that can be tweened? | D; then B; A never |
| OCR exact, structure checks, colour discipline | unchanged from `PROTOCOL-2D.md` | - |
| trace metrics | kept for A only; meaningless for B/D/E | A |

**The subset rule, and why it protects the comparison:** KiCad cannot draw a swimlane and Blender should not be
asked for a bar chart. Each arm is therefore scored **only on the prompts it can express** (`kind` in
`prompts-diagrams.json`), and every table states the subset. Comparing arms on different subsets is forbidden;
comparing them on the overlap (`schematic`, `blueprint`, `flowchart`) is required.

## Predictions, recorded before any of these runs

1. **Method B will beat method A on every diagram prompt** on editability and on axis alignment, and will lose on
   nothing except possibly crowded layouts where a generated SVG overlaps its own labels.
2. **Method B's failure mode will be layout, not geometry** - valid SVG, exact circles, overlapping text and
   arrows that stop short of their boxes. That is a *solvable* failure (constraint the prompt, or post-validate the
   SVG with a checker) and it is why B is the right first investment for teaching material.
3. **Blender will win animation-readiness trivially and lose setup time**, and its 2D output will need Freestyle or
   curve-export discipline to avoid a render that looks like a screenshot of a 3D object.
4. **KiCad will be the most reliable and the narrowest** - perfect for what it does, unusable outside that subset.

## Animation, when it arrives

The instrument extends the same way the LLM track extended to nine batteries: **frame-level scoring plus temporal
consistency** - OCR and structure checks on sampled frames, trace metrics per frame, and a *continuity* measure
(does an object's identity survive between frames?). That is a separate protocol revision, declared when the first
animated asset is attempted, not bolted on now.
