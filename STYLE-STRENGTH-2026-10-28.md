# Drawing styles, and the strength a base image should get - measured

**2026-10-28.** Three drawing media were added to `STYLES` (`charcoal`, `pencil`, `blue pencil`), and every style now
proposes a strength for a base image (`STYLE_STRENGTH`), corrected per model (`MODEL_STRENGTH_OFFSET`). Everything below
was measured on one photograph (a seated figure in a drawing room) with one sentence of subject ("a seated woman in a pale
dress in a drawing room") plus the style's own suffix; only the strength and the model moved.

*Use: `python tools/style_strength_trial.py --base <figure.png>`.*

## A style suffix is a medium, not a subject

The suffix carries the medium only. The mannequin/block/construction words stay in the prompt or in the starters, because
a medium must not silently re-subject a picture: choosing "charcoal" and getting mannequins would be a surprise, and it
would make the style list a second, hidden prompt list.

## Where a medium change actually happens

| style | strength | reads as the medium? |
|---|---|---|
| charcoal (klein) | 0.55 | **no** - a photograph |
| charcoal (klein) | 0.65 | **no** - a photograph |
| charcoal (klein) | **0.72** | **yes** - coarse strokes, textured paper, and the figure, chair and room survive |
| blue pencil (klein) | 0.55, 0.65 | **no** |
| blue pencil (klein) | **0.72** | **yes** - light blue construction lines drawn over muted colour washes |
| watercolour (klein) | 0.65 | **no** - "a realistic photograph, not soft watercolour" |
| charcoal (turbo) | 0.65, 0.72 | **no** - "an oil painting" |
| charcoal (turbo) | **0.78** | **yes** - charcoal, but looser: hair, collar and dress have drifted |

So **0.72 is the strength at which a medium change happens** on klein, and turbo needs **0.78** for the same medium -
a measured **+0.06** offset. That is the second measurement of the same asymmetry: in the two-pass trial klein added
construction lines at 0.55 while turbo ignored them at 0.55 and only acted at 0.65, where it preferred anatomy to
geometry.

## A suffix cannot change genre

`diagram` at **0.78** produced *"an illustrated portrait scene with labels"* - not a technical diagram. The genre has to be
carried by the prompt ("thin black lines, white background, black line only", the words already in the `construction`
starter); the style only sets the look. The genre styles therefore sit at 0.78, the highest strength that keeps the subject
before 0.84 has left the source.

## The defaults as shipped

| style | strength on a base image | why |
|---|---|---|
| none | 0.70 | the measured pose threshold for flux2 klein |
| photo, cinematic, watercolour, charcoal, pencil, blue pencil | 0.72 | measured: below it the render is still called a photograph |
| icon, diagram, blueprint | 0.78 | a genre change, kept below the strength that leaves the source |
| **+0.06 on z-image turbo** | | measured: turbo reads charcoal as an oil painting at 0.72 and as charcoal at 0.78 |
| +0.06 on ALL | | a sweep can only run one number, so it takes the larger and the klein half runs slightly strong |

**A style never overwrites a strength the user set.** The proposal only applies while the slider still holds the value we
put there (`_strength_touched`), the same rule the style-conflict dialog follows. Switching model re-proposes the number,
because the offset belongs to the model; a number the user moved by hand survives the switch.

That rule had a hole, found by probing rather than by reading: `vary('base', 0.40)` set the strength *without* marking it as
chosen, so a style change turned 0.40 into 0.78 and a model switch into 0.84. Picking a variation strength is a decision
about that picture, so it now counts as one (`_strength_touched`), and a test pins both the style change and the model
switch against it.

## Checks

- `test_gallery_ui.py`: the three styles exist and carry a medium; no suffix exceeds 9 words (9 is the pre-existing worst
  case, `diagram`, left alone because it measured well); the medium styles sit at 0.72 and the genre styles between 0.72
  and 0.84; a charcoal prompt conflicts with the photo style and agrees with the charcoal style; no base image means no
  proposal; a base image takes the style's number plus the model's offset; moving the slider freezes it; switching model
  re-proposes it once and never after a manual move. Suite exit 0.

## Also verified this session: the 16-up still works

Both paths, through the real window and the real engine, 16 tiles at 384 px:

| path | wall time | what landed |
|---|---|---|
| 16-up, "one image at a time" (the default tick) | 183.3 s | 16 tiles, seeds 1234-1249, plus `_sheet_16up.png` |
| 16-up, one batch | **17.1 s** | 16 tiles on seed 1234, plus a 1554x1554 contact sheet (4 x 4 + 2 px gutters) |

The extra PNG after the 16 tiles is the assembled sheet, not a stray render.
