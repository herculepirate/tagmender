# TagMender icon — "Ripple"

A stacked **T** over **M** on a glass disc, with green ripples spreading out over red:
the fix spreading through the library.

| File | Use |
| --- | --- |
| `tagmender-icon.svg` | Master artwork, full-bleed square (iOS/macOS apply their own corner mask) |
| `tagmender-icon-dark.svg` | Dark-appearance master |
| `tagmender-icon-rounded.svg` | Rounded version for desktops and docs; also `packaging/tagmender.svg` |
| `tagmender-icon-animated.svg` | Animated (flowing ripples) rounded version for the README and web |
| `AppIcon.appiconset/` | Xcode single-size app icon: light, dark and tinted 1024 × 1024 PNGs, opaque, no alpha |
| `png/tagmender-<size>.png` | Rounded PNGs, 16 – 1024 px, transparent corners |
| `tagmender.ico` | Windows icon, 16 – 256 px |
| `tagmender.icns` | macOS icon, 16 – 1024 px |

Colours: red `#FF3B30` → orange `#FF9F0A` → yellow `#FFD60A` → green `#30D158` (Apple system colours),
on a `#080C11` disc. Home-screen icons cannot animate; the PNGs are a still frame of the animation.
