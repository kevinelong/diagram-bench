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

## Backlog closed: combinations that had never been rendered

Ten probes in one pass (`tools/construction_media_trial.py`), one question for the medium and one for the content.

| probe | medium | content | what it actually is |
|---|---|---|---|
| **diagram style + the construction words**, klein 0.78 | **yes** | yes | **thin black outline on white** - the genre lands when the *prompt* carries it |
| the same, turbo 0.78 | no | yes | a photograph with a line-art overlay - turbo will not take the genre |
| **blue pencil + construction over the blocks**, klein 0.72 | **yes** | yes | **the artist's artefact**: blue-pencil construction lines on a block figure |
| charcoal + construction over the blocks, klein 0.72 | no | yes | reads as *graphite* - see the contradiction below |
| pencil 0.72, klein | yes | no | hatching arrives, the seated figure drifts away |
| pencil 0.78, turbo | yes | yes | confirms the +0.06 offset for a third medium |
| blue pencil 0.78, turbo | no | yes | blue pencil is a klein strength; turbo inks the dress and stops |
| icon 0.78, klein | no | yes | a suffix cannot make an icon of a photograph |
| blueprint 0.78, klein | no | yes | white overlay lines on a photograph |
| watercolour 0.72 and 0.78, klein | 1 of 3 | **no** | **the weakest style shipped**: an illustration, and the content drifts |

Four consequences, all of them things the earlier pass could not see:

1. **A genre needs its words in the prompt.** `diagram` alone failed at 0.78 ("an illustrated portrait scene with
   labels"); `diagram` **plus** the construction words produced a thin-line diagram on white. The starters already carry
   those words, which is why they work.
2. **A contradiction inside the prompt is a real failure mode.** Charcoal plus "black line only, white background" came
   back as graphite: the construction words and the charcoal suffix fight each other. The style guard checks *prompt against
   style*, not *prompt against prompt*; that second guard does not exist, so this has to be known rather than caught.
3. **For a construction study, blue pencil is the medium that works** on klein (0.72, over a block figure). Charcoal over
   blocks loses its charcoal to the "black line only" words.
4. **Watercolour is documented as unreliable**, not silently shipped as if it worked: 0.72 and 0.78 both drift the content,
   and only one of three renders read as watercolour at all.

## Failures now say what failed

`: `workflow failed`` became `workflow failed: <node type>: <exception message>`, read out of ComfyUI's history entry. Two
renders failed during this pass (`RuntimeError: workflow failed`) and neither reproduced on an immediate retry with the same
seed and parameters - transient server faults, not parameters - but the next one will say which node and why.

## The 16-up smoke, and a bug in the smoke itself

`tools/smoke_16up.py` closes the test gap between the suite (which checks the dispatch against a stub) and a real run:

| path | wall time | tiles | seeds | sheet |
|---|---|---|---|---|
| one batch | **13.6 s** | 16 | one (1234) | 1554 x 1554 |
| one at a time | 129.3 s | 16 | 1234-1249 | 1554 x 1554 |

The first run of the smoke reported 129 s *and* varying seeds for the "one batch" path. The app was right; the smoke was
wrong - `app.seq_var.set(not a.sequential)` inverted its own flag. Fixed and re-measured, which is how the 13.6 s row exists
at all. A verification tool that has not been verified is just a second source of doubt.

## Round 2: the three candidates, closed

| probe | medium | content/pose | verdict |
|---|---|---|---|
| watercolour **anchored** ("rough cold-press paper, visible brush strokes, paper showing through") 0.72 and 0.78 | **no, no** | no, no | the wording is not the problem - watercolour does not restyle a photograph at these strengths on klein |
| icon **+ genre words** 0.78 | **yes** | n/a | a flat vector illustration of the sitter - the genre lands, exactly as `diagram` did |
| blueprint **+ genre words** 0.78 | **yes** | n/a | same |
| blue pencil + construction **+ "in the same seated pose"** 0.72 | **yes** | **yes** | blocks, blue guides and **the seated pose held** |
| the same at 0.65 | no | yes | 0.65 is below the strength where a medium appears at all |

Three conclusions:

1. **Naming the pose is what keeps the pose.** Both earlier construction winners came back frontal because nothing asked for
   the seat; adding "in the same seated pose" held it at 0.72 while still laying down the blue guides. The strength that
   makes a medium land cannot also be expected to preserve composition - the prompt has to say what must not move.
2. **The genre rule generalises**: `diagram`, `icon` and `blueprint` all landed their genre once the *prompt* carried the
   genre words. The content question is the wrong measure for a genre change (an icon of a scene is not the scene) - the
   icons kept the sitter, which is what an icon is.
3. **Watercolour is not fixable by wording at 0.72-0.78.** Two different suffixes, two strengths, five renders: it stays an
   illustration. It probably works as a text-to-image look, which is untested; as a photo restyle it is the weak style and the
   docs now say so.

## A guard for a prompt that contradicts itself

`PROMPT_CLASHES` names the rival pairs, `prompt_clashes()` finds them in the text **plus the style suffix**, and the result
shows in the banner (in the warning colour) and once in the hint when Generate is pressed. It advises and never blocks - a
contradiction is a style choice, not an error. The pair that started it, measured: *"black line only, white background"* with
a charcoal suffix returned graphite.

Writing it surfaced two things worth keeping: `Theme` had no `warning` colour (added, light and dark), and `cget` returns a
Tcl object, so the test compares `str(...)` and by value - a Windows theme change rebuilds `app.theme`, while the banner
keeps the colour it was given.

## Round 3: the guard acts, and watercolour is cleared of the charge

**Watercolour as text-to-image works.** The same suffix that failed five times over a photograph produced a genuine
watercolour - soft washes, paper texture, painterly edges - from text alone at 1,024 px. So the style is sound; what fails
is *restyling a photograph*, which is a different job and is now described as such.

**The contradiction guard now offers the fix**, not just the diagnosis. `_rival_spans` finds the clashing words that are in
the prompt box (the style's own words are not the box's to drop - clearing the style is the style dialog's job), the box
highlights them, and the dialog offers *"Remove it/them and generate"* against *"Keep and generate"*. Escape keeps, because
dropping words someone typed is the aggressive action, and the question is asked **once per prompt text** so pressing
Generate again does not reopen it.

Also: a `blueprint` starter now carries its own genre words, the way the `diagram` and `icon` starters do - a genre suffix
alone stays a photograph (measured), so the words have to be in the prompt.

Checks added: the spans come only from the box, a suffix-only rival yields none, remove takes the words out of what is sent
while keeping the style, keep sends the prompt as typed, and the dialog is asked exactly once. Suite exit 0.

Two of my own mistakes were caught by the suite rather than by review: the first version of the clash test triggered the
**real** modal dialog and hung the run for ten minutes (stubbed now), and my span expectation was off by one character.
