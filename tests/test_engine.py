import hashlib
import json
import os

import pytest

import engine


# ---------- чистые функции ----------

def test_parse_enum_line():
    assert engine.parse_enum_line("alice\t7123456789012345678\t20240513\tHello  world") == \
        ("alice", "7123456789012345678", "20240513", "Hello world")
    assert engine.parse_enum_line("NA\t7123456789012345678\tNA\tNA") == \
        (None, "7123456789012345678", None, None)
    assert engine.parse_enum_line("мусор") is None
    assert engine.parse_enum_line("a\tabc") is None


def test_dates():
    assert engine.parse_date("2024-05-13") == "20240513"
    assert engine.parse_date("13.05.2024") == "20240513"
    assert engine.parse_date("13/05/2024") is None and engine.parse_date(None) is None
    assert engine.fmt_date("20240513") == "13.05.2024" and engine.fmt_date("x") == "x"


def test_apply_date_range():
    rows = [("1", "20240101", ""), ("2", "20240601", ""), ("3", "20241201", ""), ("4", None, "")]
    keep, skip = engine.apply_date_range(rows, "20240301", "20240930")
    assert keep == ["2", "4"] and skip == ["1", "3"]       # пост без даты не выбрасываем


def test_sec_uid_and_page_detection():
    sec = "MS4wLjABAAAA" + "x" * 60
    assert engine.find_sec_uid_in_text(f'..."secUid":"{sec}"...') == sec
    assert engine.find_sec_uid_in_text("нет ничего") is None
    assert engine.looks_private('{"privateAccount": true}') and not engine.looks_private("{}")
    assert engine.looks_login_gated("Log in to TikTok") and not engine.looks_login_gated("ok")


def test_release_urls_and_args():
    assert engine.ytdlp_release_base("latest").endswith("/releases/latest/download")
    assert engine.ytdlp_release_base("2026.11.01").endswith("/download/2026.11.01")
    a = engine.byedpi_args(9050)
    assert a[:4] == ["-i", "127.0.0.1", "-p", "9050"]       # слушаем только loopback
    assert "--socks" not in a


# ---------- Engine ----------

@pytest.fixture
def eng(tmp_app):
    events = []
    e = engine.Engine(lambda kind, payload=None: events.append((kind, payload)))
    e.events = events
    return e


def logs(e):
    return [p[1] for k, p in e.events if k == "log"]


def test_proxy_settings_from_file(tmp_app):
    import settings
    settings.save({"byedpi_port": 9050, "ytdlp_version": "2026.11.01"})
    e = engine.Engine(lambda *a: None)
    assert e.proxy_addr == ("127.0.0.1", 9050) and e.proxy_url == "socks5://127.0.0.1:9050"
    assert e.ytdlp_version == "2026.11.01"
    assert e._base_args() == ["--socket-timeout", "20", "--retries", "3", "--no-warnings"]


def test_api_shape_warning_once(eng):
    assert eng._check_api_shape("user/detail", "<html>captcha</html>", "userInfo") is None
    assert eng._check_api_shape("user/detail", json.dumps({"x": 1}), "userInfo") is None
    warns = [m for m in logs(eng) if "изменил внутренний API" in m]
    assert len(warns) == 1                                   # не спамим повторами
    assert eng._check_api_shape("user/detail", json.dumps({"userInfo": {}}), "userInfo") == {"userInfo": {}}


def test_api_honest_error_is_not_api_change(eng):
    assert eng._check_api_shape("user/detail", json.dumps({"statusCode": 10221}), "userInfo") is None
    assert not [m for m in logs(eng) if "изменил внутренний API" in m]


def test_existing_media_and_file_ok(eng, tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "20240101_111.mp4").write_bytes(b"x" * 10)
    (d / "20240101_222.mp4").write_bytes(b"")               # пустой — не считается
    (d / "20240101_333.mp4.part").write_bytes(b"x" * 10)     # недокачанный — не считается
    (d / "20240101_444.webp").write_bytes(b"x" * 10)         # обложка — не считается
    assert eng._existing_media(str(d), "111").endswith("111.mp4")
    for vid in ("222", "333", "444", "999"):
        assert eng._existing_media(str(d), vid) is None
    assert eng._file_ok(str(d / "20240101_111.mp4")) and not eng._file_ok(str(d / "nope"))


# ---------- установка yt-dlp с проверкой хеша ----------

def _fake_exe():
    head = b"MZ" if engine.paths.IS_WIN else b"\x7fELF"      # то, что проверяет _is_valid_ytdlp
    return head + b"\0" * (1024 * 1024 + 10)


