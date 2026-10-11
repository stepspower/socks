# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Translations: socks_<lang>.ts (sources) and committed socks_<lang>.qm (compiled).

``install`` picks the language ("auto" = system locale) and installs SOCKS' and Qt's own
translators. A missing or empty .qm simply leaves the English source strings.
Regenerate with::

    .venv/bin/pyside6-lupdate $(find socks/gui -name '*.py') -ts socks/gui/i18n/socks_nl.ts
    .venv/bin/pyside6-lrelease socks/gui/i18n/socks_nl.ts
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

DIR = Path(__file__).resolve().parent
LANGUAGES: dict[str, str] = {
    "auto": "System language",
    "en": "English",
    "nl": "Nederlands",
    "cs": "Čeština",
    "de": "Deutsch",
    "nb": "Norsk bokmål",
    "sv": "Svenska",
}
SUPPORTED = tuple(k for k in LANGUAGES if k not in ("auto", "en"))

_installed: list[QTranslator] = []


def pick_language(setting: str | None, system_name: str | None = None) -> str:
    """ "auto"/None → the system locale's language if we ship it, else "en"."""
    if setting and setting != "auto":
        return setting if setting in LANGUAGES else "en"
    name = system_name if system_name is not None else QLocale.system().name()
    lang = name.split("_")[0].lower()
    if lang in ("no", "nn"):
        lang = "nb"  # Norwegian: we ship bokmål
    return lang if lang in SUPPORTED else "en"


def qm_path(lang: str) -> Path:
    return DIR / f"socks_{lang}.qm"


def install(app: QCoreApplication, setting: str | None) -> str:
    """Install translators for the chosen language; returns the language code used."""
    for t in _installed:
        app.removeTranslator(t)
    _installed.clear()
    lang = pick_language(setting)
    if lang == "en":
        return lang
    qt = QTranslator(app)
    if qt.load(
        QLocale(lang), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    ):
        app.installTranslator(qt)
        _installed.append(qt)
    ours = QTranslator(app)
    if qm_path(lang).is_file() and ours.load(str(qm_path(lang))):
        app.installTranslator(ours)
        _installed.append(ours)
    return lang
