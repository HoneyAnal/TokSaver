"""Проверка «проводки» интерфейса без экрана и без tkinter: на заглушках
импортируются все модули ui*, и убеждаемся, что у App есть все методы."""
import importlib
import sys
import types


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Meta(name, (), {})

    def __getitem__(cls, k):
        return cls._d.setdefault(k, {}) if hasattr(cls, "_d") else _Dummy()


class _Dummy(metaclass=_Meta):
    theme = {"CTkFrame": {}, "CTkScrollbar": {}}

    def __init__(self, *a, **k):
        pass


def _stub_module(name):
    m = types.ModuleType(name)
    m.__getattr__ = lambda attr: _Meta(attr, (), {})
    return m


def test_all_ui_modules_import_and_app_complete(monkeypatch):
    tk = _stub_module("tkinter")
    tk.font = _stub_module("tkinter.font")
    tk.filedialog = tk.messagebox = tk.simpledialog = _stub_module("tkinter.x")
    ctk = _stub_module("customtkinter")
    ctk.ThemeManager = _Dummy
    for n, m in {"tkinter": tk, "tkinter.font": tk.font, "customtkinter": ctk}.items():
        monkeypatch.setitem(sys.modules, n, m)
    for n in [m for m in sys.modules if m.startswith("ui")]:
        monkeypatch.delitem(sys.modules, n)
    ui = importlib.import_module("ui")
    expected = {"__init__", "_apply_theme", "_poll", "_build_sidebar", "_toast", "_append_log",
                "_on_card_menu", "on_add", "on_close", "_open_calendar", "_on_edit_key",
                "_build_posts_window", "_on_cookies_changed", "_toggle_byedpi", "_update_deps",
                "_on_accounts_sort", "_on_parallel_changed"}
    have = {n for c in ui.App.__mro__ for n in vars(c)}
    assert expected <= have
    assert callable(ui.run)
    for n in [m for m in sys.modules if m.startswith("ui")]:
        sys.modules.pop(n, None)


def test_service_bat_binds_loopback_and_asks_confirmation():
    import os
    bat = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "service_install.bat"),
               encoding="utf-8").read()
    assert "-i 127.0.0.1" in bat and "YES" in bat
