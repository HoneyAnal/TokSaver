"""Локализация TokSaver.

В коде тексты написаны по-английски: tr("Add and download"). Переводы лежат в
lang/<код>.json (ключ — английский текст). Нет перевода → показывается английский.
Подстановки — позиционные: tr("{0} of {1}", a, b).

Язык: настройка "language" в data/settings.json (или переменная TOKSAVER_LANG);
по умолчанию английский. Чтобы добавить язык, положите lang/<код>.json:
  {"_name": "Español", "strings": {"Add and download": "Añadir y descargar", ...}}
Смена языка применяется после перезапуска.
"""
import json
import os

import paths

LANG_DIR = os.path.join(paths.RES_DIR, "lang")
DEFAULT = "en"

_cur = None
_cat = {}
_names = {}


def _discover():
    names = {DEFAULT: "English"}
    try:
        for fn in sorted(os.listdir(LANG_DIR)):
            if fn.endswith(".json"):
                code = fn[:-5]
                try:
                    with open(os.path.join(LANG_DIR, fn), encoding="utf-8") as f:
                        names[code] = json.load(f).get("_name", code)
                except (OSError, ValueError):
                    continue
    except OSError:
        pass
    return names


def set_language(code):
    """Загружает каталог языка. Неизвестный код → английский."""
    global _cur, _cat, _names
    _names = _discover()
    if code not in _names:
        code = DEFAULT
    _cat = {}
    if code != DEFAULT:
        try:
            with open(os.path.join(LANG_DIR, code + ".json"), encoding="utf-8") as f:
                _cat = json.load(f).get("strings", {})
        except (OSError, ValueError):
            _cat = {}
    _cur = code
    return code


def _initial():
    code = os.environ.get("TOKSAVER_LANG")
    if not code:
        try:
            with open(paths.SETTINGS_FILE, encoding="utf-8") as f:
                code = json.load(f).get("language")
        except (OSError, ValueError, AttributeError):
            code = None
    return code or DEFAULT


def current():
    if _cur is None:
        set_language(_initial())
    return _cur


def tr(text, *args):
    if _cur is None:
        set_language(_initial())
    s = _cat.get(text, text)
    if args:
        try:
            return s.format(*args)
        except (IndexError, KeyError, ValueError):
            try:
                return text.format(*args)
            except (IndexError, KeyError, ValueError):
                return s
    return s


def language_names():
    current()
    return [_names[c] for c in _names]


def language_name(code):
    current()
    return _names.get(code, code)


def code_for_name(name):
    current()
    for c, n in _names.items():
        if n == name:
            return c
    return None
