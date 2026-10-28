IMAGE GENERATOR PLATFORM ICONS

Six editable SVG masters based on the supplied 1024-pixel reference image.
Palette: cyan #087FA6, blue #3C5EDB, violet #7543C6. Windows uses a slightly lighter related ramp for contrast on transparent backgrounds.
No fonts, embedded bitmaps, external resources, SVG masks, filters, or shadows. All geometry remains editable.

iOS
- ios-black-white.svg / ios-gradient.svg
- 1024 x 1024 opaque, square, unmasked artwork.
- Centered mark; system applies outer corner masking.
- These are source SVGs, not an Icon Composer .icon or an Xcode asset catalog. Import/compose or export using Xcode for shipping.

ANDROID
- android-black-white.svg / android-gradient.svg
- 108 x 108 canvas; unmasked and no exterior shadows.
- Main symbol is 54 x 48 inside the central 66 x 66 safe area. Its proportions are slightly taller to preserve safe placement.
- android-layers contains separate white/black foregrounds, opaque gradient/white backgrounds, and a transparent monochrome layer.
- Gradient pair: foreground-white.svg + background-gradient.svg.
- Black/white pair: foreground-black.svg + background-white.svg.
- SVG masters must be imported/converted to VectorDrawable resources and referenced by adaptive-icon XML before shipping.

WINDOWS
- windows-black-white.svg / windows-gradient.svg
- Transparent 48 x 48 design grid. More compact frame corners; gradient is continuous across the mark.
- Black/white uses pure black artwork with transparent negative space. For a white-backed view, see previews.
- Shipping app icons require PNG assets or a multi-size ICO. These SVG files are editable source masters.
- The thin sun rays and other details should be simplified by hand for very small native 16/24-pixel exports.

PREVIEWS
- previews contains one 1024px PNG rendering of each master.
- platform-comparison.png shows iOS and Android with illustrative system masks; the source masters remain square and unmasked. Android preview shows the 72px mask viewport within the 108px source.
- Windows is shown against white, and all six are also shown at 64px.

REFERENCE
- reference.svg is the faithful, transparent vector reconstruction of the latest supplied image.
- The earlier top-left grid reconstruction is saved separately in the workspace as image-generator-pipeline-top-left.svg.

OFFICIAL GUIDANCE CHECKED 2026-10-28
Apple: https://developer.apple.com/design/human-interface-guidelines/app-icons
Android: https://developer.android.com/develop/ui/compose/system/icon_design_adaptive
Microsoft design: https://learn.microsoft.com/en-us/windows/apps/design/iconography/app-icon-design
Microsoft construction: https://learn.microsoft.com/en-us/windows/apps/design/iconography/app-icon-construction
