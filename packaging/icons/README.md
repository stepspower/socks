# Icons

The single source of truth is `data/icons/socks.svg` (owned by the GUI worker). The Flatpak
installs it as `hicolor/scalable/apps/net.stepspower.Socks.svg`, which is all Flathub requires.

Optional raster exports, only if a store asks for them: 128x128 and 256x256 PNG
(`rsvg-convert -w 128 socks.svg -o socks-128.png`). Flathub shows 128x128 minimum.
Keep the artwork square, no text, readable at 16 px. The icon name everywhere is
`net.stepspower.Socks`.
