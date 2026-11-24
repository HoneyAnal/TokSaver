import json

import settings


def test_defaults_when_missing(tmp_app):
    d = settings.load()
    assert d["theme"] == "dark" and d["byedpi_port"] == 1080 and d["ytdlp_version"] == "latest"


def test_save_atomic_and_roundtrip(tmp_app):
    assert settings.save({"theme": "light", "custom": 1})
    d = settings.load()
    assert d["theme"] == "light" and d["custom"] == 1 and d["byedpi_port"] == 1080
    assert not (tmp_app / "data" / "settings.json.tmp").exists()


def test_corrupt_file_falls_back(tmp_app):
    (tmp_app / "data").mkdir()
    (tmp_app / "data" / "settings.json").write_text("{не json", encoding="utf-8")
    assert settings.load()["theme"] == "dark"


def test_port_validation():
    assert settings.get_port({"byedpi_port": 9050}) == 9050
    for bad in (0, 80, 70000, "abc", None, -5):
        assert settings.get_port({"byedpi_port": bad}) == 1080


def test_ytdlp_version_validation():
    assert settings.get_ytdlp_version({"ytdlp_version": "2026.11.01"}) == "2026.11.01"
    assert settings.get_ytdlp_version({"ytdlp_version": "latest"}) == "latest"
    for bad in ("../../evil", "2026", "latest; rm", ""):
        assert settings.get_ytdlp_version({"ytdlp_version": bad}) == "latest"
