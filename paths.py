"""Пути и общие константы приложения TokSaver.

Все файлы программы лежат рядом с main.py — портативно, папку можно переносить.

Структура данных:
  data/
    accounts.db          — реестр аккаунтов (без постов)
    0001_<ник>/          — папка КАЖДОГО аккаунта
        videos.db        — посты этого аккаунта
        media/           — скачанные видео и фото этого аккаунта
  Downloads/             — только разовые загрузки (без базы)
  logs/                  — файловый лог (toksaver.log)
  .venv/                 — собственное окружение Python (создаётся само)
"""
import os
import subprocess
import sys

IS_WIN = sys.platform == "win32"

FROZEN = bool(getattr(sys, "frozen", False))      # собранный PyInstaller-ом exe
# Ресурсы только для чтения (lang/…): в собранной версии лежат в _MEIPASS,
# а данные пользователя — рядом с exe (портативно, как и из исходников).
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
           else os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(APP_DIR, "bin")            # yt-dlp / ciadpi (ByeDPI)
OUTPUT_DIR = os.path.join(APP_DIR, "Downloads")   # разовые загрузки (без базы)
DATA_DIR = os.path.join(APP_DIR, "data")          # база + папки аккаунтов
LOG_DIR = os.path.join(APP_DIR, "logs")           # файловый лог
VENV_DIR = os.path.join(APP_DIR, ".venv")         # окружение с зависимостями
REQUIREMENTS = os.path.join(APP_DIR, "requirements.txt")
LEGACY_DB = os.path.join(APP_DIR, "tiktok.db")    # старая база (для миграции)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")  # тема и мелкие настройки UI

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
