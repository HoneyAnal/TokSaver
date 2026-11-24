"""Горячие клавиши в полях ввода при любой раскладке, меню правого клика, колесо мыши.

Часть интерфейса TokSaver: миксин для класса App (ui.py). Методы перенесены
из прежнего ui.py без изменений."""
from i18n import tr
import tkinter as tk


import applog
from ui_widgets import FlatScroll
from ui_widgets import PopupMenu

_LOG = applog.get(__name__)


class EditMixin:
    # ------------------ клавиатура: вставка при любой раскладке ------------------
    # Windows-баг tk: при русской (и любой не-латинской) раскладке Ctrl+V
    # приходит как Ctrl+«м» — штатная вставка не срабатывает. Роутим по
    # ВИРТУАЛЬНОМУ коду клавиши (86 = физическая V на Windows, 55 = на X11):
    # он одинаков для всех раскладок. Заодно чиним Ctrl+C/X/A и даём
    # Ctrl+Insert / Shift+Insert и меню по правому клику.
    _EDIT_KEYS = {
        "paste": ((86, 55), ("v", "cyrillic_em", "u043c"), ("v", "м", "\x16")),
        "copy": ((67, 54), ("c", "cyrillic_es", "u0441"), ("c", tr('from'), "\x03")),
        "cut": ((88, 53), ("x", "cyrillic_che", "u0447"), ("x", "ч", "\x18")),
        "all": ((65, 38), ("a", "cyrillic_ef", "u0444"), ("a", "ф", "\x01")),
    }

    @classmethod
    def _edit_action(cls, event):
        """Какое действие запрошено — работает при любой раскладке."""
        ks = (getattr(event, "keysym", "") or "").lower()
        ch = getattr(event, "char", "") or ""
        kc = getattr(event, "keycode", 0) or 0
        for action, (codes, syms, chars) in cls._EDIT_KEYS.items():
            if kc in codes or ks in syms or ch.lower() in chars:
                return action
        return None

    def _attach_edit_keys(self, entry):
        """Подключить вставку/копирование и контекстное меню к полю ввода."""
        w = getattr(entry, "_entry", entry)   # внутренний tk.Entry у CTkEntry
        w.bind("<Control-KeyPress>", self._on_edit_key, add="+")
        w.bind("<Control-Insert>", lambda _e: (self._edit_do(w, "paste"), "break")[1],
               add="+")
        w.bind("<Shift-Insert>", lambda _e: (self._edit_do(w, "paste"), "break")[1],
               add="+")
        menu = PopupMenu(w, self.T, self._font_family, min_width=200)
        menu.add_command(label=tr('Paste'), command=lambda: self._edit_do(w, "paste"))
        menu.add_command(label=tr('Copy'), command=lambda: self._edit_do(w, "copy"))
        menu.add_command(label=tr('Cut'), command=lambda: self._edit_do(w, "cut"))
        menu.add_separator()
        menu.add_command(label=tr('Select all'), command=lambda: self._edit_do(w, "all"))
        w.bind("<Button-3>", lambda e: self._popup_menu(menu, e))

    def _on_edit_key(self, event):
        action = self._edit_action(event)
        if action is None:
            return None
        self._edit_do(event.widget, action)
        return "break"

    @staticmethod
    def _edit_do(w, action):
        try:
            if action == "paste":
                data = w.clipboard_get()
                try:
                    if w.selection_present():
                        w.delete("sel.first", "sel.last")
                except Exception as _exc:
                    _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                    pass
                w.insert(w.index("insert"), data)
            elif action == "copy":
                if w.selection_present():
                    w.clipboard_clear()
                    w.clipboard_append(w.get("sel.first", "sel.last"))
            elif action == "cut":
                if w.selection_present():
                    w.clipboard_clear()
                    w.clipboard_append(w.get("sel.first", "sel.last"))
                    w.delete("sel.first", "sel.last")
            elif action == "all":
                w.select_range(0, "end")
                w.icursor("end")
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass  # пустой буфер обмена или не тот виджет — молча

    def _popup_menu(self, menu, event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------ колесо мыши ------------------------------
    def _on_wheel(self, event):
        """Скроллим зону, над которой курсор (независимо от фокуса окна)."""
        try:
            w = self.winfo_containing(event.x_root, event.y_root)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            return None
        while w is not None:
            if isinstance(w, FlatScroll):
                w.wheel_scroll(event)
                return "break"
            w = getattr(w, "master", None)
        return None