def test_ensure_ytdlp_installs_when_hash_ok(eng, monkeypatch):
    blob = _fake_exe()
    sha = hashlib.sha256(blob).hexdigest()
    monkeypatch.setattr(engine, "YT_DLP_ASSET", "yt-dlp.exe")
    monkeypatch.setattr(eng, "_ytdlp_expected_sha", lambda: sha)

    def fake_dl(url, dest, expected_sha256=None, max_size=None):
        assert expected_sha256 == sha
        open(dest, "wb").write(blob)
    monkeypatch.setattr(eng, "_download_file", fake_dl)
    os.makedirs(os.path.dirname(engine.YT_DLP_EXE), exist_ok=True)
    assert eng._ensure_ytdlp() is True
    assert eng._read_sidecar(engine.YT_DLP_EXE) == sha


def test_ensure_ytdlp_refuses_without_checksum(eng, monkeypatch):
    monkeypatch.setattr(engine, "YT_DLP_ASSET", "yt-dlp.exe")
    monkeypatch.setattr(eng, "_ytdlp_expected_sha", lambda: None)
    called = []
    monkeypatch.setattr(eng, "_download_file", lambda *a, **k: called.append(1))
    assert eng._ensure_ytdlp() is False and not called       # непроверенное не качаем и не запускаем


def test_ensure_ytdlp_rejects_tampered_download(eng, monkeypatch):
    import netutil
    monkeypatch.setattr(engine, "YT_DLP_ASSET", "yt-dlp.exe")
    monkeypatch.setattr(eng, "_ytdlp_expected_sha", lambda: "0" * 64)

    def bad_dl(url, dest, expected_sha256=None, max_size=None):
        raise netutil.IntegrityError("сумма не совпала")
    monkeypatch.setattr(eng, "_download_file", bad_dl)
    assert eng._ensure_ytdlp() is False
    assert not os.path.exists(engine.YT_DLP_EXE)
    assert any("НЕ установлен" in m for m in logs(eng))


def test_ensure_ytdlp_detects_modification_after_install(eng, monkeypatch):
    blob = _fake_exe()
    sha = hashlib.sha256(blob).hexdigest()
    monkeypatch.setattr(engine, "YT_DLP_ASSET", "yt-dlp.exe")
    monkeypatch.setattr(eng, "_ytdlp_expected_sha", lambda: sha)
    os.makedirs(os.path.dirname(engine.YT_DLP_EXE), exist_ok=True)
    open(engine.YT_DLP_EXE, "wb").write(blob + b"EVIL")      # файл изменили после установки
    eng._write_sidecar(engine.YT_DLP_EXE, sha)
    installs = []

    def fake_dl(url, dest, expected_sha256=None, max_size=None):
        installs.append(1)
        open(dest, "wb").write(blob)
    monkeypatch.setattr(eng, "_download_file", fake_dl)
    assert eng._ensure_ytdlp() is True and installs == [1]
    assert hashlib.sha256(open(engine.YT_DLP_EXE, "rb").read()).hexdigest() == sha


def test_ensure_ytdlp_offline_keeps_existing(eng, monkeypatch):
    blob = _fake_exe()
    monkeypatch.setattr(engine, "YT_DLP_ASSET", "yt-dlp.exe")
    monkeypatch.setattr(eng, "_ytdlp_expected_sha", lambda: None)
    os.makedirs(os.path.dirname(engine.YT_DLP_EXE), exist_ok=True)
    open(engine.YT_DLP_EXE, "wb").write(blob)
    assert eng._ensure_ytdlp() is True                       # нет сети — работаем с уже стоящим
    assert any("контрольную сумму" in m for m in logs(eng))


# ---------- ByeDPI ----------

def test_byedpi_hash_pinned_and_checked(eng):
    assert len(engine.BYEDPI_ZIP_SHA256) == 64 and len(engine.BYEDPI_EXE_SHA256) == 64
    os.makedirs(engine.paths.BIN_DIR, exist_ok=True)
    assert eng._byedpi_ok() is False                          # нет файла
    open(engine.BYEDPI_EXE, "wb").write(b"not the real one")
    assert eng._byedpi_ok() is False                          # чужой файл не принимается


def test_sync_summary_counts_files_and_failures(tmp_app):
    import threading
    import time as _t
    events = []
    e = engine.Engine(lambda kind, payload=None: events.append((kind, payload)))
    f = tmp_app / "a.mp4"
    f.write_bytes(b"x" * 2048)
    e._sync_run = {"t0": _t.time() - 65, "ok": 0, "fail": 0, "bytes": 0, "new": 3,
                   "accounts": 2, "per": {}, "lock": threading.Lock()}
    e._run_note_file("bob", 1, "7000000000000000001", str(f), "video")
    e._run_note_fail("bob")
    e._run_report()
    logs = [p[1] for k, p in events if k == "log"]
    toasts = [p for k, p in events if k == "toast"]
    assert any("2.0 KB" in m for m in logs)
    assert toasts and toasts[0][0] == "warn"          # есть ошибка → предупреждение
    assert e._sync_run is None
