"""Сборка самостоятельной программы (без установки Python): python build_exe.py

Нужен PyInstaller (pip install pyinstaller) и зависимости из requirements.txt.
Результат: dist/TokSaver/ (папка с TokSaver[.exe]) и архив dist/TokSaver-<платформа>.zip.
Данные пользователя (data/, bin/, logs/) создаются рядом с исполняемым файлом.
"""
import os
import platform
import shutil
import sys

import PyInstaller.__main__

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SEP = ";" if sys.platform == "win32" else ":"


def main():
    os.chdir(APP_DIR)
    shutil.rmtree(os.path.join(APP_DIR, "build"), ignore_errors=True)
    shutil.rmtree(os.path.join(APP_DIR, "dist", "TokSaver"), ignore_errors=True)
    args = [
        "main.py", "--name", "TokSaver", "--noconfirm", "--clean",
        "--windowed",                                   # без чёрного окна консоли
        "--add-data", f"lang{SEP}lang",
        "--collect-all", "customtkinter",
        "--collect-all", "gallery_dl",                  # экстракторы подгружаются динамически
        "--hidden-import", "socks",
        "--hidden-import", "PIL._tkinter_finder",
    ]
    PyInstaller.__main__.run(args)
    plat = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    arch = platform.machine().lower().replace("amd64", "x86_64")
    name = f"TokSaver-{plat}-{arch}"
    for f in ("README.md", "README.ru.md"):
        if os.path.exists(f):
            shutil.copy(f, os.path.join("dist", "TokSaver", f))
    shutil.make_archive(os.path.join("dist", name), "zip", "dist", "TokSaver")
    print("Готово:", os.path.join("dist", name + ".zip"))


if __name__ == "__main__":
    main()
