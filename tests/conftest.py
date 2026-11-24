import os
os.environ.setdefault("TOKSAVER_LANG", "ru")   # тесты проверяют русские тексты
import os
import sys

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


@pytest.fixture
def tmp_app(tmp_path, monkeypatch):
    """Изолированные пути: данные, настройки и логи уходят во временную папку."""
    import paths
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "SETTINGS_FILE", str(tmp_path / "data" / "settings.json"))
    monkeypatch.setattr(paths, "BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setattr(paths, "OUTPUT_DIR", str(tmp_path / "Downloads"))
    monkeypatch.setattr(paths, "LOG_DIR", str(tmp_path / "logs"))
    # engine вычисляет пути к бинарникам при импорте — подменяем и их, чтобы тесты
    # никогда не трогали настоящую папку bin/
    import engine
    monkeypatch.setattr(engine, "YT_DLP_EXE", str(tmp_path / "bin" / "yt-dlp"))
    monkeypatch.setattr(engine, "BYEDPI_EXE", str(tmp_path / "bin" / "ciadpi.exe"))
    return tmp_path
