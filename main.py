"""Точка входа TokSaver.

Запуск:  python main.py   (или двойной клик по Запустить.bat)
Структура проекта:
  paths.py     — пути и константы
  settings.py  — настройки (data/settings.json)
  applog.py    — файловый лог (logs/toksaver.log)
  netutil.py   — сеть: SOCKS5, докачка, SHA-256, безопасная распаковка
  db.py        — хранилище: реестр аккаунтов + папка с базой у каждого аккаунта
  engine.py    — движок: yt-dlp, ByeDPI, gallery-dl, задачи скачивания
  ui*.py       — интерфейс (customtkinter)

При первом запуске программа сама создаёт собственное окружение .venv рядом
с собой и ставит в него зависимости из requirements.txt — системный Python
не засоряется. Отключить: переменная окружения TOKSAVER_NO_VENV=1.
"""
import sys

# Собранная версия (PyInstaller): движок зовёт «себя» как gallery-dl.
if len(sys.argv) > 1 and sys.argv[1] == "--gallery-dl":
    sys.argv = ["gallery-dl"] + sys.argv[2:]
    import gallery_dl
    sys.exit(gallery_dl.main())

from i18n import tr
import hashlib
import os
import subprocess

APP_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
           else os.path.dirname(os.path.abspath(__file__)))
VENV_DIR = os.path.join(APP_DIR, ".venv")
REQUIREMENTS = os.path.join(APP_DIR, "requirements.txt")
IS_WIN = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _setup_logging():
    """Запуск через pythonw (Запустить.bat) не имеет stdout/stderr: любой
    print и даже traceback умирает молча, и программа «просто мигает».
    Перенаправляем оба потока в run.log рядом с программой — теперь в файл
    попадает вообще всё, включая ошибки до открытия окна."""
    if sys.stdout is not None and sys.stderr is not None:
        return  # запущено из консоли — вывод и так виден
    try:
        f = open(os.path.join(APP_DIR, "run.log"), "a",
                 encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
        import time
        f.write(tr('\n===== launch ') + time.strftime("%Y-%m-%d %H:%M:%S")
                + " (python " + sys.version.split()[0] + ") =====\n")
    except OSError:
        pass


def _check_python_version():
    """Python 3.14+ совсем свежий: у части библиотек (Pillow, customtkinter) могут
    быть проблемы. Не блокируем запуск, но предупреждаем в журнале."""
    if sys.version_info < (3, 9):
        raise SystemExit(tr('Python 3.9 or newer is required (3.12 or 3.13 recommended).'))
    if sys.version_info >= (3, 14):
        print(tr('WARNING: Python ') + sys.version.split()[0] + tr(' is very new. Python 3.12 or 3.13 is recommended — all dependencies are verified for them.'))


_setup_logging()
_check_python_version()


# ------------------------------ окружение .venv ------------------------------

def _normal(p):
    return os.path.normcase(os.path.abspath(p))


def _in_target_venv():
    return _normal(sys.prefix) == _normal(VENV_DIR)


def _venv_python():
    """Python внутри .venv. Из окна без консоли (pythonw) — тоже без консоли."""
    windowless = os.path.basename(sys.executable).lower() == "pythonw.exe"
    if IS_WIN:
        exe = "pythonw.exe" if windowless else "python.exe"
        return os.path.join(VENV_DIR, "Scripts", exe)
    return os.path.join(VENV_DIR, "bin", "python")


def _requirements_hash():
    try:
        with open(REQUIREMENTS, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def _run(cmd):
    """Запуск с выводом в журнал; бросает CalledProcessError при ошибке."""
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="ignore", creationflags=NO_WINDOW)
    if r.stdout:
        print(r.stdout.strip()[-3000:])
    r.check_returncode()


def _prepare_venv():
    """Создаёт .venv и ставит requirements.txt (только если файл менялся)."""
    vpy = _venv_python()
    if not os.path.exists(vpy):
        print(tr('Creating the .venv environment (first launch, takes a minute)…'))
        _run([sys.executable, "-m", "venv", VENV_DIR])
        if not os.path.exists(vpy):
            raise FileNotFoundError(vpy)
    stamp = os.path.join(VENV_DIR, ".requirements.sha256")
    want = _requirements_hash()
    have = ""
    try:
        with open(stamp, encoding="ascii") as f:
            have = f.read().strip()
    except OSError:
        pass
    if want and have != want:
        print(tr('Installing dependencies from requirements.txt…'))
        _run([vpy, "-m", "pip", "install", "--disable-pip-version-check", "-r", REQUIREMENTS])
        with open(stamp, "w", encoding="ascii") as f:
            f.write(want)
    return vpy


def _relaunch_in_venv():
    """Если мы не в .venv — готовим его и перезапускаем себя в нём.
    True — перезапуск выполнен (текущему процессу пора завершиться).
    При любой проблеме с окружением возвращает False: работаем в текущем Python."""
    if (os.environ.get("TOKSAVER_NO_VENV") == "1" or getattr(sys, "frozen", False)
            or _in_target_venv()):
        return False
    try:
        vpy = _prepare_venv()
    except Exception as e:
        print(tr('The .venv environment could not be prepared (') + repr(e) + tr(') — continuing with the current Python.'))
        return False
    cmd = [vpy, os.path.abspath(__file__)] + sys.argv[1:]
    if os.path.basename(vpy).lower() == "pythonw.exe":
        subprocess.Popen(cmd, cwd=APP_DIR, creationflags=NO_WINDOW)
        return True
    sys.exit(subprocess.call(cmd, cwd=APP_DIR))


def _ensure_deps():
    """Резерв (если .venv недоступен): доустановить недостающее из requirements.txt
    в тот python, которым запущен этот файл. Pillow проверяем ПЕРВЫМ — часть
    версий customtkinter сама импортирует PIL при старте."""
    missing = False
    for mod in ("PIL", "customtkinter", "socks"):
        try:
            __import__(mod)
        except ModuleNotFoundError:
            missing = True
    if missing:
        print(tr('First installation of components, please wait…'))
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "--disable-pip-version-check", "-r", REQUIREMENTS],
                              creationflags=NO_WINDOW)


