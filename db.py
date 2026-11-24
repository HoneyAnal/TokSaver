"""Хранилище TokSaver: у КАЖДОГО аккаунта — своя отдельная папка.

Структура (всё внутри data/ рядом с программой):

  data/
    accounts.db            — реестр аккаунтов (только список, без постов)
    0001_<ник>/            — папка одного аккаунта
        videos.db          — посты этого аккаунта (SQLite)
        media/             — скачанные видео и фото этого аккаунта
    0002_<ник>/
        ...

Зачем так:
  - аккаунт удаляется одним движением — вместе с папкой;
  - можно удалить папку прямо в Проводнике: при следующей синхронизации
    программа заметит это, создаст папку заново и перекачает аккаунт;
  - проблемы одного аккаунта не задевают остальных;
  - папку легко перенести/скопировать — она самодостаточна.

База привязана к ПОСТОЯННОМУ ID профиля (sec_uid): ник можно менять —
при синхронизации программа это увидит, обновит запись и переименует папку.

Все публичные функции берут DB_LOCK; внутренние `_nolock`-хелперы — нет
(вызывать только уже под блокировкой: threading.Lock не реентерабельный).
"""
from i18n import tr
import os
import re
import shutil
import sqlite3
import threading

import applog

_LOG = applog.get(__name__)

DB_LOCK = threading.Lock()
_conn = None            # соединение с реестром accounts.db
_conns = {}             # acc_id → соединение с videos.db этого аккаунта
DATA_DIR = None

# заметки для журнала (миграция и т.п.) — интерфейс забирает их при старте
_NOTES = []

_ACC_COLS = ("id, nickname, sec_uid, added_at, last_sync, note, "
             "limit_count, date_from, date_to, paused, folder, range_kind")
_ACC_KEYS = ("id", "nickname", "sec_uid", "added_at", "last_sync", "note",
             "limit_count", "date_from", "date_to", "paused", "folder", "range_kind")


def note(msg):
    _NOTES.append(msg)


def take_notes():
    out = _NOTES[:]
    _NOTES.clear()
    return out


# ------------------------------ имена папок ------------------------------

def _folder_for(acc_id):
    """Постоянное имя папки: только номер (0001, 0002…).

    Ник — величина непостоянная, поэтому имя папки от него НЕ зависит:
    при смене логина папка не переименовывается, а текущий ник пишется
    в файл account.txt внутри папки. Папка живёт по постоянному ID.
    """
    return f"{acc_id:04d}"


def _fmt_d(s):
    """'20240513' → '13.05.2024' — для файла account.txt."""
    s = s or ""
    if len(s) == 8 and s.isdigit():
        return f"{s[6:8]}.{s[4:6]}.{s[:4]}"
    return s


def _range_line_nolock(acc):
    """Строка о диапазоне для account.txt (или None, если диапазона нет)."""
    rk = acc.get("range_kind")
    if rk == "tail" and acc.get("limit_count"):
        return tr('range: last {0} posts', acc['limit_count'])
    if rk == "period" and (acc.get("date_from") or acc.get("date_to")):
        df = _fmt_d(acc.get("date_from")) or tr('beginning')
        dt = _fmt_d(acc.get("date_to")) or tr('today')
        return tr('range: {0} — {1}', df, dt)
    if not rk and (acc.get("date_from") or acc.get("date_to") or acc.get("limit_count")):
        return (tr('range (old record): {0} {1} — {2}', acc.get('limit_count') or '', _fmt_d(acc.get('date_from')), _fmt_d(acc.get('date_to')))).strip()
    return None


