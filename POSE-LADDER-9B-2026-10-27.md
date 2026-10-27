# POSE-LADDER-9B — 2026-10-27

**The 9B's pose threshold is denoise 0.70 — the same as the 4B's, measured with the same judge.** No value below 0.70
seated the figure in either arm, and at 0.70 and 0.85 both models both reseated her and kept her. What the 9B buys is
not headroom on this instrument: it costs **3-5x the wall time** (38.8 s against 7.6-13.4 s on the same box, same
moment) and **8.2-8.4 GiB against 5.0-5.5 GiB** for the render, and the identity half of "works" could not be shown to
be better at all. The window's default (**flux2 klein at 0.70**) is unchanged, and is now a same-instrument result
rather than a cloud verdict standing in for one.

This note is the last inherited assumption in this repo: the 0.70 threshold had never been measured on the 9B, and had
never been measured on the local judge at all.

## 1. The instrument had to be fixed before it could answer

`pose_probe.py` hard-wired the cloud judge (`deepseek-flash`, `DEEPSEEK_API_KEY`), so a figure study left the machine
on every cell. It now has `--judge local` (`qwen3-vl:4b` through `local_vlm.ask`) and **local is the default**;
`--judge cloud` remains for comparison against the 2026-10-24 run only.

Ollama's generate API takes **one image per call**, where the cloud path sent both images in one message. So "same
character" is asked two ways, and the two are never collapsed:

| field | question | why it is shaped that way |
|---|---|---|
| `same_character` | *Does this photograph show a young woman with short red hair, wearing a long green coat?* | attribute presence on the edit alone — the calibrated question shape (presence, not counting). Deliberately lenient: a different woman with the same hair and coat passes. |
| `sitting` | *Is the person in this photograph seated on a chair?* | the target pose |
| `standing_check` | *Is the person in this photograph standing upright on both feet?* | the same fact asked inverted; a contradiction is recorded as a contradiction (`sitting: null`) rather than resolved |
| `identity_pair` | two-panel image, base left / edit right: *Ignore the clothes, the pose and the background. Is the face in the right panel the same person's face as in the left panel?* | the closest local analogue of the cloud question, recorded apart because it is a comparison, not a presence check |

Two calibration errors were found by running the instrument rather than by reading it:

1. **A stricter pose question was worse.** The first phrasing — "sitting on a chair with bent legs, rather than
   standing upright on both feet" — answered **no** to a figure plainly seated on a wooden chair (4B, d=0.70; one boot
   rests on the floor). Measured on six hand-labelled renders (base and d=0.40/d=0.55 standing, d=0.70/d=0.85 seated,
   a different character standing): that phrasing scored **5/6**, and `Is the person sitting down?`,
   `Is the person seated on a chair?`, `Is the person standing up on both feet?` and `Does this photograph show a
   person sitting on a chair?` each scored **6/6**. The short forms won and the inverted cross-check was added.
2. **The wrapper's token budget silently truncated the answer.** `local_vlm.yes_no` fixes `num_predict` at 220, and on
   the longer questions the thinking model spent all of it reasoning and returned an **empty `response`** — 2 of 6
   first-pass control questions came back unanswerable, and both were the *positive* ones. `pose_probe.py` now
   escalates the budget (500, then 900) and can recover an answer stranded in `thinking`, which it counts in a
   `channel` field: **all 8 sweep cells and all 9 controls answered on the `response` channel** — nothing in this note
   needed the fallback.

Run-time controls (`--controls`, on by default) are stored with the results, not asserted here. **9/9**:

| control | expected | got |
|---|---|---|
| seated? — standing base | no | no |
| seated? — different character | no | no |
| seated? — generated seated reference | **yes** | **yes** |
| standing? — standing base | yes | yes |
| standing? — seated reference | no | no |
| attributes present — base | yes | yes |
| attributes present — different character | no | no |
| same face — base vs base | **yes** | **yes** |
| same face — base vs different character | no | no |

The judge is a 4B model. It cannot read text (`VAILDATE` came back as `VAIDDATE` on 2026-10-26), which is why nothing
here asks it to.

