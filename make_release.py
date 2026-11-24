"""Чистая сборка для передачи: python make_release.py

Кладёт в zip ТОЛЬКО то, что нужно программе: исходники, bat-файлы, README,
requirements.txt и тесты. НЕ попадают: ваши данные (data/), скачанное
(Downloads/), логи, кэш Python, .venv, скачанные бинарники (программа
сама скачает их с проверкой контрольных сумм).
"""
import os
import sys
import zipfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
NAME = os.path.basename(APP_DIR) or "TikTokSaver"

EXCLUDE_DIRS = {".venv", "__pycache__", "logs", "data", "Downloads", ".pytest_cache",
                "dist", "build", ".git", ".github"}
EXCLUDE_FILES = {"run.log", "crash.log", ".gitignore"}
EXCLUDE_EXT = (".pyc", ".sha256", ".part", ".spec")
EXCLUDE_IN_BIN = ("yt-dlp", "ciadpi.exe", "byedpi.zip")   # бинарники не раздаём


def wanted(rel, name):
    if name in EXCLUDE_FILES or name.endswith(EXCLUDE_EXT):
        return False
    if rel.split(os.sep)[0] == "bin" and name.startswith(EXCLUDE_IN_BIN):
        return False
    return True


def main():
    out = os.path.join(APP_DIR, "dist")
    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, NAME + ".zip")
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(APP_DIR):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
            for f in sorted(files):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, APP_DIR)
                if wanted(rel, f):
                    z.write(full, os.path.join(NAME, rel))
                    count += 1
        for empty in ("data", "Downloads"):
            z.writestr(f"{NAME}/{empty}/.keep", "")
    print(f"Готово: {target} ({count} файлов)")


if __name__ == "__main__":
    sys.exit(main())
