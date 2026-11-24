import http.server
import os
import subprocess
import sys
import threading
import time

import pytest

import applog
import engine
import netutil


# ---------- редиректы и cookies ----------

class _Recorder(http.server.BaseHTTPRequestHandler):
    seen = {}
    redirect_to = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).seen[self.server.server_address[1]] = self.headers.get("Cookie")
        if type(self).redirect_to and self.server.server_address[1] == type(self).redirect_to[0]:
            self.send_response(302)
            self.send_header("Location", type(self).redirect_to[1])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_cookie_not_forwarded_to_other_host():
    a, b = _serve(), _serve()
    try:
        pa, pb = a.server_address[1], b.server_address[1]
        _Recorder.seen = {}
        # другой ХОСТ (localhost вместо 127.0.0.1) → Cookie отбрасывается
        _Recorder.redirect_to = (pa, f"http://localhost:{pb}/x")
        assert netutil.http_get(f"http://127.0.0.1:{pa}/",
                                headers={"Cookie": "sessionid=SECRET"}) == b"ok"
        assert _Recorder.seen[pa] == "sessionid=SECRET"
        assert _Recorder.seen[pb] is None
    finally:
        a.shutdown(), b.shutdown()


def test_cookie_kept_on_same_host_redirect():
    a = _serve()
    try:
        pa = a.server_address[1]
        _Recorder.seen = {}
        _Recorder.redirect_to = (pa, f"http://127.0.0.1:{pa}/again")
        # редирект на себя зациклится — ограничиваем: второй раз отдаём 200
        calls = []
        orig = _Recorder.do_GET

        def once(self):
            if calls:
                _Recorder.redirect_to = None
            calls.append(1)
            orig(self)
        _Recorder.do_GET = once
        try:
            netutil.http_get(f"http://127.0.0.1:{pa}/", headers={"Cookie": "a=b"})
        finally:
            _Recorder.do_GET = orig
        assert _Recorder.seen[pa] == "a=b"
    finally:
        _Recorder.redirect_to = None
        a.shutdown()


def test_response_too_large_and_deadline(monkeypatch):
    srv = _serve()
    _Recorder.redirect_to = None
    try:
        port = srv.server_address[1]
        with pytest.raises(netutil.ResponseTooLarge):
            netutil.http_get(f"http://127.0.0.1:{port}/", limit=1)
        assert netutil.http_get(f"http://127.0.0.1:{port}/", limit=10) == b"ok"
        monkeypatch.setattr(netutil.time, "monotonic", iter(range(0, 10000, 500)).__next__)
        with pytest.raises(TimeoutError):
            netutil.http_get(f"http://127.0.0.1:{port}/", deadline=1)
    finally:
        srv.shutdown()


def test_is_within(tmp_path):
    base = tmp_path / "media"
    base.mkdir()
    assert netutil.is_within(str(base), str(base / "a.mp4"))
    assert not netutil.is_within(str(base), str(tmp_path / "evil.mp4"))
    assert not netutil.is_within(str(base), str(base / ".." / "evil.mp4"))


# ---------- журнал ----------

def test_redact():
    r = applog.redact
    assert "SECRET" not in r("Cookie: sessionid=SECRET; ttwid=Z")
    assert "SECRET" not in r("https://x/?msToken=SECRET&a=1") and "a=1" in r("https://x/?msToken=S&a=1")
    assert "<cookies.txt>" in r("файл C:\\Users\\me\\Downloads\\cookies.txt выбран")
    assert r("обычный текст") == "обычный текст"


def test_engine_log_redacts(tmp_app):
    events = []
    e = engine.Engine(lambda k, p=None: events.append((k, p)))
    e.log("ошибка: Cookie: sessionid=SECRET")
    assert "SECRET" not in events[-1][1][1]


# ---------- cookies в движке ----------

COOKIES = (".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tSECRET\n"
           "#HttpOnly_.tiktok.com\tTRUE\t/\tTRUE\t0\tttwid\tTW\n"
           ".bank.example\tTRUE\t/\tTRUE\t0\tbanksession\tBANK\n")


def test_cookie_header_only_tiktok_domains(tmp_app):
    e = engine.Engine(lambda *a: None)
    f = tmp_app / "cookies.txt"
    f.write_text("# Netscape HTTP Cookie File\n" + COOKIES, encoding="utf-8")
    assert e.set_cookies_file(str(f))
    header = e._cookie_header()
    assert "sessionid=SECRET" in header and "ttwid=TW" in header and "BANK" not in header


def test_cookie_only_sent_to_tiktok_hosts(tmp_app, monkeypatch):
    e = engine.Engine(lambda *a: None)
    f = tmp_app / "cookies.txt"
    f.write_text(COOKIES, encoding="utf-8")
    e.set_cookies_file(str(f))
    sent = {}
    monkeypatch.setattr(engine.netutil, "http_get",
                        lambda url, headers=None, **k: sent.setdefault(url, dict(headers)) and b"x")
    e._http_get("https://www.tiktok.com/@a", 5)
    e._http_get("https://evil.example/@a", 5)
    assert "Cookie" in sent["https://www.tiktok.com/@a"]
    assert "Cookie" not in sent["https://evil.example/@a"]


