# Topology checks, and the fifth instrument correction: I checked something the prompt forbids

Three properties the count and shape checks could not reach: **room closure**, **crossing direction**, and
**arrowheads**. `validate_topology.py` measures them from the SVG; the floor-plan result forced a correction to the
check itself.

## Results

| check | document | measurement | verdict |
|---|---|---|---|
| **crossing direction** (`process-1`) | s0 / s1 | 1 and 2 connectors span the lane divider, **all downward** | **pass 2/2** |
| **arrowheads** (`blueprint-1`) | s1 | **0** polygons, 0 multi-vertex paths, **0 short segments** to form a head | **fail - real** |
| **rooms closed** (`floor-1`) | s0 / s1 | 1 and 3 enclosed regions with 10 wall bands each | **criterion invalid - see below** |

## The correction: the prompt asks for the gaps I counted as defects

The floor plan renders with **door openings in every wall**, and the frozen prompt says explicitly *"one door gap in
each room wall"*. A flood-fill that counts enclosed regions therefore **contradicts the specification it is
checking**: rooms connected by their doors are correct, and the drawing scored 1 and 3 "enclosed" rooms while being
right about the thing I was measuring.

So the closure criterion is **retired with its reason recorded** in `structure-expectations.json`, and replaced by
the check that catches what is actually wrong with these drawings.

## What the replacement found

The rendered plan shows `BEDROOM`, `BEDROOM`, `BATH`, `KITCHEN`, `LIVING` - **and a second `BATH`**, six labels
where the prompt names five. Neither the count checks ("at least 4 labels") nor the source-label check ("every
required string present") can see a **duplicate**, because every required string *is* present.

`validate_labels_multiplicity.py` compares the **multiset** of text nodes against the spec:

| document | found | expected | verdict |
|---|---|---|---|
| `floor-1-s0` | BEDROOM 2, **BATH 2**, KITCHEN 1, LIVING 1 | BEDROOM 2, BATH 1, KITCHEN 1, LIVING 1 | **fail** |
| `floor-1-s1` | BEDROOM 2, BATH 1, KITCHEN 1, LIVING 1 | same | pass |

A duplicated room is a real defect in a teaching diagram, and it took a multiset comparison to see it - the third
distinct kind of check needed for one prompt.

## Method B's scorecard, after all instruments

| property | instrument | result |
|---|---|---|
| every required label present | source check + one repair round | **19/19 documents** |
| label multiplicity | multiset vs spec | **1/2** floor plans (s0 duplicates BATH) |
| structural counts where the prompt pins them | exact counts | **10/10** |
| shape (lanes, crossings, holes, plates, walls, nodal layout) | calibrated proxies | **8/9** |
| crossing direction | endpoint geometry | **2/2** |
| arrowheads on dimension lines | primitive detection | **0/1** - real miss |
| structure via a vision model | DeepSeek, 37 checks | **44/57 (77 %)** |
| didactic answerability | 30 frozen questions | **45/57 (79 %)** |

## The lesson, five times over

This is the fifth time today that a measurement was wrong before a model was: the rung order in the cache ladder,
`flat_pct` failing to see gradients, exact structural counts, uncalibrated shape proxies, and now a closure test
that penalised the door gaps the prompt demanded. **Each one was caught by looking at the artifact instead of
trusting the number.** That habit - not any single result - is what this track has produced so far.
