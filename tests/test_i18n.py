"""Каталоги переводов: все tr("...") из кода есть в каждом языке, подстановки совпадают."""
import ast
import glob
import json
import os
import re

import pytest

import i18n

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = re.compile(r"\{\d*[^}]*\}|%[rsd]")


def _keys():
    keys = set()
    for f in glob.glob(os.path.join(APP, "*.py")):
        tree = ast.parse(open(f, encoding="utf-8").read())
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr"
                    and n.args and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                keys.add(n.args[0].value)
    return keys


def _langs():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(APP, "lang", "*.json")))


def test_languages_exist():
    assert {"ru", "uk"} <= set(_langs())


@pytest.mark.parametrize("code", _langs())
def test_catalog_complete_and_placeholders(code):
    data = json.load(open(os.path.join(APP, "lang", code + ".json"), encoding="utf-8"))
    cat = data["strings"]
    assert data["_name"]
    missing = sorted(k for k in _keys() if k not in cat)
    assert not missing, f"{code}: нет перевода для {len(missing)} строк, например {missing[:3]}"
    for k, v in cat.items():
        assert v.strip(), f"{code}: пустой перевод для {k!r}"
        assert sorted(PH.findall(k)) == sorted(PH.findall(v)), f"{code}: подстановки {k!r} → {v!r}"


def test_tr_fallback_and_format(monkeypatch):
    monkeypatch.setattr(i18n, "_cat", {"Hello {0}": "Привет {0}", "Bad {0}": "Плохо {1}"})
    monkeypatch.setattr(i18n, "_cur", "ru")
    assert i18n.tr("Hello {0}", "мир") == "Привет мир"
    assert i18n.tr("Unknown {0}", 5) == "Unknown 5"
    assert i18n.tr("Bad {0}", "x") == "Bad x"          # сломанный перевод не роняет программу


STATUS_PARTS = [  # статус карточки красится по подстроке: перевод фрагмента обязан входить в перевод статуса
    ("Error", "Error — see the log"),
    ("failed", "@{0}: sync failed — see the log"),
    ("Downloading", "Downloading 0/{0}…"),
    ("Downloading", "Downloading {0}/{1}…"),
    ("Downloading", "Downloading…"),
    ("Building", "Building the post list…"),
    ("Building", "Building the full post list…"),
    ("did not respond", "TikTok did not respond"),
]


@pytest.mark.parametrize("code", _langs())
def test_status_fragments_are_substrings(code):
    cat = json.load(open(os.path.join(APP, "lang", code + ".json"), encoding="utf-8"))["strings"]
    for part, full in STATUS_PARTS:
        assert cat[part].lower() in cat[full].lower(), f"{code}: {part!r} не входит в {full!r}"
