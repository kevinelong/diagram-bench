# Platform icons - the masters, and what a build must do with them

Six editable SVG masters of the image-generator mark, plus the platform layer files, the previews and the reference.
These are **source masters**: nothing here is a shippable binary, and nothing should be redrawn - import and export.

Vendored 2026-10-28 from `D:/Codex/House Rules 2026 v3/image-generator-platform-icons/` (and the earlier top-left
reference from the same folder). The upstream `README.txt` in this folder is the authoritative specification; this file is
the summary a build needs, and it is what to read first.

## The mark

Palette: cyan **#087FA6**, blue **#3C5EDB**, violet **#7543C6**; Windows uses a slightly lighter related ramp for contrast
on transparent backgrounds. No fonts, no embedded bitmaps, no external resources, no SVG masks, no filters, no shadows -
all geometry stays editable.

## Which file, for which context

| context | file | notes |
|---|---|---|
| iOS app icon, colour | `ios-gradient.svg` | 1024 × 1024, opaque square, **unmasked** - the system applies the corner mask |
| iOS, monochrome/dark contexts | `ios-black-white.svg` | same canvas |
| Android adaptive, colour | `android-gradient.svg` | 108 × 108 canvas, mark 54 × 48 inside the central **66 × 66 safe area** |
| Android adaptive, monochrome | `android-black-white.svg` | same geometry |
| Android layers | `android-layers/` | colour: `foreground-white.svg` + `background-gradient.svg`; mono: `foreground-black.svg` + `background-white.svg`; plus `monochrome.svg` |
| Windows app icon | `windows-gradient.svg`, `windows-black-white.svg` | transparent **48 × 48** design grid; black/white is pure black with transparent negative space |
| reference | `reference.svg` | faithful transparent vector of the supplied image; **not** a platform asset |
| older reconstruction | `top-left-grid-reference-earlier.svg` | kept for comparison only - superseded by `reference.svg` |
| human preview | `platform-comparison.png`, `previews/*.png` | for judging at size; never ship these |

## What shipping actually requires (the part that gets forgotten)

- **iOS**: compose in Xcode (an asset catalog, or Icon Composer). The SVG is not a `.icon` file.
- **Android**: convert the layers to **VectorDrawable** resources and reference them from an `adaptive-icon` XML. The
  safe area above is why the mark is not centred on the full canvas.
- **Windows**: export **PNG assets or a multi-size ICO**. At native **16 and 24 px** the thin sun rays and fine details
  need simplifying by hand - exporting the 48 px master straight down to 16 px is what produces a smudge.
- Choose black/white over gradient wherever the surface is monochrome or the host tints the icon itself (tray glyphs,
  notification icons, taskbar glyphs, print).

## Used today

`previews/windows-gradient.png` (1024 px) is set as the Tk window icon, with `iconphoto(default=True)` so dialogs inherit
it. That is the only place in this project that consumes an icon so far; the mobile and store assets are unshipped.

Guidance checked upstream on 2026-10-28: Apple HIG app icons, Android adaptive icon design, Microsoft app icon design and
construction (links in the upstream `README.txt`).
