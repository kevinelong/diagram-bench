# Mining the app's source: v3 scores 9/10, and the rack is the last real failure

Kevin pointed at `github.com/kevinelong/ace-new-site`. Reading `frontend/index.html` replaced every adjective in my
prompt with a constant, and the improvement is the largest of the three iterations.

## What the source gave that the rendered SVG could not

| constant | value | source |
|---|---|---|
| playfield | `(0,0)` to `(100,50)`, exactly 2:1, `viewBox="-8 -8 116 66"` | `#pool-table-svg` |
| corner pockets | `(1.5,1.5) (98.5,1.5) (1.5,48.5) (98.5,48.5)`, **r = 3.5** | `POCKET_ZONES` |
| side pockets | **`(50,1)` and `(50,49)`**, **r = 2.5** | `POCKET_ZONES` |
| ball | **r = 1.125** (2.25" diameter) | `BALL_RADIUS` |
| head spot / foot spot | **`(25,25)` / `(75,25)`** | `#head-spot`, `#foot-spot` |
| rack rows | apex on the foot spot; offsets `[0]`, `[-0.5,0.5]`, `[-1,0,1]`, `[-1.5,-0.5,0.5,1.5]`, `[-2,-1,0,1,2]` × ball diameter | `generateRackPositions` |
| row spacing | `BALL_D * sin(60deg)` = **1.9493** | same |
| 8-ball / 15-ball | **centre of row 2** / **back-centre** | same |
| diamonds | long rails at **12.5, 25, 37.5, 62.5, 75, 87.5** (50 omitted, it is the side pocket), short rails 3 each at 12.5/25/37.5; 0.5 units, cream `#f5f5dc`, gold stroke | `layer-diamonds` |
| aim conventions | ghost ball dashed white `0.3,0.2`; target line red `2,0.8` | `render-scenarios.js` |

## The three iterations, on their own checks

| prompt | checks | score | character of the failure |
|---|---|---|---|
| **v1** prose, orientation unstated | 7 | **2/7** | side pockets on the short rails, apex off centre |
| **v2** prose, geometry stated | 7 | **6/7** | cue ball off the head quarter |
| **v3** ACE coordinate system + constants | 10 | **9/10** | **the rack is a rhombus, not a triangle** |

## The one failure left, and it is instructive

The v3 drawing puts the apex correctly on the foot spot at `(75,25)` and gets 15 balls in - but they are laid out on
a **diamond lattice**: `x` steps of **1.125** (one *radius*) with `y` steps of **1.949**, so consecutive rows are
offset diagonally instead of being separated by the row spacing. The rightmost five balls span **3.38 units** of
`x` where a back row should span **0** - so `back_row_parallel` fails while every other check passes.

That is the difference between a **proportion** and a **lattice**. Prose ("rows widen toward the foot rail",
"row spacing 1.95") constrains the spacing but not the axis the rows advance along, and the model picked the wrong
one. The fix for the final pass is to state the coordinates as a formula rather than as a description:

```
for row k = 0..4:
    x = 75 + k * 1.9493
    for ball j = 0..k:
        y = 25 + (j - k/2) * 2.25
```

That is eleven words of arithmetic that no adjective can replace, and it is exactly what the source encodes.

## Also earned: three more detector corrections

The v3 checker was wrong three times before it could judge:

1. **Diamonds are rotated `<rect>`s, not `<polygon>`s** in this model's output, so counting polygons found zero.
2. **The cue was inside `<g transform="rotate(30 25 25)">`** - the checker read the child's own attributes and
   reported 0 degrees. It now accumulates group rotations while walking the tree.
3. Both of those were the same mistake as earlier today: **trusting a structure the artifact did not use.**