def _write_profile_nolock(acc):
    """Пишет/обновляет файл account.txt внутри папки аккаунта:
    текущий логин, ссылка, постоянный ID, заметка, диапазон, время синка."""
    d = _dir_of(acc)
    if not d or not os.path.isdir(d):
        return
    lines = [tr('login: @{0}', acc['nickname']),
             tr('link: https://www.tiktok.com/@{0}', acc['nickname'])]
    if acc.get("sec_uid"):
        lines.append(tr('permanent ID: {0}', acc['sec_uid']))
    rng = _range_line_nolock(acc)
    if rng:
        lines.append(rng)
    if acc.get("note"):
        lines.append(tr('note: {0}', acc['note']))
    if acc.get("added_at"):
        lines.append(tr('added: {0}', acc['added_at']))
    if acc.get("last_sync"):
        lines.append(tr('synced: {0}', acc['last_sync']))
    try:
        with open(os.path.join(d, "account.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass  # файл занят/диск — не критично, обновится при следующей записи


# --------- внутренние хелперы (БЕЗ блокировки — звать только под DB_LOCK) ---------

def _get_account_nolock(acc_id):
    row = _conn.execute(f"SELECT {_ACC_COLS} FROM accounts WHERE id=?", (acc_id,)).fetchone()
    return dict(zip(_ACC_KEYS, row)) if row else None


def _dir_of(acc_or_id):
    """Папка аккаунта на диске (без захвата блокировки)."""
    if isinstance(acc_or_id, dict):
        acc = acc_or_id
    else:
        acc = _get_account_nolock(acc_or_id)
    if not acc:
        return None
    folder = acc.get("folder") or _folder_for(acc["id"])
    return os.path.join(DATA_DIR, folder)


def _tune(conn):
    """WAL: читатели не ждут писателя, запись быстрее и устойчивее к сбоям.
    busy_timeout: при занятой базе ждём до 5 с вместо мгновенной ошибки."""
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error as e:
        _LOG.warning('database PRAGMAs not applied: %r', e)


def _open_videos_db(dir_path):
    conn = sqlite3.connect(os.path.join(dir_path, "videos.db"), check_same_thread=False)
    _tune(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS videos (
        video_id TEXT PRIMARY KEY,
        upload_date TEXT,
        title TEXT,
        downloaded INTEGER DEFAULT 0,
        file_path TEXT,
        downloaded_at TEXT,
        kind TEXT,
        description TEXT)""")
    # старые базы: добавляем колонку описания (появилась позже остальных)
    have = {r[1] for r in conn.execute("PRAGMA table_info(videos)").fetchall()}
    if "description" not in have:
        conn.execute("ALTER TABLE videos ADD COLUMN description TEXT")
    conn.commit()
    return conn


def _acc_conn(acc_id):
    """Соединение с videos.db аккаунта (кэш). Если файл снёсли — пересоздаём."""
    conn = _conns.get(acc_id)
    d = _dir_of(acc_id)
    if conn is not None and (d is None or not os.path.exists(os.path.join(d, "videos.db"))):
        try:
            conn.close()
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
        conn = None
    if conn is None:
        if d is None:
            return None
        os.makedirs(d, exist_ok=True)
        conn = _open_videos_db(d)
        _conns[acc_id] = conn
    return conn


def _close_acc_conn(acc_id):
    conn = _conns.pop(acc_id, None)
    if conn is not None:
        try:
            conn.close()
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass


# ------------------------------ публичные пути ------------------------------

def account_dir(acc):
    """Папка аккаунта. Принимает dict аккаунта или числовой id."""
    with DB_LOCK:
        return _dir_of(acc)


def account_media_dir(acc_id):
    """Папка media/ аккаунта (создаётся при необходимости)."""
    with DB_LOCK:
        d = _dir_of(acc_id)
    if not d:
        return None
    media = os.path.join(d, "media")
    os.makedirs(media, exist_ok=True)
    return media


# ------------------------------ инициализация ------------------------------

def db_init(data_dir):
    """Открывает реестр аккаунтов и папки данных. Повторный вызов безопасен."""
    global DATA_DIR, _conn
    DATA_DIR = data_dir
    os.makedirs(data_dir, exist_ok=True)
    for acc_id in list(_conns):        # при смене каталога данных кэш недействителен
        _close_acc_conn(acc_id)
    if _conn is not None:
        try:
            _conn.close()
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
    _conn = sqlite3.connect(os.path.join(data_dir, "accounts.db"), check_same_thread=False)
    _tune(_conn)
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.execute("""CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nickname TEXT NOT NULL,
        sec_uid TEXT UNIQUE,
        added_at TEXT DEFAULT (datetime('now', 'localtime')),
        last_sync TEXT,
        note TEXT,
        limit_count INTEGER,
        date_from TEXT,
        date_to TEXT,
        paused INTEGER DEFAULT 0,
        folder TEXT,
        range_kind TEXT)""")
    # старые базы: добываем колонку range_kind (вид диапазона: tail/period/all)
    have = {r[1] for r in _conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "range_kind" not in have:
        _conn.execute("ALTER TABLE accounts ADD COLUMN range_kind TEXT")
    _conn.commit()
    _migrate_legacy()
    _migrate_folder_names()


def _migrate_folder_names():
    """Старые папки «0001_ник» → «0001»: имя папки теперь только по ID,
    а текущий логин живёт в файле account.txt внутри."""
    rows = _conn.execute("SELECT id, folder FROM accounts").fetchall()
    renamed = 0
    for acc_id, folder in rows:
        want = _folder_for(acc_id)
        if not folder or folder == want:
            continue
        old_dir = os.path.join(DATA_DIR, folder)
        new_dir = os.path.join(DATA_DIR, want)
        if os.path.isdir(old_dir) and not os.path.exists(new_dir):
            try:
                _close_acc_conn(acc_id)
                os.rename(old_dir, new_dir)
                _conn.execute("UPDATE accounts SET folder=? WHERE id=?", (want, acc_id))
                renamed += 1
            except OSError:
                continue  # папка занята — оставим как есть, работать будет так
        elif not os.path.exists(old_dir):
            _conn.execute("UPDATE accounts SET folder=? WHERE id=?", (want, acc_id))
    _conn.commit()
    for acc_id, _f in rows:
        acc = _get_account_nolock(acc_id)
        if acc:
            _write_profile_nolock(acc)
    if renamed:
        note(tr('Account folders were renamed by permanent ID ({0}): the current login is now in account.txt inside each folder.', renamed))


def _migrate_legacy():
    """Перенос старой однофайловой базы (tiktok.db) в новую структуру.

    Файлы из Downloads/<ник> переезжают в data/NNNN_<ник>/media, пути в базе
    переписываются. Старый tiktok.db сохраняется как tiktok.db.bak.
    """
    import paths

    legacy = paths.LEGACY_DB
    if not os.path.exists(legacy):
        return
    n = _conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    if n:
        return  # уже работали в новой структуре — старую базу не трогаем

    created_folders = []
    try:
        old = sqlite3.connect(legacy)
        old.execute("PRAGMA foreign_keys=ON")
        # добиваем колонки, которых не было в самых первых версиях
        have = {r[1] for r in old.execute("PRAGMA table_info(accounts)").fetchall()}
        for name, decl in (("limit_count", "INTEGER"), ("date_from", "TEXT"),
                           ("date_to", "TEXT"), ("paused", "INTEGER DEFAULT 0")):
            if name not in have:
                old.execute(f"ALTER TABLE accounts ADD COLUMN {name} {decl}")
        if "videos" in {r[0] for r in old.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            have_v = {r[1] for r in old.execute("PRAGMA table_info(videos)").fetchall()}
            if "kind" not in have_v:
                old.execute("ALTER TABLE videos ADD COLUMN kind TEXT")
        old.commit()

        accs = old.execute(
            "SELECT id, nickname, sec_uid, added_at, last_sync, note, "
            "limit_count, date_from, date_to, paused FROM accounts").fetchall()
        for (_oid, nick, sec, added, lastsync, note_, lc, df, dt, paused) in accs:
            cur = _conn.execute(
                "INSERT INTO accounts(nickname, sec_uid, added_at, last_sync, note, "
                "limit_count, date_from, date_to, paused, folder) "
                "VALUES(?,?,?,?,?,?,?,?,?,'')",
                (nick, sec, added, lastsync, note_, lc, df, dt, paused or 0))
            new_id = cur.lastrowid
            folder = _folder_for(new_id)
            _conn.execute("UPDATE accounts SET folder=? WHERE id=?", (folder, new_id))
            _conn.commit()

            adir = os.path.join(DATA_DIR, folder)
            os.makedirs(adir, exist_ok=True)
            created_folders.append(adir)
            new_media = os.path.join(adir, "media")

            # переносим скачанные файлы, если они лежали в Downloads/<ник>
            old_media = os.path.join(paths.OUTPUT_DIR, nick)
            moved = False
            if os.path.isdir(old_media) and not os.path.exists(new_media):
                try:
                    shutil.move(old_media, new_media)
                    moved = True
                except OSError:
                    pass  # файл занят — оставим на старом месте

            vids = old.execute(
                "SELECT video_id, upload_date, title, downloaded, file_path, "
                "downloaded_at, kind FROM videos WHERE account_id=?", (_oid,)).fetchall()
            vconn = _open_videos_db(adir)
            old_prefix = old_media + os.sep
            new_prefix = new_media + os.sep
            for (vid, up, title, dl, fp, dlat, kind) in vids:
                if fp and moved and fp.startswith(old_prefix):
                    fp = new_prefix + fp[len(old_prefix):]
                vconn.execute("INSERT OR REPLACE INTO videos VALUES(?,?,?,?,?,?,?)",
                              (vid, up, title, dl, fp, dlat, kind))
            vconn.commit()
            vconn.close()
        old.close()

        os.replace(legacy, legacy + ".bak")
        note(tr('The old tiktok.db was moved to the data/ folder. Accounts: {0}. Backup of the old database: tiktok.db.bak.', len(accs)))
        if any(os.path.isdir(os.path.join(paths.OUTPUT_DIR, a[1])) for a in accs):
            note(tr('Media folders moved: Downloads/<nick> → data/<nick>/media.'))
    except Exception as e:
        # миграция не должна валить программу: сворачиваемся, старая база цела
        for acc_id in list(_conns):
            _close_acc_conn(acc_id)
        for d in created_folders:
            shutil.rmtree(d, ignore_errors=True)
        try:
            _conn.execute("DELETE FROM accounts")
            _conn.commit()
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
        note(tr('Migration of the old database failed ({0}). The app started with an empty database; the old tiktok.db is untouched — you can try again.', e))


# ------------------------------- аккаунты -------------------------------

def db_add_account(nickname, sec_uid=None, limit_count=None, date_from=None, date_to=None):
    """Добавляет аккаунт и создаёт ему папку. Возвращает (id, создан_ли_новый).

    Дедупликация по нику ИЛИ по постоянному ID — двойники не создаются.
    """
    with DB_LOCK:
        row = _conn.execute(
            "SELECT id FROM accounts WHERE nickname=? OR (? IS NOT NULL AND sec_uid=?)",
            (nickname, sec_uid, sec_uid)).fetchone()
        if row:
            if sec_uid:
                _conn.execute("UPDATE accounts SET sec_uid=? WHERE id=?", (sec_uid, row[0]))
                _conn.commit()
            return row[0], False
        cur = _conn.execute(
            "INSERT INTO accounts(nickname, sec_uid, limit_count, date_from, date_to, folder) "
            "VALUES(?,?,?,?,?,'')", (nickname, sec_uid, limit_count, date_from, date_to))
        acc_id = cur.lastrowid
        folder = _folder_for(acc_id)
        _conn.execute("UPDATE accounts SET folder=? WHERE id=?", (folder, acc_id))
        _conn.commit()
        os.makedirs(os.path.join(DATA_DIR, folder), exist_ok=True)
        _acc_conn(acc_id)  # сразу создаём videos.db
        _write_profile_nolock(_get_account_nolock(acc_id))  # логин внутри папки
        return acc_id, True


def db_accounts():
    """Все аккаунты (свежие добавленные сверху)."""
    with DB_LOCK:
        rows = _conn.execute(f"SELECT {_ACC_COLS} FROM accounts ORDER BY id DESC").fetchall()
    return [dict(zip(_ACC_KEYS, r)) for r in rows]


def db_get_account(acc_id):
    with DB_LOCK:
        return _get_account_nolock(acc_id)


def db_delete_account(acc_id, remove_files=False):
    """Удаляет аккаунт из реестра (посты — вместе с его videos.db).

    remove_files=True — папка аккаунта удаляется целиком (база + media).
    Возвращает (ок, путь_папки, текст_ошибки).
    """
    with DB_LOCK:
        acc = _get_account_nolock(acc_id)
        if not acc:
            return False, None, tr('account not found')
        _close_acc_conn(acc_id)
        _conn.execute("DELETE FROM accounts WHERE id=?", (acc_id,))
        _conn.commit()
        folder = _dir_of(acc)
        if remove_files and folder and os.path.isdir(folder):
            try:
                shutil.rmtree(folder)
            except OSError as e:
                return True, folder, tr('folder not deleted (in use?): {0}', e)
        return True, folder, None


def db_update_sec_uid(acc_id, sec_uid):
    with DB_LOCK:
        _conn.execute("UPDATE accounts SET sec_uid=? WHERE id=?", (sec_uid, acc_id))
        _conn.commit()
        _write_profile_nolock(_get_account_nolock(acc_id))


def db_rename_account(acc_id, nickname):
    """Смена логина: обновляет запись и файл account.txt.
    Папку НЕ переименовывает — имя папки (0001) постоянно по ID.
    Возвращает (ок, путь_папки_или_None)."""
    with DB_LOCK:
        acc = _get_account_nolock(acc_id)
        if not acc:
            return False, None
        _conn.execute("UPDATE accounts SET nickname=? WHERE id=?", (nickname, acc_id))
        _conn.commit()
        acc = _get_account_nolock(acc_id)
        _write_profile_nolock(acc)
        return True, _dir_of(acc)


def db_touch_sync(acc_id):
    with DB_LOCK:
        _conn.execute("UPDATE accounts SET last_sync=datetime('now','localtime') WHERE id=?",
                      (acc_id,))
        _conn.commit()
        _write_profile_nolock(_get_account_nolock(acc_id))


def db_set_note(acc_id, note_):
    with DB_LOCK:
        _conn.execute("UPDATE accounts SET note=? WHERE id=?", (note_, acc_id))
        _conn.commit()
        _write_profile_nolock(_get_account_nolock(acc_id))


def db_set_paused(acc_id, paused):
    with DB_LOCK:
        _conn.execute("UPDATE accounts SET paused=? WHERE id=?", (1 if paused else 0, acc_id))
        _conn.commit()


def db_set_date_floor(acc_id, date_yyyymmdd):
    """Нижняя граница по дате (используется режимом «последние N»)."""
    with DB_LOCK:
        _conn.execute("UPDATE accounts SET date_from=? WHERE id=?", (date_yyyymmdd, acc_id))
        _conn.commit()


def db_set_range_kind(acc_id, kind):
    """Вид диапазона аккаунта: 'tail' (последние N), 'period' (даты), 'all'.
    Нужен только интерфейсу — чтобы честно показать диапазон на карточке
    и не путать границу «последних N» с пользовательским периодом."""
    with DB_LOCK:
        _conn.execute("UPDATE accounts SET range_kind=? WHERE id=?", (kind, acc_id))
        _conn.commit()


def db_set_range(acc_id, kind, limit_count=None, date_from=None, date_to=None):
    """Сохранить диапазон аккаунта как ПОСТОЯННУЮ задачу: вид и параметры
    одной записью (диалог «Изменить диапазон…»). Диапазон действует при
    каждой синхронизации, пока его не изменят: «последние N» держит нижнюю
    границу, «период» — обе, «все» снимает фильтры."""
    with DB_LOCK:
        _conn.execute(
            "UPDATE accounts SET range_kind=?, limit_count=?, date_from=?, date_to=? "
            "WHERE id=?", (kind, limit_count, date_from, date_to, acc_id))
        _conn.commit()
        _write_profile_nolock(_get_account_nolock(acc_id))


def db_folder_exists(acc_id):
    """Есть ли папка аккаунта с базой постов на диске (могли удалить вручную)."""
    with DB_LOCK:
        acc = _get_account_nolock(acc_id)
        if not acc:
            return False
        d = _dir_of(acc)
    return bool(d) and os.path.isdir(d) and os.path.isfile(os.path.join(d, "videos.db"))


def db_ensure_folder(acc_id):
    """Пересоздаёт папку аккаунта, если её снесли (база постов начнётся заново)."""
    with DB_LOCK:
        d = _dir_of(acc_id)
        if not d:
            return
        os.makedirs(os.path.join(d, "media"), exist_ok=True)
        _acc_conn(acc_id)  # videos.db пересоздастся сам, если файла нет


# -------------------------------- посты --------------------------------

def db_upsert_video(acc_id, video_id, upload_date, title):
    """Добавляет пост в базу аккаунта. Возвращает True, если пост новый."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        cur = conn.execute(
            "INSERT OR IGNORE INTO videos(video_id, upload_date, title) VALUES(?,?,?)",
            (video_id, upload_date, title))
        conn.commit()
        return cur.rowcount > 0


def db_upsert_videos(acc_id, rows):
    """Пакетное добавление постов [(video_id, upload_date, title)…] ОДНОЙ
    транзакцией (раньше — коммит на каждый пост: сотни коммитов за синхронизацию,
    а блокировка DB_LOCK держалась на каждом). Возвращает число новых постов."""
    rows = list(rows)
    if not rows:
        return 0
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO videos(video_id, upload_date, title) VALUES(?,?,?)", rows)
        conn.commit()
        return conn.total_changes - before


def db_known_video_ids(acc_id):
    """Множество ID постов, УЖЕ лежащих в базе (в любом состоянии).
    Движок решает по нему судьбу каждого поста из свежего списка: известные
    не трогаем (в т.ч. возвращённые юзером «Скачать пропущенное»)."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        if conn is None:
            return set()
        rows = conn.execute("SELECT video_id FROM videos").fetchall()
    return {r[0] for r in rows}


def db_newest_downloaded_date(acc_id):
    """Самая свежая дата среди СКАЧАННЫХ постов (YYYYMMDD) или None.
    Страховочный floor для повторных проверок: закреплённый пост (пиннат)
    с датой постарше не занижает границу — берём максимум, а не минимум."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        if conn is None:
            return None
        row = conn.execute(
            "SELECT MAX(upload_date) FROM videos "
            "WHERE downloaded=1 AND upload_date IS NOT NULL").fetchone()
    return row[0] if row and row[0] else None


def db_pending_videos(acc_id, limit=0):
    """ID постов аккаунта, которые ещё нужно скачать (сначала свежие)."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        sql = "SELECT video_id FROM videos WHERE downloaded=0 ORDER BY rowid DESC"
        if limit and limit > 0:
            rows = conn.execute(sql + " LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute(sql).fetchall()
    return [r[0] for r in rows]


def db_mark_downloaded(acc_id, video_id, file_path, kind=None, description=None):
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        conn.execute(
            "UPDATE videos SET downloaded=1, file_path=?, kind=COALESCE(?, kind), "
            "description=COALESCE(?, description), "
            "downloaded_at=datetime('now','localtime') WHERE video_id=?",
            (file_path, kind, description, video_id))
        conn.commit()


def db_skip_video(acc_id, video_id):
    """Помечает пост как пропущенный (вне диапазона загрузки).
    Возвращает True, если пост действительно был переведён в пропуск."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        cur = conn.execute("UPDATE videos SET downloaded=2 WHERE video_id=? AND downloaded=0",
                           (video_id,))
        conn.commit()
        return cur.rowcount > 0