## 2. Method

* **Base image**: the standing figure from the 2026-10-24 run, reused **byte-identical**
  (`20261024-100904_512x512_seed4242.png`, `sha256_16 50e1a4524e013094`; z-image turbo, 512x512, seed 4242). Reusing it
  is what makes the 4B row comparable to the historic cloud row and the 9B row comparable to the 4B: the only variable
  that changed is the judge. The 4B pixel deltas reproduce the 2026-10-24 run **exactly** (7.46 / 9.43 / 32.63 /
  45.54), which is the check that the pipeline is otherwise unchanged.
* **Edit**: img2img from that base, 512x512, 4 steps, cfg 1.0, seed 4242, one instruction:
  *"the same young woman with short red hair and freckles, wearing the same green wool coat and black boots, now
  sitting on a wooden chair, legs bent, same face, plain white background"*.
* **Models**: `flux2 klein` (4B, Q4_K_M unet + fp4 qwen_3_4b encoder) and `flux2 klein 9b uncensored` (9B Q4_K_M unet
  + 9B uncensored Q5_K_M encoder), denoise 0.40 / 0.55 / 0.70 / 0.85.
* **VRAM order**: `local_vlm.unload()` before every generation, judge reloaded by Ollama on the first question.
  Peak is sampled from `nvidia-smi memory.used` every 250 ms and is **device-wide**, so it includes ~1-2 GiB of
  desktop.
* **Reproduce**: `python pose_probe.py --profiles "flux2 klein" "flux2 klein 9b uncensored" --denoise 0.4 0.55 0.7
  0.85 --size 512 --seed 4242 --out pose-ladder-9b --keep-server`

## 3. The ladder (`pose-ladder-9b/result.json`)

`S` = same character, `P` = seated, `SP` = both. Peak VRAM in MiB, generation / judging, device-wide.

| model | denoise | same character | seated | works | s | pixel delta | peak MiB (gen / judge) |
|---|---|---|---|---|---|---|---|
| flux2 klein (4B) | 0.40 | yes | no | no | 12.2 | 7.46 | 5,508 / 11,890 |
| flux2 klein (4B) | 0.55 | yes | no | no | 12.8 | 9.43 | 5,351 / 11,825 |
| flux2 klein (4B) | **0.70** | yes | **yes** | **yes** | 13.4 | 32.63 | 5,482 / 11,760 |
| flux2 klein (4B) | **0.85** | yes | **yes** | **yes** | 7.6 | 45.54 | 5,033 / 9,652 |
| flux2 klein 9B uncensored | 0.40 | yes | no | no | 22.8 | 5.69 | 8,247 / 9,881 |
| flux2 klein 9B uncensored | 0.55 | no † | no | no | 36.2 | 7.82 | 8,393 / 9,929 |
| flux2 klein 9B uncensored | **0.70** | yes | **yes** | **yes** | 38.8 | 33.40 | 8,240 / 9,821 |
| flux2 klein 9B uncensored | **0.85** | yes | **yes** | **yes** | 38.8 | 43.84 | 8,208 / 9,821 |

† A false negative, not an identity break: at d=0.55 the render is **7.82 away from the base** - nearly unchanged, the
same face, the same red hair, the same green coat, with a chair leg entering bottom right. The attribute question
answered `no` anyway, at temperature 0, so a repeat would not have moved it. It is the one verdict in eight that
disagrees with the picture, and it is recorded rather than re-rolled.

**Peak VRAM across the sweep: 11,890 MiB of 12,227 (97.2%).** After the final `local_vlm.unload()` the card sat at
**1,072 MiB**. The two instruments do fit together, with about 340 MiB to spare - a tighter margin than the anatomy
run's 11,725, and the 4B cells (not the 9B ones) were the peak, because Ollama loaded the judge while ComfyUI still
held the 4B unet.

Wall times are wall-clock on a box that was also running another track's work; the 9B row's 22.8 s against 38.8 s is
the same model three minutes apart, so read the ratio, not the seconds.

## 4. What the numbers say