if _relaunch_in_venv():
    sys.exit(0)

try:
    # Импорты внутри try: раньше ошибка импорта (например, несовместимая
    # версия библиотеки) умирала МОЛЧА — обработчик ниже её не видел.
    _ensure_deps()
    import applog  # noqa: E402
    import db      # noqa: E402
    import paths   # noqa: E402
    import ui      # noqa: E402

    applog.setup()
    db.db_init(paths.DATA_DIR)
    ui.run()
except Exception:
    # Программа запущена без консоли (через Запустить.bat), поэтому при
    # падении на старте обязаны сами показать причину: пишем crash.log
    # рядом с программой и поднимаем системное окно-сообщение.
    import time
    import traceback

    txt = traceback.format_exc()
    try:
        print(txt)  # в run.log или в консоль, если запускали вручную
    except Exception:
        pass
    try:
        log = os.path.join(APP_DIR, "crash.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write("\n[" + time.strftime("%Y-%m-%d %H:%M:%S") + "]\n"
                    + txt + "\n")
        print(tr('Details were written to the file:'), log)
    except OSError:
        log = None
    try:
        if sys.platform == "win32":
            import ctypes
            # Показываем НАЧАЛО самого текста ошибки: причину видно сразу,
            # crash.log нужен только для полного следа.
            brief = txt.strip().splitlines()
            tail = "\n".join(brief[-6:])[-600:]
            ctypes.windll.user32.MessageBoxW(
                0,
                tr('The app could not start.\n\n') + tail
                + tr('\n\nThe full report is in crash.log in the app folder.'),
                tr('TokSaver — startup error'),
                0x10)  # MB_ICONERROR
    except Exception:
        pass
    sys.exit(1)
