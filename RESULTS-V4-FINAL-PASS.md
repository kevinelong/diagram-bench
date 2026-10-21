# v4: 10/10 on the dimensions, and the cue is on the wrong side of the ball

The final pass on the pool-table prompt. v3 scored 9/10 and failed only the rack; v4 states the rack as an explicit
lattice and scores **10/10** on the ten geometric checks - and then a look at the render found something no check
was asking about.

## The progression, on each prompt's own checks

| prompt | how geometry is expressed | checks | score |
|---|---|---|---|
| v1 | prose, orientation unstated | 7 | **2/7** |
| v2 | prose, geometry stated | 7 | **6/7** |
| v3 | ACE coordinate system and constants | 10 | **9/10** |
| **v4** | **+ the rack as a lattice formula** | 10 | **10/10** |

The v4 rack is exactly the app's lattice - measured from the drawing:

```
x= 75.00  y=[25.0]                                     apex, on the foot spot
x= 76.95  y=[23.88, 26.12]
x= 78.90  y=[22.75, 25.0, 27.25]
x= 80.85  y=[21.62, 23.88, 26.12, 28.38]
x= 82.80  y=[20.5, 22.75, 25.0, 27.25, 29.5]            back row, vertical, parallel to the foot rail
```

`x = 75 + k*1.9493`, `y = 25 + (j - k/2)*2.25` - the formula did what four adjectives could not.

## What the render showed that the checks did not

The cue is drawn from **(25,25) to (48.74,12.5)**: tip on the cue ball, butt pointing at **+x - toward the rack**.
The angle is right (27.8 degrees), the tip is right, the length is right - and the shot is backwards. A stick on
that side of the ball drives it *away* from the rack, which is the opposite of a shot setup.

**Every one of the ten checks passed while the diagram was semantically inverted**, because all ten measured
geometry and none measured **direction**. Adding an eleventh check - "the butt must be on the head side, i.e. its x
must be less than the cue ball's x" - drops the score to **10/11** and names the defect.

## The two lessons this last cycle adds

1. **Express a lattice as arithmetic.** v3 stated the row spacing (1.949) and produced a rhombus, because spacing
   does not say which axis the rows advance along. v4 stated `x = 75 + k*1.9493` and produced the triangle.
2. **A geometric check cannot catch a semantic inversion.** Angle, length, tip position and containment all passed
   on a diagram whose meaning was reversed. Direction has to be asserted explicitly, and it is the kind of thing a
   human spots in one glance - which is why the render still gets looked at, not just the score.

Also worth recording: the model's first reply to the v4 prompt was a bare ``` fence and nothing else. Only when the
prompt added "output the raw SVG, starting at <svg, do not use code fences" did it produce a document. A formatting
instruction, not a geometry one, was the difference between 0 and 10.