* **The 9B threshold is 0.70**, on this instrument and this base image: **0.70 is the lowest tested value that both
  reseated the figure and kept the character**. 0.40 and 0.55 leave her standing; 0.70 and 0.85 seat her.
* **The 4B threshold is also 0.70 on the local judge**, which is the point of re-running it: the historic cloud verdict
  (**2/4 works, at 0.70 and 0.85**) reproduces on a judge that never sees another machine, with the same two working
  cells and the same four pixel deltas.
* **So on this task the threshold is a property of the denoise, not of the model.** The crossing pixel deltas agree to
  within 0.8 (32.63 for the 4B against 33.40 for the 9B) and the whole ladder is nearly the same shape
  (5.69/7.82/33.40/43.84 against 7.46/9.43/32.63/45.54): below the threshold the prompt's *object* arrives - a chair
  appears behind a figure still on her feet - and above it the pose follows the instruction.
* **No identity advantage could be measured.** The face-only two-panel question (`pose-ladder-9b/face-check.json`,
  controls 2/2: same image `yes`, different person `no`) answered **"same face" for all four** 0.70/0.85 renders of both
  models. By eye the 9B's 0.85 render keeps the base's face more closely than the 4B's does, but that is an impression
  from the contact sheet, not a result: the judge did not separate them, so the claim is not made.
* **What the 9B costs** is real and measured: 3-5x the wall time and about 3 GiB more for the render, for a threshold
  and a verdict that are the same.

## 5. What this does not measure

* **One seed per cell, one instruction, one character.** Same caveat the 2026-10-24 run carried, still true. A
  threshold measured on one seed is a bracket, not a curve.
* **512 px only.** 704 was available and not used: the base image is 512, and an img2img at 704 upscales the base
  first, which would change the thing being compared. Everything here is a 512 px statement.
* **No bracket between 0.55 and 0.70.** "0.70 is the threshold" means 0.70 is the lowest *tested* working value; the
  transition itself lives somewhere in 0.55-0.70 and was not located. If the 9B crossed at 0.62 and the 4B at 0.68,
  this sweep could not see it.
* **The identity half of "works" is the weak half.** `same_character` is attribute presence (short red hair, long green
  coat), and the two-panel face question said `yes` to renders whose faces visibly drift at 0.85. Read a `works` cell
  as "the coat and hair survived and she is seated", not as "the face is identical".
* **Judges are not adjudicated.** Where the local 4B and the cloud `deepseek-flash` disagree there is no third judge
  here to settle it; the local verdict is the one that keeps the image on the box, so it is the one reported.
* **Wall times are shared-box times** (see §3), and peak VRAM is device-wide.

## 6. The standing decision

The window's default edit strength stays **flux2 klein at denoise 0.70**. The 9B is a picker option, not a new
default: on this task, at this resolution, it changes nothing except the cost. What would change the decision is a
task where the 9B's 4-step render is the difference - text rendering, small objects, figure count - and none of those
were measured here.

Files: `pose-ladder-9b/result.json` (every verdict, timing and VRAM sample, with the controls), `pose-ladder-9b/sheet.jpg`
(base first, then 0.40 / 0.55 / 0.70 / 0.85, one row per model), `pose-ladder-9b/face-check.json` (the face-only A/B).
The cell images are generated folders and are ignored, as `pose-probe/*/` already was.


## Disclosure: one possible cloud upload during smoke testing

While smoke-testing the `--judge none` and `--judge cloud` branches of the new `--judge` flag, **`DEEPSEEK_API_KEY` was
present in the environment**, so the cloud branch actually ran for roughly 60-80 s before it was killed. It reached the
generation stage of its first cloud-judged cell (`z-image-turbo_d0.40`), so **one figure study may have been uploaded to
api.deepseek.com** before the kill. The ladder itself used the local judge for every cell; nothing else left the machine.

Two consequences, both applied: `--judge local` is now the **default** so a bare invocation cannot reach the cloud, and
`--judge cloud` is the only path that does - it is an explicit choice, documented as *"keeps uploading the picture to
DeepSeek"*. Anyone re-running this on a box where the key is set should assume the cloud path is a single typed flag
away, not a safety net.
