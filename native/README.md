# native/ — the experiment: the gallery out of real Windows controls

Not a port, and not shippable. This exists to answer one question with a running program instead of an argument: *what
does it cost to build this gallery from the controls Windows provides, instead of a toolkit's imitation of them?*

    cargo build --release
    target\release\zimage-native.exe C:\Users\kevin\Pictures\zimage-ui

## What it is

| piece | the real control |
|---|---|
| folder column | `SysListView32` in `LVS_LIST`, themed with `SetWindowTheme(Explorer)` |
| pictures | `SysListView32` in `LVS_REPORT` + **`LVS_OWNERDATA`** — a virtual list: the control asks for the rows it is about to paint and never holds 1 000 items itself (`LVN_GETDISPINFOW`) |
| prompt | `EDIT` |
| default action | `BUTTON` with **`BS_DEFPUSHBUTTON`** — Windows draws the default ring and Enter activates it |
| status | `STATIC` |

Data comes from the same sidecars the Python app reads (`prompt`, `lineage_root`), so the families on the left are the
same families — roughly; the naming rule here is a truncated root, not `group_prompts.label_for`.

## What came free (and is the whole point)

Because the controls are real and the message loop calls **`IsDialogMessageW`**, none of this is written anywhere in
`src/main.rs`:

- Tab / Shift+Tab between the controls, in visual order
- **Enter** activating the default button, **Space** activating the focused one
- **Alt mnemonics** (`&Generate` shows the underlined G and answers Alt+G)
- arrow keys, Home/End/PageUp/PageDown, wheel, multi-select, type-ahead in the list view
- genuine focus rectangles drawn by the system — not a ring we paint
- Common Controls v6 look (manifest carried by the linker) and per-monitor DPI v2
- the title bar, resizing, maximise and Snap behaviour

In the Tk app each of those was either a defect we found one at a time or code we wrote: a focus ring, `takefocus` on every
tile, Enter/Space handlers, F6, Alt keys, and a `_closing` guard so a debounce could not fire into a dead interpreter.

## Measured

| | value |
|---|---|
| release binary | **270 KB** (no runtime installed, no Python, no Tk) |
| working set at startup, 1 000 pictures scanned | **19.2 MB** |
| grouping scan (read 1 000 sidecars, build families) | included in that startup; the list is virtual, so scrolling is O(visible) |
| first compile to a running window | **6 build/fix rounds** (`windows` 0.62 types: `PWSTR` vs `PCWSTR`, `LIST_VIEW_ITEM_STATE_FLAGS`, `MESSAGEBOX_RESULT`; my errors, not the API's) |

## Thumbnails (added, wiring verified, painting unconfirmed)

WIC decode on a worker thread (`IWICImagingFactory` → `IWICBitmapScaler` → 32bppBGR → DIB `HBITMAP`), a process-wide cache
keyed by path, and `NM_CUSTOMDRAW` painting each row: tile on the left, file name and prompt beside it, selection colours
from the system. Rows need an image list to be tall enough in report mode (`ImageList_Create(1, 90, ILC_COLOR32, …)` +
`LVM_SETIMAGELIST(LVSIL_SMALL)`), because report rows size themselves from that list.

Three defects were found and fixed in this step, all mine:

- `CDDS_ITEMPREPAINT` is `CDDS_ITEM | CDDS_PREPAINT` = **0x10001**, not `0x10000` — with the wrong constant no row ever asked
  to be painted;
- `/MANIFESTDEPENDENCY:"type='win32' name='…'"` is one argument containing spaces, and the linker word-splits it
  (`LNK1181: cannot open input file "name='Microsoft.Windows.Common-Controls'"`); the manifest is a file passed with
  `/MANIFEST:EMBED` + `/MANIFESTINPUT:<path>` instead;
- rows in `LVS_REPORT` are one line tall until an image list sets their height, so the tile had nowhere to go.

**Thumbnails land through the image list, not custom draw.** A probe that logged every `WM_NOTIFY` settled it: the picture
list sent `NM_CUSTOMDRAW` only a handful of times, for the window itself, and never per row - an owner-data list draws its own
rows. The picture therefore arrives where the control expects it: `ImageList_Add` into the `LVSIL_SMALL` list and
`LVIF_IMAGE` in `LVN_GETDISPINFOW`. Confirmed visually from a `PrintWindow` render (which needs no screen grab): real
thumbnails, tall rows, tooltips of text beside each one.

**Theme**: the OS reports dark (`AppsUseLightTheme = 0`, both values), and `zimage-native.exe --theme` prints `dark = true`.
`DarkMode_Explorer` alone does **not** recolour a list view, so the lists get their colours explicitly
(`LVM_SETBKCOLOR`/`LVM_SETTEXTCOLOR`/`LVM_SETTEXTBKCOLOR`), the window class background becomes `BLACK_BRUSH`, and `EDIT`/
`STATIC` are coloured through `WM_CTLCOLOR*`. The title bar uses `DWMWA_USE_IMMERSIVE_DARK_MODE`. Still light: the list
*header* and the thin control borders - the header needs its own theme call.

**Status bar** was clipped at 16 px; it now owns a 22 px line under the controls (`SS_NOPREFIX`, so a prompt containing `&`
is not eaten as a mnemonic).

## Not done yet (deliberately)

- the A/B overlay and finish screen, generation against ComfyUI, Describe-all
- the grouping/naming rules of `group_prompts` (families are keyed by a raw root prefix here)
- the A/B review overlay, the finish screen, generation against ComfyUI, Describe-all, search/filter
- the grouping/naming rules of `group_prompts` (families are keyed by a raw root prefix here)

## The Zig half, and why it is not built

`zig/main.zig` is the same milestone written against `@cImport("windows.h")`. It does **not** compile, and the reasons are
about the toolchain rather than typos:

1. **Zig 0.17.0 (current, 2026-10-01) removed `@cImport` entirely** — `error: invalid builtin function: '@cImport'`. The
   whole C-import route, which is how you reach Win32 from Zig, no longer exists in the current release.
2. The last release that still has it, **0.14.1**, cannot cleanly translate Microsoft's own headers through Zig's bundled
   MinGW headers: `unable to translate macro: undefined identifier 'A'` (`__MINGW_NAME_AW`, i.e. every `...A/W` macro) and
   `overflow of integer type 'c_uint' with value '-100'` (`LVN_FIRST`). The generated bindings fail to compile before any of
   our code runs.
3. Further churn in the same attempt: `SendMessageW` takes `LPARAM = c_longlong` (pointer arguments need `@bitCast`), and
   `std.process.args()` now requires `initWithAllocator` on Windows.

So the honest comparison is not "Rust took 6 rounds, Zig would too": the Rust path had complete, current, generated bindings
for every API used (`windows` 0.62, from the same `win32metadata` Microsoft publishes), while the Zig path needs either a
pinned 0.14 toolchain *and* `zigwin32` (generated bindings, which lag the metadata) or hand-written `extern` declarations for
every function and struct. That is work the Rust version did not do, and it is the reason the recommendation stays Rust.