def db_skip_videos(acc_id, video_ids):
    """Пакетный вариант db_skip_video (одна транзакция). Возвращает число изменённых."""
    ids = [(v,) for v in video_ids]
    if not ids:
        return 0
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        before = conn.total_changes
        conn.executemany("UPDATE videos SET downloaded=2 WHERE video_id=? AND downloaded=0", ids)
        conn.commit()
        return conn.total_changes - before


def db_reset_skipped(acc_id):
    """Снимает все «пропуски»: посты снова попадут в очередь скачивания."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        cur = conn.execute("UPDATE videos SET downloaded=0 WHERE downloaded=2")
        conn.commit()
        return cur.rowcount


def db_latest_downloaded(acc_id):
    """(video_id, kind) самого свежего скачанного поста — источник обложки карточки."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        if conn is None:
            return None, None
        row = conn.execute(
            "SELECT video_id, kind FROM videos WHERE downloaded=1 "
            "ORDER BY downloaded_at DESC, rowid DESC LIMIT 1").fetchone()
    return (row[0], row[1]) if row else (None, None)


def db_set_description(acc_id, video_id, description):
    """Сохранить описание поста (вычитано из сайдкар-файла после скачки)."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        conn.execute("UPDATE videos SET description=? WHERE video_id=?",
                     (description, video_id))
        conn.commit()


def db_posts(acc_id):
    """Все посты аккаунта (новые сверху) — для окна «Посты аккаунта».
    Список dict: video_id, upload_date, title, downloaded, file_path, kind,
    description. Посты без даты — в конце списка."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT video_id, upload_date, title, downloaded, file_path, kind, "
            "description FROM videos "
            "ORDER BY (upload_date IS NULL), upload_date DESC, rowid DESC").fetchall()
    keys = ("video_id", "upload_date", "title", "downloaded", "file_path",
            "kind", "description")
    return [dict(zip(keys, r)) for r in rows]