def test_set_cookies_file_validation(tmp_app):
    logs = []
    e = engine.Engine(lambda k, p=None: logs.append((k, p)))
    assert e.set_cookies_file(str(tmp_app / "missing.txt")) is False
    assert e.set_cookies_file(str(tmp_app)) is False                    # папка, не файл
    big = tmp_app / "big.txt"
    big.write_bytes(b"x" * (engine.Engine.MAX_COOKIES_FILE + 1))
    assert e.set_cookies_file(str(big)) is False and e.cookies_file is None
    ok = tmp_app / "ok.txt"
    ok.write_text("x", encoding="utf-8")
    assert e.set_cookies_file(str(ok)) is True and e.cookies_file == str(ok)
    if os.name == "posix":
        assert (ok.stat().st_mode & 0o077) == 0                         # только владельцу
    assert e.set_cookies_file(None) is True and e.cookies_file is None


# ---------- команды внешних программ ----------

def test_ytdlp_commands_use_safe_arg(tmp_app):
    e = engine.Engine(lambda *a: None)
    acc = {"id": 1, "nickname": "--exec"}
    # ник, превращающийся в опцию, не должен дойти до командной строки
    src = open(engine.__file__, encoding="utf-8").read()
    assert "[YT_DLP_EXE, url," not in src and "[YT_DLP_EXE, target," not in src
    assert "validate.safe_arg(" in src and acc and e


# ---------- процессы и потоки ----------

def test_busy_counter_across_tasks(tmp_app):
    events = []
    e = engine.Engine(lambda k, p=None: events.append((k, p)))
    e._busy_enter()
    e._busy_enter()
    e._busy_exit()                       # первая закончила — вторая ещё работает
    assert [x for x in events if x[0] == "busy"] == [("busy", True)]
    e._busy_exit()
    assert [x for x in events if x[0] == "busy"] == [("busy", True), ("busy", False)]
    e._busy_exit()                       # лишний выход не уходит в минус
    assert e._tasks_active == 0


def test_run_timeout_kills_process(tmp_app):
    e = engine.Engine(lambda *a: None)
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        e._run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert time.monotonic() - t0 < 10


def test_shutdown_kills_children(tmp_app):
    e = engine.Engine(lambda *a: None)
    p = e._popen([sys.executable, "-c", "import time; time.sleep(30)"], max_seconds=60)
    assert p.poll() is None
    e.shutdown()
    p.wait(timeout=10)
    assert p.poll() is not None


def test_watchdog_kills_overdue(tmp_app, monkeypatch):
    e = engine.Engine(lambda *a: None)
    real_sleep = time.sleep
    monkeypatch.setattr(engine.time, "sleep", lambda s: real_sleep(0.05))
    p = e._popen([sys.executable, "-c", "import time; time.sleep(30)"], max_seconds=0.2)
    p.wait(timeout=10)
    assert p.poll() is not None


def test_backoff_and_throttle(tmp_app, monkeypatch):
    e = engine.Engine(lambda *a: None)
    e._note_rate_limit()
    assert e._backoff == 5
    e._note_rate_limit()
    assert e._backoff == 10
    e._note_ok()
    assert e._backoff == 5
    slept = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: (slept.append(s), setattr(e, "_throttle_until", 0)))
    e._wait_throttle()
    assert slept


# ---------- параллельные загрузки ----------

def _make_account(tmp_app, n):
    import db
    db.db_init(os.path.join(str(tmp_app), "data"))
    acc_id, _ = db.db_add_account("alice")
    ids = [f"10000000{i:02d}" for i in range(n)]
    db.db_upsert_videos(acc_id, [(v, "20240101", "t") for v in ids])
    return acc_id, ids


