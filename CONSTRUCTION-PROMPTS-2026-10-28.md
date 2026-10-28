# Asking our models for a figure's construction forms: five phrasings, both models, measured

**2026-10-28.** Base `20261022-182713_1024x1024_seed1235.png` (a seated figure in a chair - pose, clothing and
background all present, so the test is about *wording*, not about what the source contains). Strength **0.72** for both
models (klein's documented pose threshold is 0.70, so this is the first strength at which an instruction changes what the
figure *is*), 1024 px, seed 4242, 9 steps on turbo and 4 on klein. Ten renders, 206 s including grading.

Two questions were asked of every render, because one question cannot judge two different artefacts:

- **Q1 (blocks):** *Is the figure drawn as simple 3D construction forms - boxes, cylinders, spheres - with no muscle
  detail and no clothing?*
- **Q2 (drawing):** *Does the image show a line drawing or diagram of a figure with visible construction lines, or
  geometric blocks drawn over or around it?*

## Q1 - reads as a block figure

| model | prompt shape | verdict | grader's reason |
|---|---|---|---|
| z-image turbo | wooden mannequin | **yes** | simple geometric forms, no muscles or clothing |
| z-image turbo | grey clay blocks | no | detailed ribcage and sculpted ornament |
| z-image turbo | art-school terms | no | detailed anatomy, face, hair and clothing |
| z-image turbo | wooden + negatives | **yes** | simple geometric forms, no muscles or clothing |
| z-image turbo | construction lines | no | photorealistic woman with clothing |
| flux2 klein | wooden mannequin | **yes** | simple geometric forms, no muscle detail or clothing |
| flux2 klein | grey clay blocks | **yes** | blocky boxes and cylinders, no clothing or muscle |
| flux2 klein | art-school terms | no | detailed smooth mannequin, not primitive construction forms |
| flux2 klein | wooden + negatives | **yes** | geometric forms, no muscles or clothing |
| flux2 klein | construction lines | no | detailed anatomy, facial features, hair and hands |

## Q2 - reads as a construction drawing

| model | prompt shape | verdict |
|---|---|---|
| turbo | construction lines | **yes** - "visible skeleton diagram and construction grid lines overlay" |
| klein | construction lines | **yes** - "visible construction lines and geometric blocks overlay the figure" |
| all other eight renders | | no |

## What this says

| prompt shape (escalating specificity) | blocks (Q1) | drawing (Q2) |
|---|---|---|
| *mannequinization: structural breakdown and form blocking into simple volumes* | **0 / 2** | 0 / 2 |
| *a figure reduced to simple 3D blocks: box pelvis, box ribcage, cylinder arms and legs, soft grey clay* | 1 / 2 | 0 / 2 |
| *thin construction lines over the figure: centre line, box around the ribcage, cylinders on the limbs, black line only* | 0 / 2 | **2 / 2** |
| *an articulated wooden artist's mannequin in the same pose: boxy ribcage, cylindrical limbs, sphere joints, unshaded* | **2 / 2** | 0 / 2 |
| *...the same, plus explicit "no muscle, no clothing, no shading"* | **2 / 2** | 0 / 2 |

1. **The object beats the pedagogy, on both models.** `1.0`-style art-school vocabulary - *mannequinization, structural
   breakdown, form blocking* - scored **0 of 2** on the artefact it is supposed to produce. Naming the objects (mannequin,
   boxes, cylinders, joints, unshaded) scored **2 of 2**.
2. **Pedagogy is not silent, though - it half-lands.** Turbo's render for the art-school prompt is a **plaster mannequin
   still wearing the dress**: the noun *morpheme* mapped to a mannequin object, while the second clause ("breakdown into
   simple volumes") had no effect at all. A word ending in *-ization* can fetch an object; it cannot fetch a
   decomposition, which is a relationship between parts.
3. **Soft materials are model-dependent; wooden is robust.** *Soft grey clay* gave blocks on klein and a sculpted ribcage
   on turbo (1 of 2). *Articulated wooden mannequin* gave blocks on both. Prefer the phrasing that does not depend on how a
   model interprets a material.
4. **In-prompt negatives buy nothing here.** Adding "no muscle, no clothing, no shading, no face detail" to the winning
   sentence produced the same verdict on both models. The negative field is the place for exclusions; the positive should
   spend its ~50 words on what the picture *is*.
5. **The construction-lines sentence works first time, at 0.72, on both models** - a line drawing of the figure with a
   torso box and cylinder limbs drawn over it.
6. **The question decides the winner.** Q1 scored the best construction *drawing* as a failure, because the drawing
   keeps anatomy - which is correct for a planning underdrawing. Grading "did the prompt work" needs the question that
   matches the artefact wanted, or it measures the wrong thing.

## The recipe, as measured

| artefact | prompt | strength | both models |
|---|---|---|---|
| block figure | *an articulated wooden artist's mannequin in the same pose: boxy ribcage, cylindrical limbs, sphere joints, unshaded, plain grey background* | 0.72-0.80 | yes |
| construction drawing | *thin construction lines over the figure: centre line, a box around the ribcage, cylinders on the limbs, white background, black line only* | 0.72 | yes |

Both are starters in the app (`STARTER_PROMPTS`: `mannequin`, `construction`). Reproduce the whole comparison with
`python tools/construction_trial.py --base <figure.png> --out <temp dir> --report <report.md>`.

## The second pass, measured

Lines over a mannequin, both models, at two strengths, question: *a figure built from simple 3D blocks and also drawn
construction lines or geometric guides over or around it?* (`tools/two_pass_trial.py`; 86 s for four renders.)

| model | second pass | blocks + lines | what the picture actually is |
|---|---|---|---|
| flux2 klein | **0.55** over its own mannequin | **yes** | **the clean one**: wooden blocks, same seated pose, centre line down the face, head circle, chest grid, ribcage arc, cross on the hip joint |
| flux2 klein | 0.65 over its own mannequin | yes | blocks with guides, slightly more redrawn |
| z-image turbo | 0.65 over its own mannequin | yes by the question, **weaker in fact** | turned "a box around the ribcage" into a literal **anatomical ribcage** growing from the torso, and dropped the chair and the crossed legs |
| z-image turbo | 0.55 over its own mannequin | no | the mannequin survived, no guides appeared |

So the two-pass recipe survives measurement, with one attribution: **the light second pass is the one that adds the
drawing** - 0.55 near-copy strength is what keeps the blocks and lets the guides be drawn *on* them. Turbo needs 0.65 to
draw at all, and at 0.65 it prefers anatomy to geometry and lets the pose go; its better single artefact is the one-pass
construction drawing at 0.72.

**The question again flattered turbo**: "blocks + guides" is true of both, but the klein 0.55 picture is the artist's
planned figure and the turbo 0.65 picture is a figure study that has started growing ribs. Verdict tables answer the
question asked; the images still have to be looked at.
