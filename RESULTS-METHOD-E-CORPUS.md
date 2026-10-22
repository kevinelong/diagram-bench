# Method E across the corpus: 3/3 clean, first attempt, and one spec that is not the frozen prompt
## 2026-10-21 — qwen3-coder:30b, `cad_arm.py --prompts flow-1,circuit-1,blueprint-1 --attempts 3`

Method E is code→KiCad: the model writes a `pcbnew` script, the script is executed, the board is exported back to
SVG and scored. It is the only route in this track whose output is a *parametric source* rather than a picture.

| prompt | script clean | drawings | source score | structure from source | attempt |
|---|---|---|---|---|---|
| flow-1 | yes | 11 | 160 paths, 9,976 B | boxes 4, connectors 3, **labels 4** — `START, VALIDATE, STORE, DONE` | 1 |
| circuit-1 | yes | 15 | — | boxes 0, connectors 11, **labels 4** — `IN, R1, C1, OUT` | 1 |
| blueprint-1 | yes | 12 | 29 paths, 3,208 B | boxes 2, connectors 6, **labels 2** — `A, B` | 1 |

`result-method-e-corpus.json`. No retries were needed: every task succeeded on attempt 1, which is the strongest
single result this track has for any method.

**flow-1 is an exact hit against the frozen expectations**: 4 boxes, 3 connectors, 4 labels, and the four strings are
the four the prompt asks for. That is a *structural* pass from generated source, not from a traced bitmap.

## The finding: blueprint-1's task is not the frozen prompt

Its labels score as a pass — `label.A` and `label.B` are both `ok` — because **the Method E task for blueprint-1
declares the labels `A` and `B`**, not the frozen prompt's `BRACKET` and dimension text. The harness scores against
the task's own declaration, which is correct behaviour for the harness and **wrong for comparability**:

- `labels_present 2/2` and `label_hits {A: true, B: true}` are true statements about a drawing that is not the one
  the diffusion arms were asked for.
- So blueprint-1's row **cannot** be compared to Z-Image's 0/4 or schnell's 0/4 on labels. Same class of defect as
  the substring matcher: the instrument is measuring its own declaration, not the corpus.

This is not fixed by the run and is not fixed here — the Method E task files need their declared labels aligned to
`prompts-diagrams.json` before any cross-method table is honest. Recorded as the next action.

## Where this leaves the methods

Method E remains the most precise route (exact text, tiny source, 100 % axis) and the most fragile (needs a working
CAD stack and a correct script). Today it needed **no retries** on three tasks, which is a better reliability
reading than the earlier single-task runs suggested, but the blueprint spec mismatch means the label column is
partly unaudited.

## What this does not establish

- Nothing about the other seven corpus prompts (only the three this track's Method E tasks cover).
- Nothing about whether the KiCad-rendered output matches the frozen prompt's *intent* for blueprint-1 — only that
  the harness's own declared labels were produced.
- No speed comparison: Method E was not timed against the diffusion arms in this run.
