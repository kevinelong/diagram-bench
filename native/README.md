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

## Not done yet (deliberately)

- **thumbnails** — needs WIC (`IWICImagingFactory` → scaled `HBITMAP`) and custom draw; the rows show names and prompt text
- the A/B review overlay, the finish screen, generation against ComfyUI, Describe-all, search/filter
- the grouping/naming rules of `group_prompts` (families are keyed by a raw root prefix here)
