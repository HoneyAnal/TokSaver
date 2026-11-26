"""Движок TokSaver: yt-dlp + ByeDPI + gallery-dl и все задачи загрузки.

Модуль полностью отделён от интерфейса: он получает функцию emit(kind, payload)
для событий (лог, статус карточки, обновление списка) и ничего не знает о tkinter.
"""
from i18n import tr
import json
import os
import re
import shutil
import concurrent.futures
import random
import stat
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse

import applog
import db
import netutil
import paths
import postmedia
import settings
import validate

_LOG = applog.get(__name__)

# --- Официальные бинарники. Ссылки ведут на реальные файлы релизов ---
YT_DLP_ASSETS = {
    "win32": "yt-dlp.exe",
    "linux": "yt-dlp_linux",
    "darwin": "yt-dlp_macos",
}
YT_DLP_ASSET = YT_DLP_ASSETS.get(sys.platform)
YT_DLP_EXE = os.path.join(paths.BIN_DIR, "yt-dlp.exe" if paths.IS_WIN else "yt-dlp")
YT_DLP_SUMS_NAME = "SHA2-256SUMS"


def ytdlp_release_base(version="latest"):
    """База ссылок релиза yt-dlp: latest или конкретный тег (из настроек)."""
    if version == "latest":
        return "https://github.com/yt-dlp/yt-dlp/releases/latest/download"
    return f"https://github.com/yt-dlp/yt-dlp/releases/download/{version}"


# ByeDPI закреплён на конкретной версии, контрольные суммы зашиты в код:
# подмена файла на GitHub (или по дороге) будет обнаружена, а не запущена.
BYEDPI_VERSION = "v0.17.3"
BYEDPI_URL = (f"https://github.com/hufrea/byedpi/releases/download/{BYEDPI_VERSION}"
              "/byedpi-17.3-x86_64-w64.zip")
BYEDPI_ZIP_SHA256 = "70d2c94147193cb915f9c6eb5144b8d404dacbcfa90bda2383b6b211afafa456"
BYEDPI_EXE_SHA256 = "eb53ceeeb981cc6735ac24bb1e51e725280b86630e80fdf19ddc4ee4a5b54ef4"
BYEDPI_EXE = os.path.join(paths.BIN_DIR, "ciadpi.exe")


def byedpi_args(port):
    """Аргументы ciadpi. ВАЖНО: у ciadpi НЕТ опции --socks — SOCKS5 это режим
    по умолчанию. Порт берётся из настроек (byedpi_port)."""
    return ["-i", "127.0.0.1", "-p", str(port),
            "--split", "1", "--disorder", "3+s",
            "--mod-http=h,d", "--auto=torst", "--tlsrec", "1+s"]


# версии для докачки в текущее окружение (должны совпадать с requirements.txt)
GDL_SPEC = "gallery-dl>=1.32.20,<2"
PYSOCKS_SPEC = "pysocks==1.7.1"

FAIL_STREAK_LIMIT = 8      # столько неудач подряд — похоже на лимит TikTok: очередь останавливаем
SEC_UID_RE = re.compile(r"MS4wLjABAAAA[A-Za-z0-9_-]{56,80}")  # постоянный ID профиля
# ID в JSON-данных страницы (ключ secUid) — надёжнее простого поиска по тексту
SEC_JSON_RE = re.compile(r'"secUid"\s*:\s*"(MS4wLjABAAAA[A-Za-z0-9_-]{56,80})"')
# Аватар профиля в JSON данных страницы: avatarLarger — самый крупный
AVATAR_RE = re.compile(r'"avatar(?:Larger|Medium|Thumb)"\s*:\s*"([^"]+)"')
# Аватар на embed-странице: <img src="https://p16-sign-va.tiktokcdn.com/obj/
# tos-…-avt-…/image">. Путь CDN-аватара всегда содержит «avt-» (объект типа
# avatar), у обложек постов этого маркера нет — не перепутаем.
AVATAR_IMG_RE = re.compile(r'<img[^>]+src="(https://p[^"]+avt[^"]+)"', re.I)
VIDEO_ID_RE = re.compile(r"\d{10,25}")
AUDIO_MEDIA_EXTS = {".m4a", ".mp3", ".aac", ".opus", ".wav", ".flac"}

SYNC_TAIL = 60  # сколько свежих постов смотрим при повторных проверках

LEVEL_INFO, LEVEL_WARN, LEVEL_ERROR, LEVEL_OK, LEVEL_HEAD = "info", "warn", "error", "ok", "head"
# дополнительные «цветные» уровни журнала (только оформление, на логику не влияют)
LEVEL_DL = "dl"        # что сейчас качается
LEVEL_SYS = "sys"      # установка и проверка компонентов, сеть
LEVEL_AUTH = "auth"    # авторизация / cookies
LEVEL_SUM = "sum"      # итоги
LEVEL_DIM = "dim"      # подробности вторым планом


def parse_enum_line(line):
    """Строка перечисления от yt-dlp: «uploader \\t id \\t upload_date \\t title».
    Возвращает (uploader|None, id, upload_date|None, title|None) либо None."""
    parts = line.split("\t", 3)
    if len(parts) < 2 or not VIDEO_ID_RE.fullmatch(parts[1]):
        return None
    uploader = parts[0].strip() if parts[0].strip() and parts[0].strip() != "NA" else None
    upload_date = parts[2].strip() if len(parts) > 2 and re.fullmatch(r"\d{8}", parts[2].strip()) else None
    title = re.sub(r"\s+", " ", parts[3].strip())[:300] \
        if len(parts) > 3 and parts[3].strip() and parts[3].strip() != "NA" else None
    return uploader, parts[1], upload_date, title


def parse_date(s):
    """'2024-05-13' или '13.05.2024' → '20240513' (формат дат TikTok). Иначе None."""
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(1) + m.group(2) + m.group(3)
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return m.group(3) + m.group(2) + m.group(1)
    return None


def fmt_date(s):
    """'20240513' → '13.05.2024' — для людей в журнале; прочее — как есть."""
    if s and len(s) == 8 and s.isdigit():
        return f"{s[6:8]}.{s[4:6]}.{s[:4]}"
    return s or ""


def apply_date_range(rows, date_from, date_to):
    """Период: делим посты на (качаем, пропускаем). Посты без даты не выбрасываем."""
    keep, skip = [], []
    for vid, date, _title in rows:
        if date and ((date_from and date < date_from) or (date_to and date > date_to)):
            skip.append(vid)
        else:
            keep.append(vid)
    return keep, skip


def find_sec_uid_in_text(text):
    """Постоянный ID профиля в HTML/JSON страницы (ключ secUid приоритетнее)."""
    if not text:
        return None
    m = SEC_JSON_RE.search(text)
    if m:
        return m.group(1)
    m = SEC_UID_RE.search(text)
    return m.group(0) if m else None


def looks_private(text):
    """Похоже, аккаунт закрытый (private)?"""
    if not text:
        return False
    low = text.lower()
    return '"privateaccount":true' in low.replace(" ", "") or "account is private" in low


# TikTok иногда показывает на ОТКРЫТЫХ профилях окно «Log in to TikTok»
# («этот автор ограничил аудиторию»): без входа список постов не отдаётся.
GATE_MARKS = ("log in to tiktok", "audience control", "войдите в tiktok",
              "ограничил аудиторию", "ограничила аудиторию", "login to continue")


def looks_login_gated(text):
    """Похоже, TikTok требует вход для просмотра этого профиля?"""
    if not text:
        return False
    low = text.lower()
    return any(mark in low for mark in GATE_MARKS)


