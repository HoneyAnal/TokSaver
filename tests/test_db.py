import os
import sqlite3

import db


def _init(tmp_app):
    db.db_init(os.path.join(str(tmp_app), "data"))


def test_account_and_batch_upsert(tmp_app):
    _init(tmp_app)
    acc_id, created = db.db_add_account("alice", "MS4wLjABAAAA" + "a" * 60)
    assert created
    assert db.db_add_account("alice")[1] is False                    # дубль по нику
    rows = [("1000000001", "20240101", "t1"), ("1000000002", "20240102", "t2"),
            ("1000000003", None, None)]
    assert db.db_upsert_videos(acc_id, rows) == 3
    assert db.db_upsert_videos(acc_id, rows) == 0                    # повтор ничего не добавляет
    assert db.db_upsert_videos(acc_id, []) == 0
    assert db.db_known_video_ids(acc_id) == {"1000000001", "1000000002", "1000000003"}


def test_batch_skip_and_counts(tmp_app):
    _init(tmp_app)
    acc_id, _ = db.db_add_account("bob")
    db.db_upsert_videos(acc_id, [(f"10000000{i:02d}", "20240101", "t") for i in range(5)])
    assert db.db_skip_videos(acc_id, ["1000000000", "1000000001"]) == 2
    assert db.db_skip_videos(acc_id, ["1000000000"]) == 0            # уже пропущен
    total, done, pending, skipped = db.db_counts(acc_id)
    assert (total, done, pending, skipped) == (5, 0, 3, 2)
    db.db_mark_downloaded(acc_id, "1000000004", "x.mp4", "video")
    assert db.db_counts(acc_id)[1] == 1
    assert "1000000004" not in db.db_pending_videos(acc_id)


def test_wal_enabled(tmp_app):
    _init(tmp_app)
    acc_id, _ = db.db_add_account("carol")
    path = os.path.join(str(tmp_app), "data", "accounts.db")
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    d = db.account_dir(acc_id)
    with sqlite3.connect(os.path.join(d, "videos.db")) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_deleted_videos_db_is_recreated(tmp_app):
    _init(tmp_app)
    acc_id, _ = db.db_add_account("dave")
    db.db_upsert_videos(acc_id, [("1000000001", "20240101", "t")])
    d = db.account_dir(acc_id)
    db._close_acc_conn(acc_id)
    for name in os.listdir(d):
        if name.startswith("videos.db"):
            os.remove(os.path.join(d, name))
    assert db.db_known_video_ids(acc_id) == set()                    # база создана заново, без падения