def db_heal_file_path(acc_id, video_id, path):
    """Чинит сохранённый путь файла: файл найден в media/ по ID поста,
    а в базе был записан неверный/устаревший путь."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        conn.execute("UPDATE videos SET file_path=? WHERE video_id=? AND downloaded=1",
                     (path, video_id))
        conn.commit()


def db_requeue_videos(acc_id, video_ids):
    """Возвращает скачанные посты в очередь (downloaded=0) — например,
    по кнопке «Проверить файлы и докачать». Возвращает, сколько реально вернулось."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        n = 0
        for vid in video_ids:
            cur = conn.execute(
                "UPDATE videos SET downloaded=0 WHERE video_id=? AND downloaded=1",
                (vid,))
            n += cur.rowcount
        conn.commit()
    return n


def db_force_queue(acc_id, video_id):
    """Пост (скачанный ИЛИ пропущенный) — обратно в очередь по ЯВНОМУ
    запросу («Перекачать» в окне постов). Возвращает True, если состояние
    изменилось."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        cur = conn.execute(
            "UPDATE videos SET downloaded=0 WHERE video_id=? AND downloaded!=0",
            (video_id,))
        conn.commit()
        return cur.rowcount > 0


def db_counts(acc_id):
    """(всего, скачано, в очереди, вне диапазона).
    «всего» включает пропущенные — интерфейс показывает их отдельно,
    чтобы «11/60» не пугало при диапазоне на 5 постов."""
    with DB_LOCK:
        conn = _acc_conn(acc_id)
        if conn is None:
            return 0, 0, 0, 0
        total, done = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(downloaded=1),0) FROM videos").fetchone()
        pending, = conn.execute("SELECT COUNT(*) FROM videos WHERE downloaded=0").fetchone()
        skipped, = conn.execute("SELECT COUNT(*) FROM videos WHERE downloaded=2").fetchone()
    return total, done, pending, skipped
