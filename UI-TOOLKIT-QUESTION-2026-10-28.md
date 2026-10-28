# Is Tk the reason the UI misbehaves? Notes, 2026-10-28, parked

Question asked: are many of the UI/UX glitches the Tk toolkit's fault rather than ours, would a native Windows or a
browser-based UI behave more like the defaults users expect (focus, arrows), and should a native Windows UI in Rust be
tried as an experiment?

Short answer: **about half the incident *classes*, almost all of them plumbing rather than behaviour — and Rust would
not have prevented any of the bugs we actually hit.** Rust replaces the language, not the toolkit; the thing that would
change the behaviour is the UI layer (browser or WinUI), not the language.

## The incidents, classified

Measured from this project's own record (`PIN-20261021.md`, `README.md`) plus the 2026-10-28 session.

Toolkit-shaped - each solved once with a shim, and each shim is itself code that can later grow a bug:

| incident | toolkit |
|---|---|
| the mouse wheel goes to the **focused** widget, not the one under the pointer (Windows and every browser scroll under the cursor) → global wheel router | Tk |
| `Canvas.yview_moveto` against a stale `scrollregion` clamps silently → cost two separate scroll-into-view bugs | Tk canvas |
| a readonly `TCombobox` draws its field from the *state map*, not `fieldbackground` | ttk |
| colours cannot be set on the `vista` theme → dark mode forces `clam` and a visibly different look | ttk |
| no per-monitor DPI awareness → manual `SetProcessDpiAwareness` before Tk starts | Tk |
| no dark title bar → manual `DWMWA_USE_IMMERSIVE_DARK_MODE` | Win32 gap (any toolkit) |
| images cannot be put on the clipboard → shell out to PowerShell/WinForms | Tk |
| `focus_get()` is meaningless in a hidden window → flaky tests, now stubbed | Tk |
| no taskbar progress, jump lists or thumbnail toolbars | Tk (no COM path) |

Ours - every one of these is a state or invalidation mistake that any toolkit would allow:

| incident | cause |
|---|---|
| pane tiles handed the keyboard to the strip, so arrows moved the wrong list | a binding flag, not the toolkit |
| the plan changed but the pane was not re-rendered (stale live view until a folder was re-selected) | no invalidation point |
| the recent strip was painted and then replaced (startup flash) | render order |
| two widgets gridded into the same cell → they drew over each other | layout |
| a grid column was given at least its children's requested width → the folder chips squeezed the picture pane | layout |
| the strip used a fixed three columns regardless of width | layout |
| the live view accepted recent pictures at the clustering's loose admission threshold → one picture shown in 12 folders | wrong threshold/question |

The asymmetry to keep in mind: the toolkit problems are **plumbing** (events, DPI, clipboard, shell integration) and
each is fixed once for the whole app; the our-fault problems are **state** and they recurred, because nothing in the
app owns "which list has the keyboard" or "what the pane must show after a plan changes".

## What was decided, not yet done

1. **Toolkit-neutral state refactor (1-2 days, do it whichever shell wins).** One owner of the keyboard (`strip` /
   `pane` / `viewer` / `dialog`), render-from-state instead of patching widgets, and a single place that invalidates the
   plan's dependents (strip, pane, folder bar, prediction chip). Five of the eight fixes made on 2026-10-28 are that one
   class. In Rust you would call this making illegal states unrepresentable; in Tk it is one dict with one writer.
2. **The experiment worth running: wrap the existing web client in a native shell (Tauri/WebView2), then walk both
   shells through the same scripted tasks.** It buys the browser event model for free - wheel under the pointer, a real
   `scrollIntoView`, focus that follows what was clicked, per-monitor DPI, native clipboard, accessibility - while the
   FastAPI backend and the measured pipeline stay as they are. 2-4 days for the spike. `zimage-web` already exists, is at
   feature parity for most actions and has 61 passing tests; it is not a from-scratch build.
3. **Not doing: a Rust port of the whole app as an experiment.** Weeks of work, every measured decision re-verified, the
   484-check suite invalidated, and one toolkit's quirks traded for another's (Win32 needs the same DWM/DPI/clipboard
   work; egui would be *worse* than Tk at Windows conventions, because it has no native focus or accessibility model).
4. **If a native answer is wanted specifically**, scope it as a *spike*, not a port: one window, the gallery column only,
   200 images, keyboard navigation + wheel + per-monitor DPI + dark caption + clipboard + one native file dialog, in Rust
   with `windows-rs`. 2-4 days, scored on the same incident list.

## How to pick this up

- Start with (1): it pays off under either shell and it is testable against the existing 484 checks.
- Then run (2) as the measurement: same 13-step keyboard walkthrough as `PIN-20261021.md` (2026-10-28 entry), plus wheel,
  focus, DPI, clipboard and dialogs, in both shells; count the shims each needs. The count is the answer to "is Tk the
  problem".
- Only if (2) shows the browser model is *not* enough does (4) become worth its 2-4 days.

## Also parked on 2026-10-28, unrelated to the toolkit

- **The web build's `ALL` has never rendered a real image** (engine-tested, 61 passing). One live render closes it, the
  same way the desktop's was closed: turbo 10.6 s / 7,455 MiB, klein 9B 24.5 s / 9,749 MiB at 1024 px.
- **20 pictures in the gallery carry `steps: 50`** on a model whose distilled ceiling is 4 (12x wasted compute). They
  predate the `ALL` path, which uses `max_steps`; the sidecars are dated and seeded, so the culprit run is findable.
- **Open questions from the first-column review** (`PIN-20261021.md`, 2026-10-27): whether search while a folder is open
  should be global or scoped; whether the newest 18 get a `Recent (18)` tile; whether clicking the open folder closes it;
  what `order: Wins` should do to the *folder* list; whether to cache member vectors for `predict` (0.10 s measured on a
  synthetic corpus); and whether the contents pane may replace the folder list below some width.
