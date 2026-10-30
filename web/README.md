# web/ — the gallery, in a browser

    python web/server.py --gallery C:/Users/kevin/Pictures/zimage-ui --port 8123

Then open <http://127.0.0.1:8123>. Standard library only: `http.server` serves JSON, JPEG thumbnails, the pictures and
three static files (`index.html`, `app.css`, `app.js`). PIL is used for one thing — building a missing thumbnail.

The folders and the A/B session are **not** reimplemented. The server imports `group_prompts`, `pair_review` and
`pair_rank` from the repo root (`sys.path` insert) and calls `plan()`, `build()`, `next_round()`, `record()`,
`order()`, `reset_wins()` exactly as the Tk window does. What is local to this file is the row-building around
`plan()` (the same fields `zimage_ui._compute_groups` supplies), the sidecar reader, and a thumbnail builder that
writes the same `.thumbs/<stem>.jpg` cache, so the two UIs share one cache and one review state (`~/.local/zimage-ui/review`
by default; `--state` moves it).

Options: `--gallery`, `--port`, `--host`, `--state`, `--llm` (default `http://127.0.0.1:8080/v1`), `--verbose`.

## Endpoints

| endpoint | what it does |
|---|---|
| `GET /` | the page |
| `GET /api/folders` | the plan: `{id, label, title, kind, count, series, undescribed}` per folder |
| `GET /api/folder/<id>` | the folder's pictures: `path`, `prompt`, `created`, `has_prompt`, wins/losses/ties |
| `GET /thumb?path=…` | JPEG thumbnail: `.thumbs` when present, built once with PIL otherwise |
| `GET /image?path=…` | the picture itself (A/B view, large result tiles) |
| `GET /api/review/<id>` | session state + the next pair + the order so far |
| `POST /api/decide` | `{id, a, b, choice}`, `choice` in `left_wins` / `right_wins` / `tie` → the next pair |
| `GET /api/rank/<id>` | ordered paths + each picture's wins (this session) and lifetime record |
| `POST /api/reset/<id>` | zero this folder's counters and rebuild the schedule |
| `POST /api/describe-all` | `{id}` → a pollable job captioning every picture here with no prompt |
| `GET /api/job/<job>` | job progress; `POST /api/job/<job>/cancel` stops it after the picture in hand |
| `GET /api/llm` | whether the vision model answers, and how many pictures still need a prompt |

Every endpoint refuses paths outside `--gallery`; nothing in the API deletes a file.

## Parity, item by item

Done:

| Tk behaviour | here | endpoint / page |
|---|---|---|
| prompt-similarity folder families, caption-only folders marked, unfiled bucket | `plan()` unchanged; the newest 18 become a "recent" folder so they are reachable | `GET /api/folders` → folder list |
| caption-only families labelled `caption: …` | same label rewrite as `_compute_groups` | `GET /api/folders` |
| thumbnail grid with prompt text and `nW nL` badges | lazily loaded 168 px tiles that link to the full picture | `GET /api/folder/<id>` + `GET /thumb` |
| A/B rank review: Left / Tie / Right, `1` `2` `3`, `←` `→`, Esc out | same choices, same key map, nothing deleted | `GET /api/review/<id>`, `POST /api/decide` |
| rounds: exhaustive at 6 pictures or fewer, otherwise log2(n) Swiss rounds that advance when a round is decided | `pair_review.build`/`next_round` untouched | `POST /api/decide` response `round`, `target_rounds` |
| wins written into each picture's own sidecar (and the per-opponent tally) | `pair_rank.record` | `POST /api/decide` |
| "Ranked by wins" result, Reset and start over, Keep and close | large tiles (`--done-tile: 360px` vs `--grid-tile: 168px`) drawn from the shared 336 px cache - each links to the full picture; Enter/Space keeps | `GET /api/rank/<id>`, `POST /api/reset/<id>`, finished screen |
| Describe all: prompt + caption + `prompt_from: describe` + `described_at` in the sidecar, Esc stops it | same fields, same model request, pollable progress | `POST /api/describe-all`, `GET /api/job/<id>` |
| keyboard: Tab everywhere, Enter/Space activate, Esc backs out, live regions for progress | focus ring on every control, `aria-live` status | `index.html` / `app.css` / `app.js` |
| dark and light | `prefers-color-scheme` with CSS variables | `app.css` |

Deferred, on purpose:

- **Generation, Vary, Upscale, base/multi-base editing, masks, crops** — no GPU, no ComfyUI, no model loading. The
  contract is review and organising; the picture-making half of the Tk app stays there.
- **Delete mode** (`delete_left`/`delete_right`, recycle bin). This view ranks only: `POST /api/decide` rejects the
  delete choices with a 400 so a stray request cannot remove a picture.
- **Gallery mode** (one picture at a time with Prev/Next). The A/B view and the grid cover looking at pictures.
- **Recursive scan** (`_scan(recursive=True)`). The server reads the gallery folder itself; subfolders are ignored,
  which is also the Tk default.
- **Sort/filter/search, source switching, settings.** The Tk window's browser controls are not part of review or
  organising as asked.
- **A `caption:`-prefix for caption-only groups is kept, but there is no per-picture "describe this one" button** —
  Describe all is the batch path, and a single picture can be described by putting it in a folder and running it.

## Tests

    python web/test_web.py

31 generated pictures (18 recent, 8 in one family, 3 in another, 2 with no prompt), a temporary gallery and a temporary
review-state folder, and a stdlib stub standing in for the vision model on a loopback port — so the describe path is
tested, not skipped, and nothing touches `~/.local/zimage-ui` or the real gallery. The checks walk folders → folder →
thumbnail → review → decide → rank → reset, the multi-round schedule, describe-all (including a cancel and the 503 when
the model is down), and the page's keyboard/contrast contract.
