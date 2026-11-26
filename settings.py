"""Настройки TokSaver (data/settings.json).

Читаются и пишутся одним модулем, запись атомарная (через временный файл),
поэтому обрыв посреди записи не портит файл. Лишние ключи сохраняются.

Ключи:
  theme          "dark" | "light"
  log_open       журнал в окне развёрнут
  byedpi_port    порт локального SOCKS-прокси ByeDPI (по умолчанию 1080;
                 если занят — программа возьмёт ближайший свободный)
  ytdlp_version  "latest" или конкретный тег релиза, например "2026.11.01"
  ytdlp_auto_update  обновлять yt-dlp при выходе новой версии
  parallel_downloads сколько постов качать одновременно, 1-4 (по умолчанию 1)
  accounts_sort / posts_sort  выбранная сортировка списков
  cookies_browser / cookies_file  выбор источника cookies запоминается
  auto_sync_mode off | hours | daily; auto_sync_hours, auto_sync_time; auto_sync_last
  posts_view     "list" (список) или "grid" (плитки) в окне постов
"""
from i18n import tr
import json
import os
import re
import threading

import applog
import paths

_LOG = applog.get(__name__)
_LOCK = threading.Lock()

DEFAULTS = {
    "theme": "dark",
    "language": "en",
    "log_open": True,
    "byedpi_port": 1080,
    "byedpi_enabled": False,
    "sort_by_type": True,
    "ytdlp_version": "latest",
    "ytdlp_auto_update": True,
    "parallel_downloads": 1,
    "accounts_sort": "added",
    "posts_sort": "new",
    "posts_view": "list",
    "cookies_browser": "",      # браузер, откуда брать cookies ("" — не использовать)
    "cookies_file": "",         # путь к cookies.txt ("" — не выбран)
    "auto_sync_mode": "off",    # off | hours | daily
    "auto_sync_hours": 24,      # для режима hours: 1, 3, 6, 12, 24
    "auto_sync_time": "03:00",  # для режима daily (HH:MM, локальное время)
    "auto_sync_last": 0,        # когда автосинхронизация запускалась последний раз
}

_TAG_RE = re.compile(r"^(latest|\d{4}\.\d{2}\.\d{2}(\.\d+)?)$")


def load(path=None):
    """Настройки с подставленными значениями по умолчанию."""
    path = path or paths.SETTINGS_FILE
    data = {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            data = raw
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        _LOG.warning('settings.json not read (%r) — using defaults', e)
    out = dict(DEFAULTS)
    out.update(data)
    return out


def save(data, path=None):
    """Атомарно записывает настройки. True при успехе."""
    path = path or paths.SETTINGS_FILE
    tmp = path + ".tmp"
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            return True
        except OSError as e:
            _LOG.warning('settings.json not written: %r', e)
            return False


def get_port(data=None):
    """Порт ByeDPI из настроек; неверное значение → 1080."""
    data = data if data is not None else load()
    try:
        port = int(data.get("byedpi_port", DEFAULTS["byedpi_port"]))
    except (TypeError, ValueError):
        return DEFAULTS["byedpi_port"]
    return port if 1024 <= port <= 65535 else DEFAULTS["byedpi_port"]


def get_ytdlp_version(data=None):
    """Тег yt-dlp из настроек: 'latest' или вида 2026.11.01 (иначе 'latest')."""
    data = data if data is not None else load()
    val = str(data.get("ytdlp_version", "latest")).strip()
    return val if _TAG_RE.match(val) else "latest"


def get_parallel(data=None):
    """Число параллельных загрузок: 1..4 (неверное значение → 1)."""
    data = data if data is not None else load()
    try:
        n = int(data.get("parallel_downloads", 1))
    except (TypeError, ValueError):
        return 1
    return max(1, min(4, n))


def get_auto_sync(data=None):
    """(режим, часы, 'HH:MM', last) с проверкой значений."""
    import autosync as schedule
    data = data if data is not None else load()
    mode = data.get("auto_sync_mode")
    mode = mode if mode in schedule.MODES else "off"
    try:
        hours = int(data.get("auto_sync_hours", 24))
    except (TypeError, ValueError):
        hours = 24
    hours = hours if hours in schedule.HOURS else 24
    hhmm = data.get("auto_sync_time")
    if schedule.parse_time(hhmm) is None:
        hhmm = "03:00"
    try:
        last = float(data.get("auto_sync_last") or 0)
    except (TypeError, ValueError):
        last = 0.0
    return mode, hours, hhmm, last
