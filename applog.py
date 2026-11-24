"""Файловый лог TokSaver: сюда пишутся ошибки, которые раньше глотались молча.

Журнал в окне программы остаётся для людей; этот лог (logs/toksaver.log)
нужен, чтобы при сбое можно было понять, что именно произошло.
Файл ротируется (3 × 1 МБ), поэтому не разрастается.
"""
import logging
import logging.handlers
import os
import re

import paths

_CONFIGURED = False

# Секреты, которые не должны попадать ни в журнал, ни в файл лога
_REDACT = [
    (re.compile(r"(?i)\b(cookie|set-cookie|authorization)\s*[:=]\s*[^\r\n]+"), r"\1: <hidden>"),
    (re.compile(r"(?i)\b(sessionid(?:_ss)?|sid_tt|sid_guard|uid_tt|msToken|ttwid|odin_tt|"
                r"csrf_session_id|tt_csrf_token|passport_[a-z_]+|access_token|token)=[^;&\s\"']+"),
     r"\1=<hidden>"),
    (re.compile(r"(?i)([A-Z]:\\[^\s\"']*|/[^\s\"']*)cookies?[^\\/\s\"']*\.txt"), "<cookies.txt>"),
]


def redact(text):
    """Вырезает из строки значения cookies/токенов и путь к файлу cookies."""
    text = str(text)
    for rx, repl in _REDACT:
        text = rx.sub(repl, text)
    return text


class _RedactFormatter(logging.Formatter):
    """Редактирует итоговую строку целиком — вместе с текстом traceback."""

    def format(self, record):
        return redact(super().format(record))


def setup():
    """Настраивает корневой логгер «toksaver». Повторный вызов безопасен."""
    global _CONFIGURED
    log = logging.getLogger("toksaver")
    if _CONFIGURED:
        return log
    _CONFIGURED = True
    log.setLevel(logging.DEBUG)
    log.propagate = False
    try:
        os.makedirs(paths.LOG_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(paths.LOG_DIR, "toksaver.log"),
            maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(_RedactFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
    except OSError:
        log.addHandler(logging.NullHandler())   # папка только для чтения — не падаем
    return log


def get(name):
    """Логгер модуля: applog.get(__name__)."""
    setup()
    return logging.getLogger("toksaver." + name)