class Engine:
    """Все операции с TikTok и бинарниками. Один объект на приложение."""

    def __init__(self, emit):
        self.emit = emit                # emit(kind, payload) — потокобезопасно (очередь в UI)
        self.settings = settings.load()
        self.proxy_port = settings.get_port(self.settings)
        self.ytdlp_version = settings.get_ytdlp_version(self.settings)
        self.use_byedpi = True
        self.sort_by_type = True        # новые файлы — в подпапки Video/ и Photos/
        self.cookies = ""               # 'chrome' / 'firefox' / 'edge'…
        self.cookies_file = None        # путь к экспортированному cookies.txt
        self.byedpi_process = None
        self.gdl_cmd = None             # команда gallery-dl (для фото-постов)
        self.deps_ready = False
        self._sync_run = None                # статистика текущей массовой синхронизации
        self._stop_all = False          # «Остановить всех» нажата
        self._cookie_copy_lock = threading.Lock()
        self._cancel_req = set()        # acc_id с нажатой «Стоп» (отмена очереди)
        self._state_lock = threading.RLock()   # счётчики задач, процессы, backoff
        self._tasks_active = 0          # сколько задач сейчас работает (для события «занято»)
        self._procs = {}                # запущенные yt-dlp/gallery-dl: {Popen: (старт, лимит с)}
        self._watchdog_started = False
        self._backoff = 0               # текущая пауза при «TikTok просит не частить», с
        self._throttle_until = 0.0
        self._api_warned = set()        # какие эндпоинты TikTok уже упомянуты в журнале

    # ------------------------------ лог и события ------------------------------
    def log(self, msg, level=LEVEL_INFO):
        # секреты (cookies, токены, путь к cookies.txt) в журнал не попадают
        self.emit("log", (level, applog.redact(msg)))

    def log_exc(self, title):
        """Показываем начало и КОНЕЦ трейсбека: суть ошибки — в последних
        строках, раньше они отрезались и в журнале оставалась загадка."""
        self.log(title, LEVEL_ERROR)
        lines = traceback.format_exc().splitlines()
        for line in lines[:2]:
            self.log("    " + line, LEVEL_ERROR)
        if len(lines) > 6:
            self.log("    …", LEVEL_ERROR)
        for line in lines[-3:]:
            self.log("    " + line, LEVEL_ERROR)

    # ------------------------------ потоки и процессы ------------------------------
    def _busy_enter(self):
        """Задача началась. Событие «занято» уходит только от первой задачи."""
        with self._state_lock:
            self._tasks_active += 1
            first = self._tasks_active == 1
        if first:
            self.emit("busy", True)

    def _busy_exit(self):
        """Задача закончилась. «Свободно» — когда закончилась ПОСЛЕДНЯЯ задача
        (раньше первая завершившаяся сбрасывала «занято» у остальных)."""
        with self._state_lock:
            self._tasks_active = max(0, self._tasks_active - 1)
            last = self._tasks_active == 0
        if last:
            self.emit("busy", False)

    def _track(self, proc, max_seconds):
        with self._state_lock:
            self._procs[proc] = (time.monotonic(), max_seconds)
            if not self._watchdog_started:
                self._watchdog_started = True
                threading.Thread(target=self._watchdog, name="proc-watchdog",
                                 daemon=True).start()

    @staticmethod
    def _kill_tree(proc):
        """Завершает процесс вместе с дочерними (yt-dlp.exe порождает потомков)."""
        try:
            if paths.IS_WIN:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=15,
                               creationflags=paths.CREATE_NO_WINDOW)
            proc.kill()
        except Exception as _exc:
            _LOG.debug('could not terminate process: %r', _exc)

    def _watchdog(self):
        """Один фоновый сторож на все процессы: убивает зависшие дольше лимита
        и забывает завершённые (отдельный Timer на каждый процесс не нужен)."""
        while True:
            time.sleep(5)
            now = time.monotonic()
            with self._state_lock:
                items = list(self._procs.items())
            for proc, (started, limit) in items:
                if proc.poll() is not None:
                    with self._state_lock:
                        self._procs.pop(proc, None)
                elif limit and now - started > limit:
                    self.log(tr('The process hung (over {0} s) — terminating it.', int(limit)), LEVEL_WARN)
                    self._kill_tree(proc)

    def _popen(self, cmd, max_seconds=1800, **kw):
        """subprocess.Popen с учётом процесса: его видит сторож (лимит времени)
        и shutdown() (при закрытии программы дочерние процессы не остаются сиротами)."""
        proc = subprocess.Popen(cmd, **kw)
        self._track(proc, max_seconds)
        return proc

    def _run(self, cmd, timeout=600, **kw):
        """Аналог subprocess.run(capture_output=True) с обязательным потолком
        времени и учётом процесса. Бросает subprocess.TimeoutExpired."""
        kw.pop("capture_output", None)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
        self._track(proc, None)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_tree(proc)
            proc.communicate()
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)

    def shutdown(self):
        """Закрытие программы: останавливаем ByeDPI и все дочерние процессы."""
        self.stop_byedpi()
        with self._state_lock:
            procs = list(self._procs)
        for proc in procs:
            if proc.poll() is None:
                self._kill_tree(proc)

    # --- «TikTok просит не частить»: общая пауза для всех потоков загрузки ---
    def _note_rate_limit(self):
        with self._state_lock:
            self._backoff = min(max(self._backoff * 2, 5), 120)
            self._throttle_until = time.monotonic() + self._backoff
            wait = self._backoff
        self.log(tr('    TikTok asks to slow down — shared pause {0} s.', wait), LEVEL_WARN)

    def _note_ok(self):
        with self._state_lock:
            self._backoff //= 2

    def _wait_throttle(self):
        while True:
            with self._state_lock:
                wait = self._throttle_until - time.monotonic()
            if wait <= 0:
                return
            time.sleep(min(wait, 1.0))

    # --- cookies.txt ---
    MAX_COOKIES_FILE = 5 * 1024 * 1024

    def _ytdlp_cookies_copy(self):
        """yt-dlp после КАЖДОГО запуска перезаписывает файл cookies (добавляет свои
        временные значения). Чтобы он не портил ваш файл — и не подсовывал gallery-dl
        «изменённые» cookies, — yt-dlp получает собственную рабочую копию в data/.
        Копия обновляется, когда меняется исходный файл. None — не вышло (берём оригинал)."""
        src = self.cookies_file
        if not src:
            return None
        try:
            st = os.stat(src)
            key = (os.path.abspath(src), st.st_mtime_ns, st.st_size)
            dst = os.path.join(paths.DATA_DIR, ".cookies.ytdlp.txt")
            with self._cookie_copy_lock:
                if getattr(self, "_cookie_copy_key", None) != key or not os.path.exists(dst):
                    os.makedirs(paths.DATA_DIR, exist_ok=True)
                    shutil.copyfile(src, dst)
                    if not paths.IS_WIN:
                        os.chmod(dst, 0o600)
                    self._cookie_copy_key = key
            return dst
        except OSError as _exc:
            _LOG.debug("cookies copy: %r", _exc)
            return None

    def set_cookies_file(self, path):
        """Подключает файл cookies.txt (None — отключить). Проверяет, что это
        обычный файл разумного размера, и на POSIX закрывает его от других
        пользователей (0600). True при успехе."""
        if not path:
            self.cookies_file = None
            return True
        try:
            st = os.stat(path)
        except OSError as e:
            self.log(tr('[Auth] The cookies file is unavailable: {0}', e.strerror or e), LEVEL_ERROR)
            return False
        if not stat.S_ISREG(st.st_mode) or st.st_size > self.MAX_COOKIES_FILE:
            self.log(tr('[Auth] This does not look like cookies.txt (not a file or too large).'),
                     LEVEL_ERROR)
            return False
        if not paths.IS_WIN:
            try:
                os.chmod(path, 0o600)
            except OSError as _exc:
                _LOG.debug("chmod cookies: %r", _exc)
        self.cookies_file = path
        return True

    # ------------------------------ бинарники ------------------------------
    @property
    def proxy_addr(self):
        return ("127.0.0.1", self.proxy_port)

    @property
    def proxy_url(self):
        return f"socks5://127.0.0.1:{self.proxy_port}"

    def _download_file(self, url, dest, expected_sha256=None, max_size=None):
        """Скачивание с докачкой .part и проверкой SHA-256. Сначала напрямую,
        при сбое сети — через ByeDPI (если он уже запущен)."""
        state = {"last": 0}

        def progress(done, total):
            if total and done - state["last"] >= 5 * 1024 * 1024:
                state["last"] = done
                self.log(tr('Downloaded {0} of {1} MB', done // (1024 * 1024), total // (1024 * 1024)), LEVEL_SYS)

        headers = {"User-Agent": paths.BROWSER_UA}
        try:
            netutil.download_file(url, dest, headers=headers, progress=progress,
                                  expected_sha256=expected_sha256, max_size=max_size)
        except netutil.IntegrityError:
            raise                      # подмена/порча файла — повтор через прокси не поможет
        except Exception as e:
            if not self._proxy_alive():
                raise
            _LOG.debug('direct download of %s failed (%r) — trying through the proxy', url, e)
            netutil.download_file(url, dest, headers=headers, proxy=self.proxy_addr,
                                  progress=progress, expected_sha256=expected_sha256,
                                  max_size=max_size)

    def _is_valid_ytdlp(self):
        if not os.path.exists(YT_DLP_EXE) or os.path.getsize(YT_DLP_EXE) < 1024 * 1024:
            return False
        try:
            with open(YT_DLP_EXE, "rb") as f:
                head = f.read(4)
        except OSError:
            return False
        if paths.IS_WIN:
            return head.startswith(b"MZ")
        return head == b"\x7fELF" or head[:2] in (b"#!", b"PK")

    def _ytdlp_version(self):
        try:
            r = self._run([YT_DLP_EXE, "--version"], timeout=30, text=True,
                          creationflags=paths.CREATE_NO_WINDOW)
            return (r.stdout or "").strip()
        except Exception as e:
            _LOG.debug('yt-dlp --version failed: %r', e)
            return None

    # --- yt-dlp: установка и проверка по SHA2-256SUMS релиза ---
    def _ytdlp_expected_sha(self):
        """Официальная контрольная сумма нужного релиза yt-dlp (None — не удалось получить)."""
        if not YT_DLP_ASSET:
            return None
        base = ytdlp_release_base(self.ytdlp_version)
        headers = {"User-Agent": paths.BROWSER_UA}
        data = None
        for proxy in (None, self.proxy_addr if self._proxy_alive() else None):
            try:
                data = netutil.http_get(f"{base}/{YT_DLP_SUMS_NAME}", headers=headers,
                                        timeout=30, proxy=proxy)
                break
            except Exception as e:
                _LOG.debug('SHA2-256SUMS not received (proxy=%s): %r', proxy, e)
        if data is None:
            return None
        return netutil.parse_sums(data.decode("utf-8", "ignore")).get(YT_DLP_ASSET)

    def _write_sidecar(self, path, sha):
        try:
            with open(path + ".sha256", "w", encoding="ascii") as f:
                f.write(sha + "\n")
        except OSError as e:
            _LOG.debug('could not write %s.sha256: %r', path, e)

    @staticmethod
    def _read_sidecar(path):
        try:
            with open(path + ".sha256", encoding="ascii") as f:
                return f.read().strip().lower() or None
        except OSError:
            return None

    def _install_ytdlp(self, expected):
        base = ytdlp_release_base(self.ytdlp_version)
        self.log(tr('Downloading the yt-dlp engine (~18 MB), version: {0}…', self.ytdlp_version), LEVEL_SYS)
        self._download_file(f"{base}/{YT_DLP_ASSET}", YT_DLP_EXE, expected_sha256=expected,
                            max_size=150 * 1024 * 1024)
        self._write_sidecar(YT_DLP_EXE, expected)
        if not paths.IS_WIN:
            os.chmod(YT_DLP_EXE, 0o755)
        self.log(tr('yt-dlp downloaded, SHA-256 checksum matches the official one.'), LEVEL_OK)

    def _ensure_ytdlp(self):
        """Гарантирует установленный, проверенный yt-dlp. True — можно работать.
        • файла нет/повреждён → скачать и сверить с SHA2-256SUMS релиза;
        • файл есть → сверить с записанной при установке суммой (защита от
          изменения после установки) и с официальной суммой нужного релиза:
          если вышла новая версия (или в настройках другая), обновить."""
        if not YT_DLP_ASSET:
            self.log(tr('Platform {0} is not supported automatically.', sys.platform), LEVEL_ERROR)
            return False
        expected = self._ytdlp_expected_sha()
        valid = self._is_valid_ytdlp()
        if valid:
            actual = netutil.sha256_file(YT_DLP_EXE)
            recorded = self._read_sidecar(YT_DLP_EXE)
            if recorded and recorded != actual:
                self.log(tr('yt-dlp.exe changed after installation — deleting and downloading again.'),
                         LEVEL_WARN)
                valid = False
            elif expected is None:
                self.log(tr('Could not get the official yt-dlp checksum (no network?). Using the already installed file.'), LEVEL_WARN)
                return True
            elif actual == expected:
                if not recorded:
                    self._write_sidecar(YT_DLP_EXE, actual)
                return True
            elif not self.settings.get("ytdlp_auto_update", True):
                self.log(tr('The installed yt-dlp differs from the official release, but auto-update is off (ytdlp_auto_update).'), LEVEL_WARN)
                return True
            else:
                self.log(tr('The installed yt-dlp differs from the required release — updating.'))
                valid = False
        elif os.path.exists(YT_DLP_EXE):
            self.log(tr('Found a corrupted yt-dlp — downloading again…'), LEVEL_WARN)
        if not valid:
            if expected is None:
                self.log(tr('Could not download yt-dlp: the release checksum is unavailable, and running an unverified file is unsafe.'), LEVEL_ERROR)
                return False
            try:
                if os.path.exists(YT_DLP_EXE):
                    os.remove(YT_DLP_EXE)
                self._install_ytdlp(expected)
            except netutil.IntegrityError as e:
                self.log(tr('yt-dlp NOT installed: {0}', e), LEVEL_ERROR)
                return False
            except Exception as e:
                _LOG.debug(tr('yt-dlp installation'), exc_info=True)
                self.log(tr('Could not download yt-dlp: {0}', e), LEVEL_ERROR)
                return False
        return self._is_valid_ytdlp()

    # --- ByeDPI ---
    def _byedpi_ok(self):
        """ciadpi.exe на месте и совпадает с закреплённой контрольной суммой."""
        if not os.path.exists(BYEDPI_EXE):
            return False
        try:
            return netutil.sha256_file(BYEDPI_EXE) == BYEDPI_EXE_SHA256
        except OSError:
            return False

    def _install_byedpi(self):
        zip_path = os.path.join(paths.BIN_DIR, "byedpi.zip")
        try:
            self._download_file(BYEDPI_URL, zip_path, expected_sha256=BYEDPI_ZIP_SHA256,
                                max_size=20 * 1024 * 1024)
            # извлекается один ciadpi.exe — остальное содержимое архива не трогаем
            if not netutil.safe_extract_member(zip_path, "ciadpi.exe", BYEDPI_EXE):
                raise FileNotFoundError(tr('ciadpi.exe not found inside the ByeDPI archive'))
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        if not self._byedpi_ok():
            os.remove(BYEDPI_EXE)
            raise netutil.IntegrityError(tr('ciadpi.exe does not match the expected checksum after extraction'))

    def _proxy_alive(self):
        return bool(self.byedpi_process and self.byedpi_process.poll() is None)

    def start_byedpi(self):
        """Запускает обходчик, если он скачан, проверен и ещё не работает.
        Порт — из настроек (byedpi_port); если занят, берётся ближайший свободный."""
        if not paths.IS_WIN or self._proxy_alive() or not os.path.exists(BYEDPI_EXE):
            return
        wanted = settings.get_port(self.settings)
        port = netutil.free_port(wanted)
        if port is None:
            self.log(tr('No free port found starting from {0} — ByeDPI not started.', wanted), LEVEL_WARN)
            return
        if port != wanted:
            self.log(tr('Port {0} is busy — ByeDPI will use port {1}.', wanted, port), LEVEL_WARN)
        self.proxy_port = port
        try:
            self.byedpi_process = subprocess.Popen(
                [BYEDPI_EXE] + byedpi_args(port),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=paths.CREATE_NO_WINDOW)
            self.log(tr('Network bypass started (SOCKS 127.0.0.1:{0}).', port), LEVEL_SYS)
        except OSError as e:
            self.log(tr('Could not start ByeDPI: {0}', e), LEVEL_WARN)
        self.emit("deps", {"ytdlp": self._is_valid_ytdlp(), "proxy": self._proxy_alive()})

    def stop_byedpi(self):
        if self._proxy_alive():
            try:
                self.byedpi_process.terminate()
            except OSError as e:
                _LOG.debug("ByeDPI terminate: %r", e)
        self.byedpi_process = None
        self.emit("deps", {"ytdlp": self._is_valid_ytdlp(), "proxy": False})

    def _ensure_gallery_dl(self):
        """gallery-dl качает фото-посты (карусели): yt-dlp от них берёт только музыку."""
        if paths.FROZEN:
            # в собранной версии gallery-dl вшит в exe: вызываем себя с ключом
            # --gallery-dl (его разбирает main.py) — pip и внешний Python не нужны
            cmd = [sys.executable, "--gallery-dl"]
            try:
                r = self._run(cmd + ["--version"], timeout=60, text=True,
                              creationflags=paths.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    self.log(tr('gallery-dl ready, version: {0}', (r.stdout or '').strip()), LEVEL_SYS)
                    return cmd
            except Exception as e:
                _LOG.debug('bundled gallery-dl failed: %r', e)
            self.log(tr('gallery-dl is not installed: {0}. Photo posts will be skipped.',
                        'bundled copy failed'), LEVEL_WARN)
            return None
        try:
            r = self._run([sys.executable, "-m", "gallery_dl", "--version"], timeout=30,
                          text=True, creationflags=paths.CREATE_NO_WINDOW)
            if r.returncode == 0:
                self.log(tr('gallery-dl ready, version: {0}', (r.stdout or '').strip()), LEVEL_SYS)
                return [sys.executable, "-m", "gallery_dl"]
        except Exception as e:
            _LOG.debug('gallery-dl not found: %r', e)
        self.log(tr('Installing gallery-dl (for photo posts)…'), LEVEL_SYS)
        try:
            # pysocks нужен gallery-dl для SOCKS-прокси
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
                                   GDL_SPEC, PYSOCKS_SPEC],
                                  creationflags=paths.CREATE_NO_WINDOW, timeout=900)
            self.log(tr('gallery-dl installed.'), LEVEL_SYS)
            return [sys.executable, "-m", "gallery_dl"]
        except Exception as e:
            self.log(tr('gallery-dl is not installed: {0}. Photo posts will be skipped.', e), LEVEL_WARN)
            return None

    def check_deps(self):
        """Фоновая проверка компонентов при старте (запускается в потоке)."""
        try:
            self.log(tr('Checking components…'), LEVEL_SYS)
            os.makedirs(paths.BIN_DIR, exist_ok=True)
            os.makedirs(paths.OUTPUT_DIR, exist_ok=True)

            if paths.IS_WIN:
                # ByeDPI ставим первым: через него пойдёт и загрузка yt-dlp,
                # если GitHub напрямую недоступен
                if not self._byedpi_ok():
                    if os.path.exists(BYEDPI_EXE):
                        self.log(tr('ciadpi.exe does not match the verified version — downloading again…'), LEVEL_WARN)
                    self.log(tr('Downloading the ByeDPI network bypass {0}…', BYEDPI_VERSION), LEVEL_SYS)
                    try:
                        self._install_byedpi()
                    except Exception as e:
                        _LOG.debug(tr('ByeDPI installation'), exc_info=True)
                        self.log(tr('Could not set up ByeDPI: {0}', e), LEVEL_WARN)
                if self.use_byedpi:
                    self.start_byedpi()
                if self._proxy_alive():
                    self.log(tr('TikTok will be reachable through the local SOCKS proxy.'), LEVEL_SYS)
                else:
                    self.log(tr('Bypass unavailable — working directly.'), LEVEL_WARN)

            if not self._ensure_ytdlp():
                self.log(tr('yt-dlp is unavailable — downloading will not work. Check your internet and restart the app.'), LEVEL_ERROR)
                return
            ver = self._ytdlp_version()
            self.log(tr('yt-dlp ready, version: {0}', ver or tr('unknown')), LEVEL_SYS)

            # --- gallery-dl (фото-посты) ---
            self.gdl_cmd = self._ensure_gallery_dl()

            # --- pysocks: нужен gallery-dl для SOCKS-прокси (самому движку — нет) ---
            try:
                import socks  # noqa: F401
            except ImportError:
                self.log(tr('Installing pysocks (gallery-dl through the proxy)…'), LEVEL_SYS)
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
                                           PYSOCKS_SPEC], creationflags=paths.CREATE_NO_WINDOW,
                                          timeout=600)
                except Exception as e:
                    self.log(tr('pysocks is not installed: {0}. gallery-dl will go directly.', e), LEVEL_WARN)

            self.deps_ready = True
            self.emit("deps", {"ytdlp": True, "proxy": self._proxy_alive()})
            self.log(tr('Everything is ready!'), LEVEL_OK)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            _LOG.exception("check_deps")
            self.log_exc(tr('Something broke while checking components:'))

    # ------------------------------ yt-dlp ------------------------------
    def _base_args(self, use_proxy=True):
        args = []
        if use_proxy and self._proxy_alive():
            args += ["--proxy", self.proxy_url]
        # Авторизация (закрытые аккаунты): файл cookies.txt приоритетнее браузера
        if self.cookies_file:
            args += ["--cookies", self._ytdlp_cookies_copy() or self.cookies_file]
        elif self.cookies:
            args += ["--cookies-from-browser", self.cookies]
        args += ["--socket-timeout", "20", "--retries", "3", "--no-warnings"]
        return args

    def _channel_id_of_video(self, video_url):
        """Постоянный ID профиля (sec_uid) автора любого поста:
        сначала yt-dlp (с прокси, если он работает, и без него — как при ручном
        запуске), если не вышло — embed-страница поста. Причина сбоя пишется в журнал."""
        attempts = [True, False] if self._proxy_alive() else [False]
        for use_proxy in attempts:
            cmd = [YT_DLP_EXE, validate.safe_arg(video_url), "--print", "channel_id"] \
                  + self._base_args(use_proxy=use_proxy)
            try:
                r = self._run(cmd, text=True, encoding="utf-8", errors="ignore",
                              timeout=180, creationflags=paths.CREATE_NO_WINDOW)
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                self.log(tr('yt-dlp could not read the post: {0}', _exc), LEVEL_DIM)
                continue
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if SEC_UID_RE.fullmatch(line):
                    return line
            err = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
            if err:
                self.log(tr('yt-dlp could not read the post: {0}',
                            err[-1][:300]), LEVEL_DIM)
        return self._secuid_via_embed(video_url)

    def _http_get(self, url, timeout, proxy=None):
        """Один GET с браузерными заголовками. Текст страницы.
        Если подан файл cookies.txt — прикрепляем cookies: фолбэк-запросы
        идут авторизованными, и замок «Log in to TikTok» чаще пропускает.
        proxy — (host, port) SOCKS5 или None (напрямую)."""
        headers = {
            "User-Agent": paths.BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        cookie = self._cookie_header()
        host = urllib.parse.urlsplit(url).hostname
        if cookie and validate.is_tiktok_host(host):     # на чужие хосты cookies не отправляем
            headers["Cookie"] = cookie
        data = netutil.http_get(url, headers=headers, timeout=timeout, proxy=proxy)
        return data.decode("utf-8", "ignore")

    def _cookie_header(self):
        """Строка Cookie из экспортированного cookies.txt (формат Netscape).
        Браузерные cookies (–cookies-from-browser) сюда не попадают: их умеет
        читать только yt-dlp/gallery-dl."""
        if not self.cookies_file:
            return None
        try:
            with open(self.cookies_file, encoding="utf-8", errors="ignore") as f:
                pairs = []
                for line in f:
                    line = line.rstrip("\n")
                    if not line.strip() or line.startswith("# "):
                        continue
                    if line.startswith("#HttpOnly_"):
                        line = line[len("#HttpOnly_"):]
                    parts = line.split("\t")
                    # берём только cookies домена tiktok.com — остальное из файла не читаем
                    if (len(parts) >= 7 and parts[5] and parts[6]
                            and validate.is_tiktok_host(parts[0].lstrip("."))):
                        pairs.append(f"{parts[5]}={parts[6]}")
            return "; ".join(pairs) if pairs else None
        except OSError:
            return None

    def _http_get_socks(self, url, timeout):
        """Тот же GET через локальный SOCKS-прокси (ByeDPI). Собственный
        SOCKS5-клиент (netutil) — без подмены socket.socket на весь процесс."""
        try:
            return self._http_get(url, timeout, proxy=self.proxy_addr)
        except Exception as e:
            _LOG.debug('GET via SOCKS failed (%s): %r', url, e)
            return None

    def _fetch_url(self, url, timeout=30):
        """GET с браузерными заголовками. Если жив ByeDPI — сначала через него
        (TikTok напрямую у многих недоступен), иначе напрямую.
        НИКОГДА не бросает исключение: HTTP 403/сброс соединения на прямом
        запросе — обычное дело, это не повод убивать всю задачу.
        Возвращает текст страницы или None."""
        via_proxy = self._proxy_alive()
        order = (self._http_get_socks, self._http_get) if via_proxy \
            else (self._http_get, self._http_get_socks)
        for getter in order:
            try:
                text = getter(url, timeout)
            except Exception as e:
                _LOG.debug("GET %s (%s): %r", url, getter.__name__, e)
                text = None
            if text:
                return text
        return None

    def _oembed_info(self, post_url):
        """JSON официального oembed-эндпоинта TikTok: ник автора и обложка
        (thumbnail_url) поста. Лёгкий и обычно не блокируется. dict или None."""
        q = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(post_url, safe="")
        html = self._fetch_url(q)
        if not html:
            return None
        try:
            data = json.loads(html)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _oembed_nick(self, post_url):
        """Ник автора поста из oembed-эндпоинта. sec_uid он не отдаёт."""
        data = self._oembed_info(post_url)
        if not data:
            return None
        nick = data.get("author_unique_id") or data.get("author_name")
        return nick.strip().lstrip("@") if nick and nick.strip() else None

    @staticmethod
    def _save_image_bytes(data, dest):
        """Проверяет, что это картинка (jpeg/png/webp), и пишет в файл. bool."""
        if not data or len(data) < 1024:
            return False
        is_img = (data[:3] == b"\xff\xd8\xff"                    # jpeg
                  or data[:8] == b"\x89PNG\r\n\x1a\n"           # png
                  or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))  # webp
        if not is_img:
            return False
        try:
            with open(dest + ".part", "wb") as f:
                f.write(data)
            os.replace(dest + ".part", dest)
            return True
        except OSError:
            return False

    def _http_get_image(self, url, limit, via_socks):
        """Байты картинки одним способом: напрямую или через локальный SOCKS."""
        try:
            return netutil.http_get(url, headers={"User-Agent": paths.BROWSER_UA}, timeout=25,
                                    proxy=self.proxy_addr if via_socks else None, limit=limit)
        except Exception as e:
            _LOG.debug('image %s (socks=%s): %r', url, via_socks, e)
            return None

    def _get_image_bytes(self, url, limit=3 * 1024 * 1024):
        """Байты картинки CDN TikTok. Если жив ByeDPI — сначала через SOCKS:
        CDN у большинства заблокирован так же, как сайт, и прямая попытка
        лишь тормозит; при неудаче — второй путь (напрямую / через SOCKS)."""
        order = (True, False) if self._proxy_alive() else (False, True)
        for via_socks in order:
            data = self._http_get_image(url, limit, via_socks)
            if data:
                return data
        return None

    def _download_small_image(self, url, dest):
        """Скачивает картинку (обложка поста / аватар) в файл. Если жив
        ByeDPI — через него, иначе напрямую; при сбое пробуется второй путь.
        bool."""
        if not url:
            return False
        data = self._get_image_bytes(url)
        return self._save_image_bytes(data, dest)

    def _download_image(self, url, dest):
        """Картинка CDN TikTok в файл — обёртка над _download_small_image
        (тот же алгоритм: прокси при живом ByeDPI, иначе напрямую)."""
        return self._download_small_image(url, dest)

    def task_fetch_thumb(self, acc_id):
        """Обложка карточки аккаунта (запускается в потоке из UI):
        последнее скачанное видео → oembed → thumbnail_url → cover.jpg
        в папке аккаунта (кэш: при следующем запуске сеть не нужна)."""
        try:
            acc = db.db_get_account(acc_id)
            if not acc:
                self.emit("thumb", (acc_id, None))
                return
            adir = db.account_dir(acc_id)
            cover = os.path.join(adir, "cover.jpg") if adir else None
            if cover and os.path.exists(cover):
                self.emit("thumb", (acc_id, cover))
                return
            vid, _kind = db.db_latest_downloaded(acc_id)
            if not vid or not cover:
                self.emit("thumb", (acc_id, None))
                return
            info = self._oembed_info(f"https://www.tiktok.com/@{acc['nickname']}/video/{vid}")
            thumb = (info or {}).get("thumbnail_url")
            ok = self._download_small_image(thumb, cover) if thumb else False
            self.emit("thumb", (acc_id, cover if ok else None))
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.emit("thumb", (acc_id, None))

    def _warn_api_changed(self, name, why):
        """Один раз за сеанс: понятное сообщение, что формат ответа TikTok не тот,
        который ждёт программа (а не «просто не вышло»)."""
        if name in self._api_warned:
            return
        self._api_warned.add(name)
        _LOG.warning("API TikTok %s: %s", name, why)
        self.log(tr('TikTok seems to have changed its internal API ({0}): {1}. The app is using fallbacks (yt-dlp, embed page). If nothing works, update the app and restart it to get a fresh yt-dlp.', name, why), LEVEL_WARN)

    def _check_api_shape(self, name, text, key):
        """Разбирает JSON ответа API и проверяет наличие ключа key. Возвращает dict
        или None. Честная ошибка API (statusCode != 0: нет такого пользователя и т.п.)
        изменением формата не считается."""
        try:
            data = json.loads(text)
        except ValueError:
            self._warn_api_changed(name, tr('the response is not JSON (placeholder or verification page)'))
            return None
        if not isinstance(data, dict):
            self._warn_api_changed(name, tr('unexpected response structure'))
            return None
        if key in data:
            return data
        if data.get("statusCode") not in (None, 0):
            return None
        self._warn_api_changed(name, tr('the response has no field “{0}”', key))
        return None

    def _avatar_url_of(self, nickname, sec_uid=None):
        r"""URL аватара профиля. Источники по очереди:
        1) страница профиля — поле avatarLarger/Medium/Thumb в JSON данных;
        2) API user/detail (второй шанс, если страница отдалась урезанной);
        3) embed-страница — то же JSON-поле либо <img> аватара на CDN
           (путь аватара содержит «avt-», см. AVATAR_IMG_RE);
        4) API постов по sec_uid (он уже лежит в базе): в каждом посте есть
           данные автора с аватаром.
        В JSON ссылки записаны как «https:\\u002F\\u002F…» или «https:\/\/…» —
        разворачиваем escape-последовательности в обычный адрес."""
        sources = (f"https://www.tiktok.com/@{nickname}",
                   f"https://www.tiktok.com/api/user/detail/?uniqueId={nickname}&aid=1988",
                   f"https://www.tiktok.com/embed/@{nickname}")
        for url in sources:
            html = self._fetch_url(url, timeout=25)
            if not html:
                continue
            m = AVATAR_RE.search(html)
            if m:
                try:
                    return json.loads(f'"{m.group(1)}"')
                except Exception as _exc:
                    _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                    pass
            if "/api/user/detail/" in url and not m:
                self._check_api_shape("user/detail", html, "userInfo")
            if "/embed/@" in url:            # embed: <img> аватара на CDN
                m = AVATAR_IMG_RE.search(html)
                if m:
                    return m.group(1).replace("\\/", "/")
        if sec_uid:
            q = ("https://www.tiktok.com/api/post/item_list/?aid=1988&count=1"
                 "&cursor=0&sourceType=8&secUid=" + urllib.parse.quote(sec_uid))
            html = self._fetch_url(q, timeout=25)
            data = self._check_api_shape("post/item_list", html, "itemList") if html else None
            if data:
                try:
                    items = data.get("itemList")
                    au = (items[0].get("author") or {}) if items else {}
                    for key in ("avatar_larger", "avatar_medium", "avatar_thumb"):
                        lst = (au.get(key) or {}).get("url_list") or []
                        if lst and isinstance(lst[0], str) and lst[0].startswith("http"):
                            return lst[0]
                except (AttributeError, IndexError, TypeError) as _exc:
                    self._warn_api_changed("post/item_list", tr('the author fields in the response are structured differently'))
        return None

    def task_fetch_avatar(self, acc_id):
        """Аватар профиля для кружка-рамки (запускается в потоке из UI):
        4 источника URL (см. _avatar_url_of) → avatar.jpg в папке аккаунта.
        Кэшируется навсегда: аватары меняются редко, повторных запросов нет."""
        try:
            acc = db.db_get_account(acc_id)
            adir = db.account_dir(acc_id) if acc else None
            if not acc or not adir:
                self.emit("avatar", (acc_id, None))
                return
            path = os.path.join(adir, "avatar.jpg")
            if os.path.exists(path):
                self.emit("avatar", (acc_id, path))
                return
            url = self._avatar_url_of(acc["nickname"], acc.get("sec_uid"))
            ok = self._download_image(url, path) if url else False
            if ok:
                self.emit("avatar", (acc_id, path))
            else:
                self.log(tr('Avatar of @{} was not downloaded yet: TikTok did not return the profile data. It will be retried automatically once the bypass is running.').format(acc["nickname"]),
                         LEVEL_WARN)
                self.emit("avatar", (acc_id, None))
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.emit("avatar", (acc_id, None))

    def _secuid_via_embed(self, post_url):
        """Постоянный ID автора со страницы embed поста (embed/v2/<id>).
        Работает даже когда обычная страница профиля отдаётся урезанной."""
        m = VIDEO_ID_RE.search(post_url or "")
        if not m:
            return None
        sec = find_sec_uid_in_text(self._fetch_url(
            f"https://www.tiktok.com/embed/v2/{m.group(0)}"))
        if sec:
            self.log(tr('Profile ID found via the post embed page.'), LEVEL_OK)
        return sec

    def _resolve_sec_uid(self, user_name):
        """Добыча постоянного ID профиля несколькими путями:
        страница профиля → API TikTok → embed-страница → ID первого поста."""
        self.log(tr('Resolving the permanent profile ID…'))
        sources = (
            (tr('profile page'), f"https://www.tiktok.com/@{user_name}"),
            (tr('profile API'), f"https://www.tiktok.com/api/user/detail/?uniqueId={user_name}&aid=1988"),
            (tr('embed page'), f"https://www.tiktok.com/embed/@{user_name}"),
        )
        private = False
        gated = False
        for name, url in sources:
            html = self._fetch_url(url)
            if not html:
                continue
            if looks_private(html):
                private = True
            if looks_login_gated(html):
                gated = True
            sec = find_sec_uid_in_text(html)
            if sec:
                self.log(tr('Profile ID found ({0}).', name), LEVEL_OK)
                return sec
            if name == tr('profile API') and not (looks_private(html) or looks_login_gated(html)):
                self._check_api_shape("user/detail", html, "userInfo")
        if private:
            self.log(tr('The account is PRIVATE: the post list is visible to followers only. Choose a browser with a TikTok login in the AUTHORIZATION section on the left and sync again.'), LEVEL_WARN)
        elif gated:
            self.log(tr('TikTok requires a login for this profile (“Log in to TikTok” — happens on public accounts too). Log in to TikTok in your browser, choose it in the AUTHORIZATION section on the left and sync again.'), LEVEL_WARN)
        # последний шанс: со страницы embed берём ID любого поста и по нему вычисляем профиль
        html = self._fetch_url(f"https://www.tiktok.com/embed/@{user_name}")
        if html:
            video_ids = []
            for pat in (r'"id":"(\d{10,25})"', r'"awemeId":"(\d{10,25})"'):
                video_ids += re.findall(pat, html)
            for vid in list(dict.fromkeys(video_ids))[:3]:
                sec = self._channel_id_of_video(
                    f"https://www.tiktok.com/@{user_name}/video/{vid}")
                if sec:
                    self.log(tr('Profile ID found via the post.'), LEVEL_OK)
                    return sec
        return None

    def _profile_meta(self, user_name):
        """(sec_uid, текущий ник) со страницы профиля — одним вызовом yt-dlp."""
        url = f"https://www.tiktok.com/@{user_name}"
        cmd = [YT_DLP_EXE, validate.safe_arg(url), "--flat-playlist", "--playlist-end", "1",
               "--print", "%(channel_id)s\t%(uploader)s"] + self._base_args()
        try:
            r = self._run(cmd, text=True, encoding="utf-8",
                               errors="ignore", timeout=180, creationflags=paths.CREATE_NO_WINDOW)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            return None, None
        for line in (r.stdout or "").splitlines():
            sec, _, up = line.strip().partition("\t")
            if SEC_UID_RE.fullmatch(sec):
                return sec, (up if up and up != "NA" else None)
        return None, None

    def _nickname_from_sec_uid(self, sec_uid):
        """Ник по постоянному ID: берём автора первого поста из списка."""
        cmd = [YT_DLP_EXE, validate.safe_arg(f"tiktokuser:{sec_uid}"), "--flat-playlist", "--playlist-end", "1",
               "--print", "%(uploader)s"] + self._base_args()
        try:
            r = self._run(cmd, text=True, encoding="utf-8",
                               errors="ignore", timeout=300, creationflags=paths.CREATE_NO_WINDOW)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            return None
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line and line != "NA":
                return line
        return None

    def _ensure_sec_uid(self, acc_id, nickname, sec_uid):
        if sec_uid:
            return sec_uid
        sec, _up = self._profile_meta(nickname)
        if not sec:
            sec = self._resolve_sec_uid(nickname)
        if not sec:
            sec = self._secuid_via_gallerydl(nickname)
        if sec:
            if acc_id is not None:
                db.db_update_sec_uid(acc_id, sec)
                # чип «нет ID» на карточке должен исчезнуть
                self.emit("accounts", None)
            self.log(tr('Permanent profile ID saved for “{0}”.', nickname))
        else:
            self.log(tr('Could not get the permanent ID of “{0}”. Check the nickname — the account may not exist or be unavailable. If you have a link to any POST of this account, add that instead: the ID is resolved more reliably from a post.', nickname),
                     LEVEL_WARN)
        return sec

    def _secuid_via_gallerydl(self, nickname):
        """Запасной путь по одному нику (в том числе закрытые аккаунты с cookies):
        gallery-dl сам решает защитную страницу TikTok и читает профиль. Адрес профиля он
        лишь раскрывает в /avatar и /posts, поэтому обращаемся к ним напрямую и берём
        первый пост (--range 1) в виде JSON — в нём есть secUid автора."""
        if not self.gdl_cmd or not validate.NICK_RE.match(nickname or ""):
            return None
        urls = [f"https://www.tiktok.com/@{nickname}/posts",
                f"https://www.tiktok.com/@{nickname}/avatar"]
        modes = [True, False] if self._proxy_alive() else [False]
        why = None
        for rnd in range(3):
            for use_proxy in modes:
                for url in urls:
                    cmd = self.gdl_cmd + ["--range", "1", "-j"]
                    if use_proxy:
                        cmd += ["--proxy", self.proxy_url]
                    if self.cookies_file:
                        cmd += ["--cookies", self.cookies_file]
                    elif self.cookies:
                        cmd += ["--cookies-from-browser", self.cookies]
                    cmd.append(validate.safe_arg(url))
                    try:
                        r = self._run(cmd, text=True, encoding="utf-8", errors="ignore",
                                      timeout=150, creationflags=paths.CREATE_NO_WINDOW)
                    except Exception as _exc:
                        _LOG.debug('gallery-dl secuid: %r', _exc, exc_info=True)
                        continue
                    m = SEC_UID_RE.search(r.stdout or "")
                    if m:
                        return m.group(0)
                    err = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
                    out = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
                    why = (err[-1] if err else (out[-1] if out else
                           tr('empty output, exit code {0}', r.returncode)))
            if self._is_cancelled_any():
                break
            time.sleep(2)
        if why:
            self.log(tr('gallery-dl could not read the profile: {0}', why[:300]), LEVEL_DIM)
        return None

    def _enumerate_videos(self, url, limit, acc_id=None):
        """Список постов профиля (yt-dlp, flat-playlist).
        Вывод читается ПО СТРОКЕ и на каждой строке проверяется «Стоп»:
        раньше процесс листинга запускался «навсегда» (subprocess.run) и
        отмена срабатывала только после полного перечисления профиля —
        теперь kill приходит в ту же секунду.
        Возвращает (rows, текущий_хэндл_автора, ошибка): rows = [(id, дата, титул)…],
        ошибка — текст сбоя yt-dlp, если список пуст и он ругался."""
        # uploader — первым полем: у заголовков бывают табуляции, у него нет
        cmd = [YT_DLP_EXE, validate.safe_arg(url), "--flat-playlist",
               "--print", "%(uploader)s\t%(id)s\t%(upload_date)s\t%(title)s"] \
              + self._base_args()
        if limit and limit > 0:
            cmd += ["--playlist-end", str(limit)]
        try:
            proc = self._popen(cmd, max_seconds=3600, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="ignore", bufsize=1,
                                    creationflags=paths.CREATE_NO_WINDOW)
        except Exception as e:
            self.log(tr('Could not get the post list: {0}', e), LEVEL_ERROR)
            return [], None, str(e)
        rows, current_handle = [], None
        tail = []            # последние строки вывода — источник текста ошибки
        cancelled = False
        while True:
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 6:
                del tail[:-6]
            parsed = parse_enum_line(line)
            if parsed:
                uploader, vid, upload_date, title = parsed
                rows.append((vid, upload_date, title))
                if uploader and not current_handle:
                    current_handle = uploader
            # Флаг отмены здесь только ЧИТАЕТСЯ: снять его должен вызывающий
            # через _is_cancelled, иначе отмена «потеряется» между этапами.
            if acc_id is not None and acc_id in self._cancel_req:
                cancelled = True
                break
        if cancelled:
            try:
                proc.kill()
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass
            self.log(tr('Post list: stopped with the Stop button.'), LEVEL_WARN)
        try:
            proc.wait(timeout=15)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
        err = None
        if not rows and not cancelled and proc.returncode not in (0, None) and tail:
            err = tail[-1][:300]
            self.log("yt-dlp: " + err, LEVEL_ERROR)
            low = err.lower()
            if any(k in low for k in ("login", "log in", "private", "captcha", "503")):
                self.log(tr('TikTok seems to require a login (private account or audience restriction): log in to TikTok in your browser and choose it in the AUTHORIZATION section on the left, then press “Sync” on the card.'), LEVEL_WARN)
            elif "user id" in low or "secondary" in low:
                self.log(tr('TikTok did not return the list by nickname. Options: add the account by a link to any of its posts (the ID is resolved automatically), try again later, or enable authorization.'), LEVEL_WARN)
        return rows, current_handle, err

    def _gallerydl_download(self, url, dest_dir):
        """Скачивает пост (фото-карусель) через gallery-dl. Возвращает (успех, путь первого файла).
        TikTok нередко отвечает 403 на запросы через прокси ByeDPI, поэтому при неудаче
        повторяем напрямую; если прямой путь сработал — дальше пробуем его первым."""
        if not self.gdl_cmd:
            return False, None
        os.makedirs(dest_dir, exist_ok=True)
        modes = [False] if not self._proxy_alive() else (
            [False, True] if getattr(self, "_gdl_prefer_direct", False) else [True, False])
        # gallery-dl сам при 403 ничего не повторяет, а заголовки у него в каждом процессе
        # случайные: часть запусков TikTok пропускает, часть нет — поэтому несколько попыток
        for rnd in range(self.GDL_ROUNDS):
            for use_proxy in modes:
                ok, first = self._gallerydl_once(url, dest_dir, use_proxy)
                vid = url.rstrip("/").rpartition("/")[2].split("?")[0]
                if ok:
                    bad = postmedia.bad_photo_files(dest_dir, vid) if vid.isdigit() else []
                    if bad:
                        # битая/пустая картинка (обрыв, заглушка вместо файла) — не успех
                        for f in bad:
                            self.log(tr('    Broken picture removed: {0}', os.path.basename(f)[:80]), LEVEL_WARN)
                            try:
                                os.remove(f)
                            except OSError as _exc:
                                _LOG.debug('remove bad picture: %r', _exc)
                        ok = False
                if ok and vid.isdigit():
                    # все ли картинки на месте: TikTok сообщает число в «count» (json рядом)
                    root = os.path.dirname(dest_dir) if os.path.basename(dest_dir) in postmedia.SORT_DIRS else dest_dir
                    want = postmedia.expected_count(vid, dest_dir, os.path.join(root, postmedia.SUB_INFO))
                    have = len(postmedia.photo_files(dest_dir, vid))
                    if want and have < want:
                        self.log(tr('    Only {0} of {1} pictures downloaded — retrying the missing ones.', have, want), LEVEL_WARN)
                        ok = False
                if ok:
                    if modes[0] is True and not use_proxy:
                        self._gdl_prefer_direct = True
                    if self.sort_by_type:
                        _vid = url.rstrip("/").rpartition("/")[2].split("?")[0]
                        _root = os.path.dirname(dest_dir) if os.path.basename(dest_dir) in postmedia.SORT_DIRS else dest_dir
                        postmedia.move_meta(dest_dir, _vid, os.path.join(_root, postmedia.SUB_INFO))
                    try:
                        vid = url.rstrip("/").rpartition("/")[2].split("?")[0]
                        ren = postmedia.fix_extensions(dest_dir, vid) if vid.isdigit() else {}
                        if ren:
                            self.log(tr('    Picture extensions corrected to the real format: {0}', len(ren)), LEVEL_DIM)
                            if first:
                                for old_p, new_p in ren.items():
                                    if os.path.normcase(old_p) == os.path.normcase(first):
                                        first = new_p
                    except Exception as _exc:
                        _LOG.debug('fix extensions: %r', _exc, exc_info=True)
                    return ok, first
                if self._is_cancelled_any():
                    return False, None
            if rnd < self.GDL_ROUNDS - 1:
                if rnd == 0:
                    self.log(tr('    TikTok refused — waiting a few seconds and trying once more…'), LEVEL_DIM)
                time.sleep(3)
                self._wait_throttle()
        return False, None

    GDL_ROUNDS = 4

    def _is_cancelled_any(self):
        return bool(getattr(self, "_stop_all", False))

    def _gallerydl_once(self, url, dest_dir, use_proxy):
        # -D: файлы кладутся прямо в папку аккаунта, без лишних подпапок
        cmd = self.gdl_cmd + ["-D", dest_dir]
        if use_proxy:
            cmd += ["--proxy", self.proxy_url]
        if self.cookies_file:
            cmd += ["--cookies", self.cookies_file]
        elif self.cookies:
            cmd += ["--cookies-from-browser", self.cookies]
        # --write-metadata: рядом с картинками лежит JSON с описанием поста
        cmd += ["--write-metadata"]
        cmd.append(validate.safe_arg(url))
        first_path = None
        try:
            self.log("    gallery-dl: " + " ".join(cmd[len(self.gdl_cmd):]), LEVEL_DIM)
            p = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="ignore", bufsize=1,
                                 creationflags=paths.CREATE_NO_WINDOW)
            while True:
                out = p.stdout.readline()
                if out == "" and p.poll() is not None:
                    break
                if out:
                    line = out.strip()
                    if not line:
                        continue
                    if first_path is None and re.search(r"\.(jpe?g|png|webp|gif|mp4|m4a)\b", line, re.I):
                        first_path = line
                    if "ERROR" in line.upper() or first_path == line:
                        self.log("    [gallery-dl] " + line[:300])
                    elif "WARNING" in line.upper():
                        self.log("    [gallery-dl] " + line[:300], LEVEL_DIM)
            return p.poll() == 0, first_path
        except Exception as e:
            self.log(tr('    [gallery-dl] failure: {0}', e), LEVEL_WARN)
            return False, None

    def _grab_description(self, media_dir, video_id):
        """Описание поста из сайдкар-файлов рядом с медиа:
        «…ID.description» (пишет yt-dlp с --write-description) или
        «…ID.json» (пишет gallery-dl с --write-metadata — фото-посты).
        Текст описания или None. Описание обрезается до 6000 знаков."""
        names = postmedia.list_names(media_dir)
        for name in names:
            low = name.lower()
            if video_id not in name:
                continue
            path = os.path.join(media_dir, name)
            try:
                if low.endswith(".description"):
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        text = f.read().strip()
                    return text[:6000] or None
                if low.endswith(".json"):
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        text = str(data.get("description")
                                   or data.get("desc") or "").strip()
                        return text[:6000] or None
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                continue
        return None

    def download_one(self, acc, video_id, has_ffmpeg):
        """Качает один пост по ID в папку media/ этого аккаунта.
        Возвращает (успех, путь, тип 'video'|'photo'). При успехе вычитывает
        описание поста из сайдкар-файла и сохраняет в базу."""
        ok, path, kind = self._download_one_impl(acc, video_id, has_ffmpeg)
        if ok and acc.get("id") is not None:
            try:
                media_dir = db.account_media_dir(acc["id"]) or paths.OUTPUT_DIR
                desc = self._grab_description(media_dir, video_id)
                if desc:
                    db.db_set_description(acc["id"], video_id, desc)
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass  # описание — приятный бонус, сбои не важны
        return ok, path, kind

    VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")

    @staticmethod
    def _file_ok(path):
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except OSError:
            return False

    def _sort_into(self, media_dir, video_id, sub, *paths_):
        """Переносит файлы поста в подпапку; возвращает пути с учётом переноса."""
        moved = postmedia.relocate(media_dir, video_id, sub)
        out = []
        for p in paths_:
            out.append(next((n for o, n in moved.items()
                             if p and os.path.normcase(o) == os.path.normcase(p)), p))
        return out

    def _existing_media(self, media_dir, video_id):
        """Готовый видеофайл поста в media/ (имя содержит ID, не .part): путь или None."""
        for name in postmedia.list_names(media_dir):
            if video_id in name and name.lower().endswith(self.VIDEO_EXTS):
                path = os.path.join(media_dir, name)
                if self._file_ok(path):
                    return path
        return None

    def _run_ytdlp_download(self, cmd):
        """Один запуск yt-dlp. Возвращает (код возврата, путь к файлу или None)."""
        file_path = None
        # shell=False обязателен: со shell=True пути с пробелами/кириллицей ломаются
        p = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="ignore", bufsize=1,
                             creationflags=paths.CREATE_NO_WINDOW)
        while True:
            out = p.stdout.readline()
            if out == "" and p.poll() is not None:
                break
            if out:
                line = out.strip()
                m = (re.search(r"\[download\] Destination: (.+)", line)
                     or re.search(r'\[Merger\] Merging formats into "(.+)"', line)
                     or re.search(r"\[download\] (.+) has already been downloaded", line))
                if m:
                    file_path = m.group(1).strip()
                if "ERROR" in line:
                    self.log("    " + line[:300], LEVEL_ERROR)
                    low = line.lower()
                    if ("429" in low or "too many requests" in low or "rate limit" in low
                            or "no video formats found" in low):
                        self._note_rate_limit()
        return p.poll(), file_path

    def _download_one_impl(self, acc, video_id, has_ffmpeg):
        """Само скачивание (без обработки описания)."""
        nickname = acc["nickname"]
        self._wait_throttle()
        media_dir = db.account_media_dir(acc["id"]) or paths.OUTPUT_DIR
        output_template = os.path.join(media_dir, postmedia.SUB_VIDEO if self.sort_by_type else "",
                                       "%(upload_date)s_%(id)s.%(ext)s")
        url = f"https://www.tiktok.com/@{nickname}/video/{video_id}"
        fmt = ["-f", "bv*+ba/b", "--merge-output-format", "mp4"] if has_ffmpeg else ["-f", "b"]
        if self.sort_by_type:
            # описание и обложка — в Info/, чтобы не мешали видео
            info_t = os.path.join(media_dir, postmedia.SUB_INFO, "%(upload_date)s_%(id)s.%(ext)s")
            fmt = fmt + ["-o", "description:" + info_t, "-o", "thumbnail:" + info_t]
        # --write-description: рядом с видео лежит текстовый файл с описанием;
        # --write-thumbnail: обложка поста — превью для окна «Посты аккаунта»
        def build(use_proxy):
            return [YT_DLP_EXE, validate.safe_arg(url), "-o", output_template] + fmt \
                + self._base_args(use_proxy=use_proxy) \
                + ["--newline", "--write-description", "--write-thumbnail",
                   "--continue", "--fragment-retries", "5"]
        # Через прокси ByeDPI TikTok местами отвечает 403 — вторая попытка идёт напрямую
        # (и, если прямой путь выручил, дальше пробуем его первым).
        if not self._proxy_alive():
            modes = [False, False]
        elif getattr(self, "_ytdlp_direct_first", False):
            modes = [False, True]
        else:
            modes = [True, False]
        # Уже скачанный файл на диске (например, потерялась запись в базе): сеть не нужна
        existing = self._existing_media(media_dir, video_id)
        if existing:
            self.log(tr('    The file is already on disk — not downloading again.'), LEVEL_INFO)
            return True, existing, "video"
        file_path = None
        code = 1
        # Две попытки: после сбоя сети yt-dlp ДОКАЧИВАЕТ оборванный .part, а не начинает заново
        for attempt in (1, 2):
            try:
                code, found = self._run_ytdlp_download(build(modes[attempt - 1]))
            except Exception as e:
                _LOG.exception(tr('yt-dlp crash'))
                self.log(tr('    Crash: {0}', e), LEVEL_ERROR)
                return False, None, "video"
            file_path = found or file_path
            if code == 0:
                if attempt == 2 and modes[0] is True and modes[1] is False:
                    self._ytdlp_direct_first = True
                break
            if attempt == 1:
                self.log(tr('    Download failed — retrying (an interrupted file will resume).'), LEVEL_WARN)
                time.sleep(2)
        if code == 0 and file_path and not netutil.is_within(media_dir, file_path):
            self.log(tr('    File path is outside the account folder — download rejected.'), LEVEL_ERROR)
            _LOG.warning('file outside media: %s', file_path)
            return False, None, "video"
        # Итоговая проверка: yt-dlp сказал «успех», но файла нет или он пустой
        if code == 0 and file_path and not self._file_ok(file_path):
            self.log(tr('    yt-dlp reported success but the file is missing or empty — treating the download as failed.'), LEVEL_ERROR)
            code = 1
        ok = code == 0
        if ok:
            self._note_ok()

        # Фото-пост (карусель): yt-dlp умеет только музыку от него
        ext = os.path.splitext(file_path or "")[1].lower()
        if ok and ext in AUDIO_MEDIA_EXTS:
            self.log(tr('    This is a photo post: yt-dlp downloaded only the music. Downloading the pictures (gallery-dl)…'), LEVEL_WARN)
            gdir = os.path.join(media_dir, postmedia.SUB_PHOTOS) if self.sort_by_type else media_dir
            if self.sort_by_type:
                file_path = self._sort_into(media_dir, video_id, postmedia.SUB_PHOTOS, file_path)[0]
            gok, gpath = self._gallerydl_download(url, gdir)
            if gok:
                return True, (gpath or file_path), "photo"
            self.log(tr('    Pictures were not downloaded (music saved). Check the gallery-dl installation.'), LEVEL_WARN)
            return True, file_path, "photo"
        if not ok and self.gdl_cmd:
            self.log(tr('    yt-dlp failed — trying gallery-dl…'), LEVEL_WARN)
            gok, gpath = self._gallerydl_download(url, media_dir)
            if gok:
                is_vid = bool(gpath) and gpath.lower().endswith(postmedia.VIDEO_EXTS)
                if self.sort_by_type:
                    gpath = self._sort_into(media_dir, video_id,
                                            postmedia.SUB_VIDEO if is_vid else postmedia.SUB_PHOTOS,
                                            gpath)[0]
                return True, gpath, ("video" if is_vid else "photo")
        return ok, file_path, "video"

    # =============================== ЗАДАЧИ ===============================
    # Каждая задача выполняется в отдельном потоке и гасит все исключения сама.

    def task_add_account(self, target, rng):
        """Добавить аккаунт (по нику, ссылке или tiktokuser:ID) и сразу скачать
        с выбранным диапазоном — как в 4K Tokkit."""
        self._busy_enter()
        acc_id = None
        try:
            try:
                t = validate.parse_target(target)
            except ValueError as e:
                self.log(str(e), LEVEL_ERROR)
                self.emit("toast", ("error", str(e)))
                return
            # Аккаунт попадает в базу и на экран СРАЗУ, если ник известен без сети.
            # ID профиля (сетевой запрос на секунды) добывается уже после добавления.
            sec_uid, nickname = None, None
            if t.kind == "secuid":
                sec_uid = t.sec_uid
            elif t.kind == "post":
                nickname = t.nickname
                if not nickname:
                    self.log(tr('Detecting the author from the post link…'))
                    sec_uid = self._channel_id_of_video(t.url)
                    nickname = (self._nickname_from_sec_uid(sec_uid)
                                if sec_uid else None) or self._oembed_nick(t.url)
                    if nickname and not validate.NICK_RE.match(nickname):
                        nickname = None
            else:                                   # "profile" или "nick"
                nickname = t.nickname

            if not nickname and sec_uid:
                nickname = self._nickname_from_sec_uid(sec_uid)
            if not nickname:
                self.log(tr('Could not determine the nickname. Enter a nickname like ecoveranew or a profile/post link.'), LEVEL_ERROR)
                return

            acc_id, created = db.db_add_account(nickname, sec_uid,
                                                rng.get("limit_count"),
                                                rng.get("date_from"), rng.get("date_to"))
            if created:
                db.db_set_range_kind(acc_id, rng.get("kind") or "all")
                folder_rel = os.path.relpath(db.account_dir(acc_id), paths.APP_DIR)
                self.log(tr('Account “{0}” added to the database. Account folder: {1}', nickname, folder_rel), LEVEL_OK)
                self.emit("toast", ("ok", tr('Account @{0} added to the database', nickname)))
            else:
                self.log(tr('Account “{0}” was already in the database — starting a check.', nickname), LEVEL_INFO)
                if rng.get("limit_count") or rng.get("date_from") or rng.get("date_to"):
                    self.log(tr('The chosen range applies only on the FIRST add. To download old posts: right-click the card → “Download whole profile”.'),
                             LEVEL_WARN)
            self.emit("accounts", None)            # карточка появляется немедленно
            if not sec_uid:
                if t.kind == "post":
                    sec_uid = self._channel_id_of_video(t.url)
                    if sec_uid:
                        db.db_update_sec_uid(acc_id, sec_uid)
                        self.emit("accounts", None)
                if not sec_uid:
                    sec_uid = self._ensure_sec_uid(acc_id, nickname, None)
                if not sec_uid:
                    self.log(tr('Profile ID not available right now — the account is saved, the ID will be fetched on sync.'),
                             LEVEL_WARN)
            self._sync_account(db.db_get_account(acc_id))
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.log_exc(tr('Could not add the account:'))
        finally:
            if acc_id is not None:
                self._cancel_req.discard(acc_id)   # висячих отмен не оставляем (чужие не трогаем)
            self._busy_exit()

    def task_sync_one(self, acc_id):
        self._busy_enter()
        try:
            acc = db.db_get_account(acc_id)
            if not acc:
                return
            if acc["paused"]:
                self.log(tr('“{0}” is paused — press “Resume” on the card first.', acc['nickname']),
                         LEVEL_WARN)
                self.emit("toast", ("info", tr('@{0}: account is paused', acc['nickname'])))
                return
            self._sync_account(acc)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.log_exc(tr('Sync interrupted:'))
            self.emit("card", (acc_id, None))   # не оставлять «Составляю список…» навсегда
            self.emit("progress", (acc_id, None))
            self.emit("toast", ("error", tr('Sync interrupted — see the log')))
        finally:
            self._cancel_req.discard(acc_id)
            self._busy_exit()

    def task_sync_all(self, auto=False):
        """Проверка новых постов и докачка очереди у всех аккаунтов подряд.
        auto=True — запуск по расписанию. В конце — сводка: сколько скачано."""
        self._busy_enter()
        accounts = []
        self._sync_run = {"t0": time.time(), "ok": 0, "fail": 0, "bytes": 0, "new": 0,
                     "accounts": 0, "per": {}, "lock": threading.Lock()}
        try:
            accounts = db.db_accounts()
            self.log(tr('Scheduled sync started: accounts {0}', len(accounts)) if auto
                     else tr('Syncing all accounts: {0}', len(accounts)), LEVEL_HEAD)
            fails_in_a_row = 0
            self._stop_all = False
            for acc in accounts:
                if self._stop_all:
                    self.log(tr('Bulk sync stopped by the user.'), LEVEL_WARN)
                    break
                fresh = db.db_get_account(acc["id"])
                if not fresh:
                    continue
                if fresh["paused"]:
                    self.log(tr('“{0}” is paused — skipping.', fresh['nickname']))
                    continue
                total_before = db.db_counts(fresh["id"])[0]
                try:
                    ok = self._sync_account(fresh)
                except Exception as _exc:
                    _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                    # сбой одного аккаунта не должен отменять проверку остальных
                    self.log_exc(tr('“{0}”: account skipped due to a failure:', fresh['nickname']))
                    ok = False
                    self.emit("card", (fresh["id"], None))
                self._sync_run["accounts"] += 1
                new_posts = max(0, db.db_counts(fresh["id"])[0] - total_before)
                self._sync_run["new"] += new_posts
                self._sync_run["per"].setdefault(fresh["nickname"], [0, 0, 0])[0] += new_posts
                if ok:
                    fails_in_a_row = 0
                else:
                    fails_in_a_row += 1
                    if fails_in_a_row >= 3:
                        self.log(tr('3 accounts in a row failed — looks like a network problem. Stopping.'),
                                 LEVEL_WARN)
                        break
            self.log(tr('Sync of all accounts finished.'), LEVEL_HEAD)
            self._run_report(auto)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.log_exc(tr('Bulk sync interrupted:'))
        finally:
            self._stop_all = False
            self._sync_run = None
            for acc in accounts:
                self._cancel_req.discard(acc["id"])   # отмены других задач не стираем
            self._busy_exit()

    def _single_via_gallerydl(self, target, dest_dir, reason=""):
        """Разовое скачивание через gallery-dl (фото-посты/карусели и случаи,
        когда yt-dlp не справился). Возвращает True при успехе."""
        if not self.gdl_cmd:
            self.gdl_cmd = self._ensure_gallery_dl()
        if not self.gdl_cmd:
            self.log(tr('gallery-dl is not installed — a photo post cannot be downloaded. Start the app again with internet access.'), LEVEL_ERROR)
            self.emit("toast", ("error", tr('One-off download failed — see the log')))
            return False
        if reason:
            self.log(reason, LEVEL_WARN)
        self.log(tr('Downloading via gallery-dl (it understands photo posts)…'), LEVEL_DL)
        ok, _path = self._gallerydl_download(target, dest_dir)
        if ok:
            self.log(tr('Saved to folder: {0}', dest_dir), LEVEL_OK)
            self.emit("toast", ("ok", tr('Saved to Downloads')))
            return True
        self.log(tr('gallery-dl could not download this post either — see the [gallery-dl] lines above.'),
                 LEVEL_ERROR)
        self.emit("toast", ("error", tr('One-off download failed — see the log')))
        return False

    def task_single_video(self, target):
        """Разовое скачивание по ссылке, без базы: видео — yt-dlp,
        фото-посты (карусели, /photo/) — сразу gallery-dl, потому что
        yt-dlp от них падает с кодом 1 (умеет только музыку)."""
        self._busy_enter()
        try:
            try:
                t = validate.parse_target(target)
                if t.kind != "post":
                    raise ValueError(tr('Expected a post link like https://www.tiktok.com/@user/video/123…'))
            except ValueError as e:
                self.log(str(e), LEVEL_ERROR)
                self.emit("toast", ("error", str(e)))
                return
            target = t.url
            self.log(tr('Downloading: {0}', target), LEVEL_HEAD)

            # фото-пост: ссылка вида /photo/… — yt-dlp даже не пробуем
            if "/photo/" in target:
                self._single_via_gallerydl(target, paths.OUTPUT_DIR,
                                           tr('This is a photo post (carousel) — yt-dlp cannot handle it.'))
                return

            has_ffmpeg = bool(shutil.which("ffmpeg"))
            if not has_ffmpeg:
                self.log(tr('ffmpeg not found — downloading a single combined file (best of the ready-made ones).'), LEVEL_WARN)
            output_template = os.path.join(paths.OUTPUT_DIR, "%(uploader)s",
                                           "%(upload_date)s_%(id)s.%(ext)s")
            fmt = ["-f", "bv*+ba/b", "--merge-output-format", "mp4"] if has_ffmpeg else ["-f", "b"]
            cmd = [YT_DLP_EXE, validate.safe_arg(target), "-o", output_template] + fmt + self._base_args() \
                + ["--newline", "--write-description", "--write-thumbnail"]
            try:
                p = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="ignore", bufsize=1,
                                     creationflags=paths.CREATE_NO_WINDOW)
            except Exception as e:
                self._single_via_gallerydl(target, paths.OUTPUT_DIR,
                                           tr('yt-dlp failed to start: {0}.', e))
                return
            last_file = None
            while True:
                out = p.stdout.readline()
                if out == "" and p.poll() is not None:
                    break
                if out:
                    line = out.strip()
                    m = (re.search(r"\[download\] Destination: (.+)", line)
                         or re.search(r'\[Merger\] Merging formats into "(.+)"', line)
                         or re.search(r"\[download\] (.+) has already been downloaded", line))
                    if m:
                        last_file = m.group(1).strip()
                    if any(x in line for x in ["[download]", "[TikTok]", "[info]", "ERROR"]):
                        self.log(line[:300])
            code = p.poll()
            if code == 0:
                # карусель бывает и по ссылке /video/: yt-dlp качает от неё только
                # музыку — дополучаем картинки через gallery-dl (как в базе)
                ext = os.path.splitext(last_file or "")[1].lower()
                if ext in AUDIO_MEDIA_EXTS:
                    self._single_via_gallerydl(target, paths.OUTPUT_DIR,
                                               tr('This is a photo post: yt-dlp downloaded only the music, downloading the pictures.'))
                    return
                self.log(tr('Saved to folder: {0}', paths.OUTPUT_DIR), LEVEL_OK)
                self.emit("toast", ("ok", tr('Saved to Downloads')))
            else:
                self.log(tr('yt-dlp exited with code {0}.', code), LEVEL_ERROR)
                self._single_via_gallerydl(target, paths.OUTPUT_DIR,
                                           tr('It may be a photo post, or TikTok changed the page.'))
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.log_exc(tr('One-off download error:'))
            self.emit("toast", ("error", tr('One-off download failed — see the log')))
        finally:
            self._busy_exit()

    # ------------------------------ синхронизация ------------------------------
    def _is_paused(self, acc_id):
        """Свежая пауза из базы: юзер жмёт кнопку в любой момент, даже посреди задачи."""
        a = db.db_get_account(acc_id)
        return bool(a and a["paused"])

    def request_cancel(self, acc_id):
        """Кнопка «Стоп» на карточке: остановить скачивание после текущего
        поста и ОЧИСТИТЬ очередь (посты пометятся пропущенными — их можно
        вернуть через «Скачать пропущенное»). Отличие от «Пауза»: пауза
        сохраняет очередь до «Пуска», отмена — убирает её совсем."""
        self._cancel_req.add(acc_id)

    # ---------------- итоги массовой синхронизации ----------------
    @staticmethod
    def fmt_size(n):
        n = float(n)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024

    @staticmethod
    def fmt_duration(sec):
        sec = int(sec)
        h, rem = divmod(sec, 3600)
        m, s_ = divmod(rem, 60)
        if h:
            return tr('{0} h {1} min', h, m)
        if m:
            return tr('{0} min {1} s', m, s_)
        return tr('{0} s', s_)

    def _run_note_file(self, nickname, acc_id, vid, path, kind):
        """Скачанный пост попадает в итог: счётчик и объём файлов."""
        r = self._sync_run
        if r is None:
            return
        size = 0
        try:
            if kind == "photo" and path:
                for f in postmedia.photo_files(os.path.dirname(path), vid) or [path]:
                    size += os.path.getsize(f)
            elif path:
                size = os.path.getsize(path)
        except OSError:
            pass
        with r["lock"]:
            r["bytes"] += size
            r["ok"] += 1
            per = r["per"].setdefault(nickname, [0, 0, 0])
            per[1] += 1

    def _run_note_fail(self, nickname):
        r = self._sync_run
        if r is None:
            return
        with r["lock"]:
            r["fail"] += 1
            r["per"].setdefault(nickname, [0, 0, 0])[2] += 1

    def _run_report(self, auto=False):
        """Итог после «Синхронизировать всех»: в журнал и всплывашкой."""
        r, self._sync_run = self._sync_run, None
        if not r:
            return
        took = self.fmt_duration(time.time() - r["t0"])
        head = tr('Scheduled sync summary', ) if auto else tr('Sync summary')
        self.log(tr('{0}: accounts {1} · new posts {2} · downloaded {3} · failed {4} · {5} · {6}',
                    head, r["accounts"], r["new"], r["ok"], r["fail"],
                    self.fmt_size(r["bytes"]), took), LEVEL_SUM)
        for nick, (new, ok, fail) in sorted(r["per"].items()):
            if new or ok or fail:
                self.log(tr('    @{0}: {1} new, {2} downloaded, {3} failed', nick, new, ok, fail),
                         LEVEL_DIM)
        self.emit("toast", ("warn" if r["fail"] else "ok",
                            tr('Sync finished: downloaded {0} ({1}), failed {2}, new posts {3}',
                               r["ok"], self.fmt_size(r["bytes"]), r["fail"], r["new"])))

    def request_cancel_all(self):
        """«Остановить всех»: стоп для каждого аккаунта + прерывание массовой
        синхронизации (очередные аккаунты даже не начинаются)."""
        self._stop_all = True
        for acc in db.db_accounts():
            self._cancel_req.add(acc["id"])

    def _is_cancelled(self, acc_id):
        """Разовый флаг отмены: сработал — снялся (не повисает на будущие задачи).
        Хвостовые проверки в finally задач не дают флагу застрять, если юзер
        нажал «Стоп» в момент, когда качать уже было нечего."""
        if acc_id in self._cancel_req:
            self._cancel_req.discard(acc_id)
            return True
        return False

    def _verify_media_files(self, acc_id, include_unknown=False, requeue=True):
        """Сверка базы с файлами в media/. Возвращает (починено_путей,
        вернулось_в_очередь, файлов_нет).
        • файл есть по сохранённому пути — ок;
        • путь сохранился криво, но файл с ID поста лежит в media/ — чиним путь;
        • файла нет вовсе — пост возвращается в очередь ТОЛЬКО по явному
          запросу пользователя (кнопка «Проверить файлы и докачать»,
          requeue=True). Файл могли удалить вручную — перекачивать его без
          просьбы НЕЛЬЗЯ: обычная синхронизация только ПОСЧИТАЕТ пропажи
          и подскажет, где включить перекачку.
        Посты без сохранённого пути (старые фото-посты) трогаем тоже только
        по явной просьбе (include_unknown), иначе можно зациклить перекачку."""
        media = db.account_media_dir(acc_id)
        if not media:
            return 0, 0, 0
        names = postmedia.list_names(media)
        # сайдкары и обложки — НЕ медиа: иначе «чинили» бы путь к превью
        # вместо самого видео (cover.jpg кэша oembed, webp от yt-dlp)
        skip_exts = (".description", ".json", ".part", ".webp", ".cover.jpg")
        healed, requeued = [], []
        missing = 0
        for p in db.db_posts(acc_id):
            if p["downloaded"] != 1:
                continue
            path = p["file_path"]
            if path and os.path.isfile(path):
                continue
            found = None
            for name in names:
                if (p["video_id"] in name and not name.startswith(postmedia.SUB_INFO + "/")
                        and not name.lower().endswith(skip_exts)):
                    cand = os.path.join(media, name)
                    if os.path.isfile(cand):
                        found = cand
                        break
            if found:
                db.db_heal_file_path(acc_id, p["video_id"], found)
                healed.append(p["video_id"])
            elif path or include_unknown:
                missing += 1
                if requeue:
                    requeued.append(p["video_id"])
        n = db.db_requeue_videos(acc_id, requeued) if (requeued and requeue) else 0
        return len(healed), n, missing

    def _sync_account(self, acc):
        """Главная логика: список постов → база → докачка недостающего.
        Возвращает True при успехе (в т.ч. при честной остановке паузой).

        Правила, чтобы не было сюрпризов:
        • первая синхронизация качает выбранный диапазон (последние N / период / весь профиль);
        • повторная качает ТОЛЬКО НОВОЕ: старьё из хвоста помечается пропущенным
          и в очередь не попадает (граница — период, либо граница «последних N»,
          либо просто самая свежая дата скачанного — пиннат её не уронит);
        • посты, возвращённые кнопкой «Скачать пропущенное», обратно не пропускаются:
          они уже были в базе до этой проверки;
        • пауза останавливает скачивание после текущего поста."""
        acc_id, nickname, sec_uid = acc["id"], acc["nickname"], acc["sec_uid"]
        first_sync = acc["last_sync"] is None
        auth_src = tr('cookies.txt file') if self.cookies_file else (self.cookies or None)
        if auth_src:
            self.log(tr('Authorization enabled (cookies: {0}).', auth_src), LEVEL_AUTH)
        if self._is_paused(acc_id):
            self.log(tr('“{0}” is paused — check not started.', nickname), LEVEL_WARN)
            self.emit("card", (acc_id, tr('Paused')))
            self.emit("progress", (acc_id, None))
            return True
        if self._is_cancelled(acc_id):
            # «Стоп» нажали между задачами: не начинаем даже листинг
            self.emit("card", (acc_id, tr('Cancelled')))
            self.emit("progress", (acc_id, None))
            self.log(tr('“{0}”: cancelled before the list was built.', nickname), LEVEL_WARN)
            return True
        self.emit("card", (acc_id, tr('Building the post list…')))
        self.emit("progress", (acc_id, (0, 0)))   # индикатор: «готовлю список…»
        self.log(tr('===== Sync: {0} =====', nickname), LEVEL_HEAD)

        # Папку могли удалить вручную (мы это только приветствуем):
        # пересоздаём — база постов начнётся заново, всё перекачается с нуля.
        if not db.db_folder_exists(acc_id):
            self.log(tr('Account folder not found (deleted manually?) — recreating it, posts will be downloaded again.'), LEVEL_WARN)
        db.db_ensure_folder(acc_id)

        # Файлы на месте? Путь чиним, но НЕ перекачиваем: файл мог быть
        # удалён вручную — качаем пропажи только по явной кнопке
        # («Проверить файлы и докачать» / «Перекачать» в окне постов).
        try:
            healed, _rq, missing = self._verify_media_files(acc_id, requeue=False)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            healed, _rq, missing = 0, 0, 0
        if healed:
            self.log(tr('“{0}”: fixed paths of {1} files (found by ID in media/).', nickname, healed))
        if missing:
            self.log(tr('“{0}”: downloaded files are missing from disk: {1} (deleted manually?). Automatic re-download is off — you can bring them back only manually: right-click the card → “Check files and download”, or in the posts window right-click a tile → “Re-download”.', nickname, missing), LEVEL_INFO)

        sec_uid = self._ensure_sec_uid(acc_id, nickname, sec_uid)
        if sec_uid:
            url = f"tiktokuser:{sec_uid}"
        else:
            url = f"https://www.tiktok.com/@{nickname}"
            self.log(tr('Permanent ID not available — working by nickname.'), LEVEL_WARN)

        # Сколько свежих постов запрашивать:
        #  - первая синхронизация «Последние N» — ровно N (быстрое добавление);
        #  - первая синхронизация «Период»/«Все» — весь список (иначе не отфильтровать);
        #  - повторные проверки — хвост SYNC_TAIL (там только новинки).
        if first_sync and acc["limit_count"] and not (acc["date_from"] or acc["date_to"]):
            limit = max(1, int(acc["limit_count"]))
        else:
            limit = 0 if first_sync else SYNC_TAIL
        if first_sync and limit == 0:
            self.log(tr('Requesting the WHOLE post list — on large accounts this takes a minute or two.'))

        rows, current_handle, enum_err = self._enumerate_videos(url, limit, acc_id=acc_id)

        # TikTok отдаёт текущий хэндл автора: если ник сменился — уведомляем и обновляем базу.
        # Привязка по постоянному sec_uid, поэтому ничего не теряется.
        if current_handle and current_handle.lower() != nickname.lower():
            self.log(tr('Nickname changed: “{0}” → “{1}”. Database record updated (linked by permanent ID).', nickname, current_handle), LEVEL_WARN)
            _ok, folder_after = db.db_rename_account(acc_id, current_handle)
            if folder_after:
                self.log(tr('Account folder: {0} (login in account.txt updated).', os.path.basename(folder_after)))
            nickname = current_handle
            acc["nickname"] = current_handle
            self.emit("accounts", None)

        if not rows:
            if enum_err:
                # честный сбой (не «постов нет»): понятный статус на карточке + подсказки уже в журнале
                low = enum_err.lower()
                if any(k in low for k in ("login", "log in", "private", "captcha")):
                    status = tr('login required')
                elif any(k in low for k in ("unavailable", "not available", "rate limit",
                                            "503", "timeout", "timed out")):
                    status = tr('TikTok did not respond')
                elif "user id" in low or "secondary" in low:
                    status = tr('ID not found')
                else:
                    status = tr('Error — see the log')
                self.emit("card", (acc_id, status))
                self.log(tr('“{0}”: sync failed, the counter was not updated. Fix the cause (above) and press “Sync”.', nickname), LEVEL_ERROR)
                self.emit("progress", (acc_id, None))
                self.emit("toast", ("error", tr('@{0}: sync failed — see the log', nickname)))
                self.emit("stats", acc_id)
                return False
            self.emit("card", (acc_id, tr('No posts')))
            self.log(tr('“{0}”: the account has no posts.', nickname), LEVEL_INFO)
            self.emit("progress", (acc_id, None))
            db.db_touch_sync(acc_id)
            self.emit("stats", acc_id)
            return True
        if not first_sync and limit == SYNC_TAIL and len(rows) >= SYNC_TAIL:
            self.log(tr('Checked the latest {0} posts; older ones were not checked and will not be downloaded. To download the old ones: right-click the card → “Download whole profile”.', len(rows)))

        # --- база: решаем судьбу каждого поста из свежего списка ---
        known = db.db_known_video_ids(acc_id)   # что УЖЕ лежало в базе до этой проверки
        if first_sync:
            floor = None      # диапазоном управляет _apply_range (ниже)
        else:
            # Нижняя граница новизны: период юзера, либо граница «последних N».
            # Записи нет — берём самую свежую дату скачанного: посты новее её —
            # новинки, старше — старьё, которое тянуть нельзя (защита от наводнения).
            floor = acc["date_from"] or db.db_newest_downloaded_date(acc_id)
        # Верхняя граница периода: диапазон аккаунта — постоянная задача,
        # действует не только при первой загрузке, а ВСЕГДА (посты новее
        # даты «по» тоже не качаются, пока период не изменят или не снимут).
        upper = acc["date_to"]
        new_count = skipped_old = 0
        fresh = [(vid, up, title) for vid, up, title in rows if vid not in known]
        db.db_upsert_videos(acc_id, fresh)   # одна транзакция вместо коммита на каждый пост
        to_skip = []
        for vid, up, _title in fresh:      # уже лежавшие в базе (в т.ч. вернутые из пропусков) не трогаем
            out_of_range = ((floor and up and up < floor)
                            or (upper and up and up > upper))
            if out_of_range:
                to_skip.append(vid)         # вне диапазона — в очередь не попадёт
                skipped_old += 1
            else:
                new_count += 1
        db.db_skip_videos(acc_id, to_skip)
        msg = tr('“{0}”: received {1}, new in database: {2}.', nickname, len(rows), new_count)
        if skipped_old:
            msg += tr(' Old posts outside the range skipped: {0}.', skipped_old)
        self.log(msg)
        self.emit("stats", acc_id)

        # --- диапазон первой загрузки (как в 4K Tokkit) ---
        if first_sync:
            self._apply_range(acc_id, acc, rows)

        pending = db.db_pending_videos(acc_id)
        if self._is_cancelled(acc_id):
            for v in pending:
                db.db_skip_video(acc_id, v)
            self.log(tr('“{0}”: CANCELLED — download not started, {1} removed from the queue (restore: right-click → “Download skipped”).', nickname, len(pending)),
                     LEVEL_WARN)
            self.emit("card", (acc_id, tr('Cancelled')))
            self.emit("progress", (acc_id, None))
            self.emit("stats", acc_id)
            self.emit("toast", ("info", tr('@{0}: cancelled', nickname)))
            return True
        if not pending:
            _t, done, _p, skipped = db.db_counts(acc_id)
            extra = tr(', {0} outside range', skipped) if skipped else ""
            self.emit("card", (acc_id, None))
            self.log(tr('“{0}”: everything is already downloaded ({1}{2}).', nickname, done, extra), LEVEL_OK)
            self.emit("progress", (acc_id, None))
            self.emit("toast", ("ok", tr('@{0}: everything is already downloaded ({1}{2})', nickname, done, extra)))
            db.db_touch_sync(acc_id)
            self.emit("stats", acc_id)
            return True

        return self._download_pending(acc, pending)

    def _download_pending(self, acc, pending):
        """Качает очередь постов. Число потоков — настройка parallel_downloads
        (1 по умолчанию: последовательно, как раньше; до 4 — параллельно, с общей
        паузой при «TikTok просит не частить»)."""
        workers = settings.get_parallel(settings.load())
        if workers > 1 and len(pending) > 1:
            return self._download_pending_par(acc, pending, workers)
        return self._download_pending_seq(acc, pending)

    def _download_job(self, acc, vid, has_ffmpeg):
        """Одна загрузка в потоке пула: небольшая случайная пауза перед стартом,
        чтобы запросы не шли залпом."""
        self._wait_throttle()
        time.sleep(random.uniform(0.3, 1.2))
        return self.download_one(acc, vid, has_ffmpeg)

    def _download_pending_par(self, acc, pending, workers):
        """Параллельная очередь: скользящее окно из `workers` загрузок. Пауза и
        «Стоп» проверяются перед постановкой КАЖДОГО поста в окно: уже идущие
        загрузки дорабатывают, новые не стартуют."""
        acc_id, nickname = acc["id"], acc["nickname"]
        has_ffmpeg = bool(shutil.which("ffmpeg"))
        total = len(pending)
        self.log(tr('“{0}”: {1} posts to download, workers: {2}.', nickname, total, workers), LEVEL_DL)
        ok_count = fail_count = finished = streak = 0
        todo = iter(pending)
        in_flight = {}
        stop = None                                    # None | "cancel" | "pause"

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="dl") as pool:

            def submit_next():
                nonlocal stop
                if stop:
                    return
                if self._is_cancelled(acc_id):
                    stop = "cancel"
                    return
                if self._is_paused(acc_id):
                    stop = "pause"
                    return
                vid = next(todo, None)
                if vid is None:
                    return
                in_flight[pool.submit(self._download_job, acc, vid, has_ffmpeg)] = vid

            for _ in range(workers):
                submit_next()
            self.emit("card", (acc_id, tr('Downloading 0/{0}…', total)))
            while in_flight:
                done, _pending = concurrent.futures.wait(
                    in_flight, return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in done:
                    vid = in_flight.pop(fut)
                    try:
                        ok, path, kind = fut.result()
                    except Exception:
                        _LOG.exception(tr('download %s'), vid)
                        self.log_exc(tr('Post {0}: download crashed:', vid))
                        ok, path, kind = False, None, "video"
                    finished += 1
                    if ok:
                        db.db_mark_downloaded(acc_id, vid, path, kind)
                        self._run_note_file(nickname, acc_id, vid, path, kind)
                        ok_count += 1
                        streak = 0
                    else:
                        self._run_note_fail(nickname)
                        fail_count += 1
                        streak += 1
                        if (streak >= FAIL_STREAK_LIMIT and ok_count == 0
                                or streak >= FAIL_STREAK_LIMIT * 2) and not stop:
                            stop = "limit"
                    self.log(tr('Post {0}/{1}: id {2} — {3}', finished, total, vid, tr('done') if ok else tr('failure')),
                             LEVEL_INFO if ok else LEVEL_WARN)
                    self.emit("card", (acc_id, tr('Downloading {0}/{1}…', finished, total)))
                    self.emit("progress", (acc_id, (finished, total)))
                    submit_next()

        rest = list(todo)
        if stop == "limit":
            self._streak_stop(nickname, streak, len(rest))
            return self._pending_summary(acc, ok_count, fail_count, total)
        if stop == "cancel":
            db.db_skip_videos(acc_id, rest)
            self.log(tr('“{0}”: CANCELLED — stopped after {1} posts, {2} removed from the queue (restore: right-click → “Download skipped”).', nickname, ok_count, len(rest)), LEVEL_WARN)
            self.emit("card", (acc_id, tr('Cancelled')))
            self.emit("progress", (acc_id, None))
            self.emit("toast", ("info", tr('@{0}: cancelled, {1} removed from the queue', nickname, len(rest))))
            self.emit("stats", acc_id)
            self.emit("posts_dirty", acc_id)
            return True
        if stop == "pause":
            self.log(tr('“{0}”: PAUSED — stopped after {1} posts, {2} left in the queue. Resume (“Resume”) — the rest will download automatically.', nickname, ok_count, len(rest)), LEVEL_WARN)
            self.emit("card", (acc_id, tr('Paused')))
            self.emit("progress", (acc_id, None))
            self.emit("toast", ("info", tr('@{0}: paused — {1} in the queue', nickname, len(rest))))
            return True
        return self._pending_summary(acc, ok_count, fail_count, total)

    def _download_pending_seq(self, acc, pending):
        """Качает готовую очередь постов. Пауза проверяется ПЕРЕД КАЖДЫМ постом:
        нажатие «Пауза» останавливает работу после текущего файла, остаток
        остаётся в очереди до следующей синхронизации. Возвращает True при
        успехе или при честной остановке паузой."""
        acc_id, nickname = acc["id"], acc["nickname"]
        has_ffmpeg = bool(shutil.which("ffmpeg"))
        self.log(tr('“{0}”: {1} posts to download.', nickname, len(pending)), LEVEL_DL)
        ok_count, fail_count, streak = 0, 0, 0
        for i, vid in enumerate(pending, 1):
            if self._is_cancelled(acc_id):
                rest = pending[i - 1:]
                for v in rest:
                    db.db_skip_video(acc_id, v)
                self.log(tr('“{0}”: CANCELLED — stopped after {1} posts, {2} removed from the queue (restore: right-click → “Download skipped”).', nickname, ok_count, len(rest)), LEVEL_WARN)
                self.emit("card", (acc_id, tr('Cancelled')))
                self.emit("progress", (acc_id, None))
                self.emit("toast", ("info",
                          tr('@{0}: cancelled, {1} removed from the queue', nickname, len(rest))))
                self.emit("stats", acc_id)
                self.emit("posts_dirty", acc_id)
                return True
            if self._is_paused(acc_id):
                left = len(pending) - i + 1
                self.log(tr('“{0}”: PAUSED — stopped after {1} posts, {2} left in the queue. Resume (“Resume”) — the rest will download automatically.', nickname, ok_count, left), LEVEL_WARN)
                self.emit("card", (acc_id, tr('Paused')))
                self.emit("progress", (acc_id, None))
                self.emit("toast", ("info", tr('@{0}: paused — {1} in the queue', nickname, left)))
                return True
            self.emit("card", (acc_id, tr('Downloading {0}/{1}…', i, len(pending))))
            self.emit("progress", (acc_id, (i, len(pending))))
            self.log(tr('Post {0}/{1}: id {2}', i, len(pending), vid), LEVEL_DL)
            ok, path, kind = self.download_one(acc, vid, has_ffmpeg)
            if ok:
                db.db_mark_downloaded(acc_id, vid, path, kind)
                self._run_note_file(nickname, acc_id, vid, path, kind)
                ok_count += 1
                streak = 0
            else:
                self._run_note_fail(nickname)
                fail_count += 1
                streak += 1
                if streak >= FAIL_STREAK_LIMIT and ok_count == 0 or streak >= FAIL_STREAK_LIMIT * 2:
                    self._streak_stop(nickname, streak, len(pending) - i)
                    break
        return self._pending_summary(acc, ok_count, fail_count, len(pending))

    def _streak_stop(self, nickname, streak, left):
        """Серия неудач подряд: почти всегда это лимит запросов TikTok.
        Оставшиеся посты НЕ помечаются — они доедут при следующей синхронизации."""
        self.log(tr('“{0}”: {1} failures in a row — TikTok is most likely limiting requests. Stopped; {2} posts stay in the queue. Sync again in 30+ minutes.',
                    nickname, streak, left), LEVEL_WARN)

    def _pending_summary(self, acc, ok_count, fail_count, n_total):
        """Общий итог очереди (последовательной и параллельной). True, если что-то скачано."""
        acc_id, nickname = acc["id"], acc["nickname"]
        db.db_touch_sync(acc_id)
        self.emit("card", (acc_id, None))
        self.emit("progress", (acc_id, None))
        if fail_count:
            self.emit("toast", ("warn", tr('@{0}: downloaded {1}, failures {2}', nickname, ok_count, fail_count)))
        else:
            self.emit("toast", ("ok", tr('@{0}: downloaded {1} of {2}', nickname, ok_count, n_total)))
        _t, done, _p, skipped = db.db_counts(acc_id)
        extra = tr(', outside range {0}', skipped) if skipped else ""
        self.log(tr('Result for “{0}”: downloaded {1}, failures {2}. Downloaded in database: {3}{4}.', nickname, ok_count, fail_count, done, extra),
                 LEVEL_SUM if not fail_count else LEVEL_WARN)
        if fail_count:
            self.log(tr('Failures are usually fixed by syncing again or by using browser cookies.'), LEVEL_INFO)
        self.emit("stats", acc_id)
        self.emit("posts_dirty", acc_id)
        return ok_count > 0


    def task_redownload(self, acc_id, video_id):
        """«Перекачать» один пост (окно постов, ПКМ по плитке). Явное действие
        пользователя — единственный способ перекачать файл, удалённый вручную
        из папки: автоматом программа удалённые файлы не трогает."""
        self._busy_enter()
        try:
            acc = db.db_get_account(acc_id)
            if not acc:
                return
            if acc["paused"]:
                self.log(tr('“{0}” is paused — press “Resume” on the card first.', acc['nickname']),
                         LEVEL_WARN)
                self.emit("toast", ("info", tr('@{0}: account is paused', acc['nickname'])))
                return
            if not db.db_force_queue(acc_id, video_id):
                self.log(tr('“{0}”: post {1} is already in the queue.', acc['nickname'], video_id), LEVEL_INFO)
            else:
                self.log(tr('“{0}”: re-downloading post {1} (as you requested).', acc['nickname'], video_id),
                         LEVEL_HEAD)
            self.emit("card", (acc_id, tr('Downloading…')))
            self._download_pending(acc, [video_id])
            self.emit("posts_dirty", acc_id)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.log_exc(tr('Post re-download interrupted:'))
        finally:
            self._cancel_req.discard(acc_id)
            self._busy_exit()

    def scan_covers(self, acc_id):
        """Локальные превью постов из media/ (один проход по папке):
        {video_id: путь}. Источники: «…ID.cover.jpg» (кэш oembed от
        task_fill_covers), картинки фото-постов (gallery-dl), обложки
        yt-dlp от --write-thumbnail (обычно webp). Приоритет: cover →
        jpg/png/gif → webp — чтобы превью не побеждало настоящее фото."""
        media = db.account_media_dir(acc_id)
        if not media:
            return {}
        names = postmedia.list_names(media)
        best = {}
        for name in names:
            low = name.lower()
            if low.endswith((".part", ".json", ".description")):
                continue
            if not low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                continue
            m = re.search(r"(\d{10,25})", name)   # ID поста (даты — 8 цифр, не совпадут)
            if not m:
                continue
            vid = m.group(1)
            rank = 0 if ".cover." in low else (2 if low.endswith(".webp") else 1)
            cur = best.get(vid)
            if cur is None or rank < cur[0]:
                best[vid] = (rank, os.path.join(media, name))
        return {vid: p for vid, (_r, p) in best.items()}

    def task_fill_covers(self, acc_id, video_ids):
        """Докачивает превью скачанных постов через лёгкий oembed-эндпоинт
        TikTok и кладёт в media/<ID>.cover.jpg. По одному, с паузами, чтобы
        не дёргать сеть; результат каждого — событие vcov (окно постов
        обновляет плитку на лету), в конце — vcov_done."""
        try:
            acc = db.db_get_account(acc_id)
            media = db.account_media_dir(acc_id)
            if not acc or not media:
                return
            for vid in video_ids:
                if acc_id in self._cancel_req:
                    break
                dest = os.path.join(media, postmedia.SUB_INFO if self.sort_by_type else "", f"{vid}.cover.jpg")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(dest):
                    self.emit("vcov", (acc_id, vid, dest))
                    continue
                info = self._oembed_info(
                    f"https://www.tiktok.com/@{acc['nickname']}/video/{vid}")
                thumb = (info or {}).get("thumbnail_url")
                ok = self._download_small_image(thumb, dest) if thumb else False
                self.emit("vcov", (acc_id, vid, dest if ok else None))
                time.sleep(0.15)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
        finally:
            self.emit("vcov_done", acc_id)

    def task_verify_files(self, acc_id):
        """«Проверить файлы и докачать» (контекстное меню карточки): полная
        сверка базы с media/ — пропавшее возвращается в очередь и скачивается.
        В отличие от автопроверки при синхронизации, тут проверяются и посты
        без сохранённого пути (старые фото-посты)."""
        self._busy_enter()
        try:
            acc = db.db_get_account(acc_id)
            if not acc:
                return
            if acc["paused"]:
                self.log(tr('“{0}” is paused — press “Resume” on the card first.', acc['nickname']),
                         LEVEL_WARN)
                return
            self.log(tr('===== File check: {0} =====', acc['nickname']), LEVEL_HEAD)
            db.db_ensure_folder(acc_id)
            healed, missing, lost = self._verify_media_files(acc["id"], include_unknown=True,
                                                             requeue=True)
            self.log(tr('Database vs media/ comparison: paths fixed {0}, returned to queue {1} (files missing: {2}).', healed, missing, lost))
            pending = db.db_pending_videos(acc["id"])
            if not pending:
                self.log(tr('“{0}”: all files are in place.', acc['nickname']), LEVEL_OK)
                self.emit("card", (acc_id, None))
                self.emit("progress", (acc_id, None))
                self.emit("toast", ("ok", tr('@{0}: all files are in place', acc['nickname'])))
                self.emit("stats", acc_id)
                self.emit("posts_dirty", acc_id)
                return
            self._download_pending(acc, pending)
            self.emit("posts_dirty", acc_id)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.log_exc(tr('File check interrupted:'))
        finally:
            self._cancel_req.discard(acc_id)
            self._busy_exit()

    def task_backfill(self, acc_id):
        """«Докачать весь профиль» (правый клик на карточке): полный список постов,
        всё нескачанное — в очередь и в скачивание. Диапазон, выбранный при
        добавлении («последние N»/период), тут игнорируется — это явное действие
        юзера, а не молчаливая докачка."""
        self._busy_enter()
        try:
            acc = db.db_get_account(acc_id)
            if not acc:
                return
            if acc["paused"]:
                self.log(tr('“{0}” is paused — press “Resume” on the card first.', acc['nickname']),
                         LEVEL_WARN)
                return
            acc_id, nickname, sec_uid = acc["id"], acc["nickname"], acc["sec_uid"]
            self.log(tr('===== Full check: {0} =====', nickname), LEVEL_HEAD)
            self.emit("card", (acc_id, tr('Building the full post list…')))
            self.emit("progress", (acc_id, (0, 0)))
            self.log(tr('Requesting the WHOLE post list — on large accounts this takes a minute or two.'))
            db.db_ensure_folder(acc_id)
            sec_uid = self._ensure_sec_uid(acc_id, nickname, sec_uid)
            url = f"tiktokuser:{sec_uid}" if sec_uid else f"https://www.tiktok.com/@{nickname}"
            rows, current_handle, _enum_err = self._enumerate_videos(url, 0, acc_id=acc_id)
            if current_handle and current_handle.lower() != nickname.lower():
                db.db_rename_account(acc_id, current_handle)
                acc = db.db_get_account(acc_id)
                nickname = current_handle
                self.emit("accounts", None)
            if not rows:
                self.log(tr('The post list is empty or TikTok did not respond — see the log above.'), LEVEL_ERROR)
                self.emit("card", (acc_id, None))
                self.emit("progress", (acc_id, None))
                self.emit("toast", ("error", tr('@{0}: could not get the post list', nickname)))
                return
            added = db.db_upsert_videos(acc_id, rows)
            self.log(tr('Full list: {0} posts, added to the database: {1}.', len(rows), added))
            pending = db.db_pending_videos(acc_id)
            if not pending:
                self.log(tr('“{0}”: nothing to download — everything is already downloaded.', nickname), LEVEL_OK)
                self.emit("card", (acc_id, None))
                self.emit("progress", (acc_id, None))
                db.db_touch_sync(acc_id)
                self.emit("stats", acc_id)
                self.emit("posts_dirty", acc_id)
                return
            self._download_pending(acc, pending)
            self.emit("posts_dirty", acc_id)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self.log_exc(tr('Full check interrupted:'))
        finally:
            self._cancel_req.discard(acc_id)
            self._busy_exit()

    def _apply_range(self, acc_id, acc, rows):
        """Первая синхронизация: применяем выбранный при добавлении диапазон.
        Посты вне диапазона помечаются пропущенными (их можно вернуть кнопкой
        «Скачать пропущенное»)."""
        if acc["limit_count"] and not (acc["date_from"] or acc["date_to"]):
            # «Последние N»: скачали ровно N свежих. Границу берём ПОЗИЦИОННО —
            # дата последнего поста в списке. Минимум по датам брать нельзя:
            # в пятёрку мог попасть закреплённый пост (пиннат) с датой постарше —
            # тогда следующая проверка тянула бы полпрофиля старья.
            floor = None
            for _v, d, _t in reversed(rows):
                if d:
                    floor = d
                    break
            if floor:
                db.db_set_date_floor(acc_id, floor)
                self.log(tr('Range: last {0} posts, cutoff — {1}. Older ones will not be downloaded (restore: right-click → “Download whole profile”).', len(rows), fmt_date(floor)))
            else:
                self.log(tr('Range: last {0} posts. TikTok did not provide dates — the “do not download old posts” protection is weakened.', len(rows)), LEVEL_WARN)
            return
        if acc["date_from"] or acc["date_to"]:
            keep, skip = apply_date_range(rows, acc["date_from"], acc["date_to"])
            for vid in skip:
                db.db_skip_video(acc_id, vid)
            df = fmt_date(acc["date_from"]) if acc["date_from"] else tr('the beginning')
            dt = fmt_date(acc["date_to"]) if acc["date_to"] else tr('today')
            self.log(tr('Period range {0} — {1}: downloading {2}, skipped {3} (restore: right-click → “Download skipped”).', df, dt, len(keep), len(skip)))
