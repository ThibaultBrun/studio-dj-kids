"""Petit système multilingue partagé (FR/EN/ES/DE/PT).

Langue choisie dans l'ordre : variable STUDIO_LANG > locale système > français.
Usage :   from i18n import t
          t("welcome")                 -> texte dans la langue courante
          t("found", n=3)              -> avec variables ({n}, {query}, …)
Le français sert toujours de secours si une clé manque.
"""
import json
import locale
import os
from pathlib import Path

LANGS = ("fr", "en", "es", "de", "pt")
_DIR = Path(__file__).resolve().parent / "lang"


def _detect():
    env = os.environ.get("STUDIO_LANG", "")
    if env[:2].lower() in LANGS:
        return env[:2].lower()
    try:
        loc = locale.getlocale()[0] or ""
        if not loc:
            loc = (locale.getdefaultlocale() or ("",))[0] or ""  # noqa: DEP (fallback)
    except Exception:
        loc = ""
    code = loc[:2].lower()
    return code if code in LANGS else "fr"


LANG = _detect()


def _load(code):
    try:
        return json.loads((_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_FR = _load("fr")
_CUR = _FR if LANG == "fr" else {**_FR, **_load(LANG)}  # la langue courante surcharge le secours FR


def t(key, **kw):
    s = _CUR.get(key, key)
    return s.format(**kw) if kw else s
