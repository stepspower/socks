# Icons

SVG sources in `socks/gui/icons/` (shipped in the wheel; owned by the GUI worker), PNG sizes in `data/icons/hicolor/`:

- `socks.svg` - app icon (red sock with white bolt). Installed as `hicolor/scalable/apps/net.stepspower.Socks.svg`.
- `socks-tray.svg` - red rounded square with a white bolt-S; the small-size glyph.
- `socks-symbolic.svg` - white-on-transparent shape only, for tray/symbolic use on dark panels.
  Installed as `hicolor/symbolic/apps/net.stepspower.Socks-symbolic.svg`.
- `hicolor/<N>x<N>/apps/net.stepspower.Socks.png` - N = 16, 24, 32, 48, 64, 128, 256, 512.
  16 and 24 are rendered from `socks-tray.svg` (the sock does not read that small); the rest
  from `socks.svg`.

The Flatpak copies the whole `data/icons/hicolor` tree to `/app/share/icons/`, so the desktop
file's `Icon=net.stepspower.Socks` resolves. Regenerate PNGs with ImageMagick:
`convert -background none -density $((N*4)) SRC.svg -resize NxN OUT.png`.
Artwork: square, no text, no Tesla marks. Candidate comparison sheet: `docs/screenshots/icon-candidates.png`.
