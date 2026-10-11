# Translating SOCKS

SOCKS ships GUI translations for Dutch (`nl`), Czech (`cs`), German (`de`),
Norwegian Bokmål (`nb`) and Swedish (`sv`). Log lines stay English.

> **Status:** the first versions of these catalogues were machine-drafted.
> They have not yet been reviewed by native speakers. Corrections are very
> welcome, from a single wrong word to a full review.

## Files

| File | What it is |
|---|---|
| `socks/gui/i18n/socks_XX.ts` | Qt Linguist source catalogue (XML). Edit this one. |
| `socks/gui/i18n/socks_XX.qm` | Compiled catalogue loaded at runtime. Generated, but committed. |

Contexts are class names (`MainWindow`, `SettingsTab`, …), so the same English
word can have different translations in different places.

## Updating the strings after code changes

Every GUI string goes through `self.tr()` / `QCoreApplication.translate()`
(module-level tables use `QT_TRANSLATE_NOOP`). After adding or changing strings,
re-extract them into all catalogues. Existing translations are kept; new
strings appear as unfinished:

```bash
.venv/bin/pyside6-lupdate $(git ls-files 'socks/gui/*.py') \
    -ts socks/gui/i18n/socks_{nl,cs,de,nb,sv}.ts
```

Then compile:

```bash
for l in nl cs de nb sv; do
    .venv/bin/pyside6-lrelease socks/gui/i18n/socks_$l.ts -qm socks/gui/i18n/socks_$l.qm
done
```

## Translating

Open a catalogue in Qt Linguist (`pyside6-linguist socks/gui/i18n/socks_nl.ts`),
or edit the XML directly: fill the `<translation>` element and drop
`type="unfinished"`.

Rules:

- Keep every placeholder exactly: `{0}`, `{1}`, `{0:.1f}`, `{0:%H:%M}`, `%n`.
  Their order in the sentence may change.
- `&` marks the keyboard accelerator (Alt+letter). Keep one `&` before a
  sensible letter, and avoid two controls in the same dialog sharing a letter.
  `&&` is a literal ampersand.
- Keep units and symbols (km, kWh, °C, %, $) and product names untranslated:
  SOCKS, Tesla, Bluetooth, Fleet API, OpenStreetMap, VIN (German uses FIN).
- Stay about as short as the English. Labels sit in tight layouts.
- Use the usual desktop register: Dutch `je`, German neutral infinitive or
  imperative style (as in GNOME/KDE), Czech neutral plural, Scandinavian `du`.

To check a change in the running app, set `language = nl` (etc.) in `gui.ini`
and restart SOCKS.

## Contributing

1. Fork <https://github.com/stepspower/socks> and create a branch.
2. Edit the `.ts` file, compile the `.qm` as above, and commit both.
3. Run `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q` before opening the PR.
4. Open a pull request. Say which language and whether you are a native
   speaker. Partial reviews are fine: list the contexts you checked.

To propose a new language, open an issue first so the language code can be
added to the picker in `socks/gui/i18n/__init__.py`.
