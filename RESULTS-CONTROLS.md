# Prompt-scaffolding controls: the 6/7 tie was not an equal comparison

The question that forced this: were the two methods given the same prompt? **The task text yes, the scaffolding
no.** Method B got a specification; method E got a specification **plus a verified Python template** (`seg`,
`circ`, `rect`, `text` helpers, a `SaveBoard` call, a hint that shapes can be rotated) **and a retry loop that fed
errors back**. That is not a like-for-like test of the methods, so both were re-run with the scaffolding moved.

## The controls

| configuration | scaffolding | result |
|---|---|---|
| **method B, bare spec** (the original run) | element whitelist only | **6 / 7** |
| **method B + worked SVG example** | minimal but correct `<svg>` skeleton with an **angled** `<line>` | **6 / 7** - still a horizontal cue |
| **method E, verified template** (the original run) | full helper library + retry loop | **6 / 7** |
| **method E, bare spec** | prose only, no example | **0 / 7** - `AttributeError: 'NoneType' object has no attribute 'Add'` on **both** attempts, no artifact at all |

## What that changes

1. **The tie was not equality.** E reached 6/7 only when handed a working template; remove it and it produces
   **nothing**. B reached the same 6/7 from a spec alone, and the example **did not improve it at all**.
   So on "what can the model do unaided", **B is clearly the more robust route**, which is the opposite of the
   impression the two renders give.
2. **The example is a gate for E, not a booster for B.** For E the difference is **0 -> 6 of 7**; for B it is
   **6 -> 6**. A method whose validity depends on a template is a method whose production cost includes building
   and maintaining that template.
3. **B's remaining failure is comprehension, not scaffolding.** My worked example contained an **angled** line
   (`200,300 -> 360,260`) and B still drew the cue horizontally. It did adopt one stylistic cue from the example -
   it switched from filled to unfilled circles - while ignoring the geometric one. That is the "plausible but
   lazy" failure mode, and it is the same family as the local model writing `VAILDATE`: it reads the request, then
   does the easy version of it.
4. **E's angle was never a comprehension win either** - it was arithmetic. The model wrote `15*cos(30°)` because
   the template's `seg()` takes coordinates, so the angle had to be computed. **The medium forced the precision.**

## Caveat on this run

Both controls were launched **concurrently** by me, against a single-slot Ollama server, so their wall times
(59.3 s and 82.9 s) are **contaminated** and are not used anywhere. Only content and validity are read from them.
That is the one-workload rule broken in my own harness, recorded rather than quietly ignored.

## Standing conclusion

**Method B is the more robust generator; method E is the more exact one when scaffolded.** Neither wins outright,
and the honest recommendation for production is the combination this test pointed at: **generate in the medium
that forces the arithmetic** (code with explicit coordinates, KiCad or SVG), then **verify containment and angles
programmatically** - because each method's remaining failure is precisely the check the other one would pass.