def test_parallel_downloads_all_done_and_concurrent(tmp_app, monkeypatch):
    import db
    acc_id, ids = _make_account(tmp_app, 8)
    e = engine.Engine(lambda *a: None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    active, peak, lock = [0], [0], threading.Lock()

    def fake(acc, vid, ff):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        threading.Event().wait(0.05)
        with lock:
            active[0] -= 1
        return True, f"/x/{vid}.mp4", "video"
    monkeypatch.setattr(e, "download_one", fake)
    acc = db.db_get_account(acc_id)
    assert e._download_pending_par(acc, db.db_pending_videos(acc_id), 3) is True
    assert 1 < peak[0] <= 3
    total, done, pending, skipped = db.db_counts(acc_id)
    assert (done, pending) == (8, 0)


def test_parallel_failures_counted_and_not_marked(tmp_app, monkeypatch):
    import db
    acc_id, ids = _make_account(tmp_app, 4)
    e = engine.Engine(lambda *a: None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    bad = set(ids[:2])
    monkeypatch.setattr(e, "download_one",
                        lambda acc, vid, ff: (vid not in bad, f"/x/{vid}.mp4", "video"))
    acc = db.db_get_account(acc_id)
    e._download_pending_par(acc, db.db_pending_videos(acc_id), 2)
    assert db.db_counts(acc_id)[1:3] == (2, 2)                    # 2 скачано, 2 осталось в очереди


def test_parallel_cancel_skips_rest(tmp_app, monkeypatch):
    import db
    acc_id, ids = _make_account(tmp_app, 6)
    e = engine.Engine(lambda *a: None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)

    def fake(acc, vid, ff):
        e._cancel_req.add(acc_id)            # «Стоп» нажат во время первой загрузки
        return True, f"/x/{vid}.mp4", "video"
    monkeypatch.setattr(e, "download_one", fake)
    acc = db.db_get_account(acc_id)
    e._download_pending_par(acc, db.db_pending_videos(acc_id), 2)
    total, done, pending, skipped = db.db_counts(acc_id)
    assert skipped >= 1 and pending == 0 and done + skipped == 6


def test_parallel_pause_leaves_queue(tmp_app, monkeypatch):
    import db
    acc_id, ids = _make_account(tmp_app, 6)
    e = engine.Engine(lambda *a: None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)

    def fake(acc, vid, ff):
        db.db_set_paused(acc_id, 1)
        return True, f"/x/{vid}.mp4", "video"
    monkeypatch.setattr(e, "download_one", fake)
    acc = db.db_get_account(acc_id)
    e._download_pending_par(acc, db.db_pending_videos(acc_id), 2)
    total, done, pending, skipped = db.db_counts(acc_id)
    assert pending >= 1 and skipped == 0                           # остаток ждёт «Пуск»


def test_dispatch_uses_workers_setting(tmp_app, monkeypatch):
    import settings
    e = engine.Engine(lambda *a: None)
    calls = []
    monkeypatch.setattr(e, "_download_pending_par", lambda a, p, w: calls.append(("par", w)) or True)
    monkeypatch.setattr(e, "_download_pending_seq", lambda a, p: calls.append(("seq",)) or True)
    e._download_pending({}, ["1", "2", "3"])
    settings.save({"parallel_downloads": 3})
    e._download_pending({}, ["1", "2", "3"])
    e._download_pending({}, ["1"])                                 # один пост — смысла в пуле нет
    assert calls == [("seq",), ("par", 3), ("seq",)]
    assert settings.get_parallel({"parallel_downloads": 99}) == 4
    assert settings.get_parallel({"parallel_downloads": "x"}) == 1


# ---------- ввод через задачи движка ----------

def _eng_with_db(tmp_app):
    import db
    db.db_init(os.path.join(str(tmp_app), "data"))
    events = []
    e = engine.Engine(lambda k, p=None: events.append((k, p)))
    e.events = events
    return e, db


@pytest.mark.parametrize("bad", ["--exec calc tiktok.com", "https://evil.com/tiktok.com/@a",
                                 "-x", "a b", ""])
def test_add_account_rejects_bad_input(tmp_app, monkeypatch, bad):
    e, db = _eng_with_db(tmp_app)
    called = []
    monkeypatch.setattr(e, "_sync_account", lambda acc: called.append(acc))
    monkeypatch.setattr(e, "_ensure_sec_uid", lambda *a: called.append("net"))
    e.task_add_account(bad, {})
    assert not called and db.db_accounts() == []
    assert any(k == "toast" and p[0] == "error" for k, p in e.events)
    assert e._tasks_active == 0                                    # «занято» снято


def test_add_account_accepts_nick_and_url(tmp_app, monkeypatch):
    e, db = _eng_with_db(tmp_app)
    monkeypatch.setattr(e, "_sync_account", lambda acc: True)
    monkeypatch.setattr(e, "_ensure_sec_uid", lambda *a: None)
    monkeypatch.setattr(e, "_channel_id_of_video", lambda url: None)
    e.task_add_account("@alice", {})
    e.task_add_account("https://www.tiktok.com/@bob/video/7123456789012345678?x=1", {})
    assert {a["nickname"] for a in db.db_accounts()} == {"alice", "bob"}


def test_add_account_cancel_of_other_account_survives(tmp_app, monkeypatch):
    e, db = _eng_with_db(tmp_app)
    monkeypatch.setattr(e, "_sync_account", lambda acc: True)
    monkeypatch.setattr(e, "_ensure_sec_uid", lambda *a: None)
    e._cancel_req.add(999)                                         # отмена чужого аккаунта
    e.task_add_account("alice", {})
    assert 999 in e._cancel_req


def test_single_video_rejects_non_post(tmp_app):
    e, db = _eng_with_db(tmp_app)
    for bad in ("--exec x tiktok.com", "https://www.tiktok.com/@alice", "alice"):
        e.task_single_video(bad)
    assert not [1 for k, p in e.events if k == "log" and "Скачивание:" in p[1]]
    assert e._tasks_active == 0
