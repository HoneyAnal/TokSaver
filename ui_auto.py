"""Автосинхронизация по расписанию и запоминание источника cookies.

Миксин для App (ui.py). Расписание работает, пока открыто окно программы
(отдельной службы нет): раз в 30 секунд проверяется, не пора ли запускаться.
Пропущенный из-за закрытой программы ежедневный запуск догоняется при открытии.
"""
import os
import threading
import time
import tkinter as tk

import customtkinter as ctk

import applog
import autosync
import db
import settings
from engine import LEVEL_SYS
from i18n import tr
from ui_theme import BROWSERS
from ui_theme import RADIUS

_LOG = applog.get(__name__)
TICK_MS = 30_000


class AutoSyncMixin:
    # ----------------------- cookies: запоминание выбора -----------------------
    def _load_cookie_prefs(self):
        """Восстанавливает выбор источника cookies с прошлого запуска."""
        browser = self.settings.get("cookies_browser") or ""
        if browser in BROWSERS and browser != tr('none'):
            self.engine.cookies = browser
        path = self.settings.get("cookies_file") or ""
        self.cookies_file_path = None
        if path:
            if os.path.isfile(path) and self.engine.set_cookies_file(path):
                self.cookies_file_path = path
            else:
                self.settings["cookies_file"] = ""      # файл исчез — забываем
                self._save_settings()
                self._pending_log = getattr(self, "_pending_log", []) + [
                    tr('[Auth] The saved cookies.txt is no longer available — choose it again.')]

    def _remember_cookies(self):
        self.settings["cookies_browser"] = self.engine.cookies or ""
        self.settings["cookies_file"] = self.cookies_file_path or ""
        self._save_settings()

    # ----------------------- автосинхронизация: интерфейс -----------------------
    def _auto_options(self):
        return [(tr('Off'), ("off", 24)),
                (tr('Every hour'), ("hours", 1)),
                (tr('Every 3 hours'), ("hours", 3)),
                (tr('Every 6 hours'), ("hours", 6)),
                (tr('Every 12 hours'), ("hours", 12)),
                (tr('Every day at…'), ("daily", 24))]

    def _build_auto_sync(self, inner, T):
        mode, hours, hhmm, _last = settings.get_auto_sync(self.settings)
        row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        row.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(row, text=tr('Auto-sync:'), font=ctk.CTkFont(size=13),
                     text_color=T["text"]).pack(side="left")
        opts = self._auto_options()
        self.auto_menu = ctk.CTkOptionMenu(
            row, values=[o[0] for o in opts], width=150, height=30, corner_radius=RADIUS,
            font=ctk.CTkFont(size=13), fg_color=T["field"], button_color=T["card_h"],
            button_hover_color=T["sel_line"], text_color=T["text"],
            dropdown_fg_color=T["card"], dropdown_hover_color=T["card_h"],
            dropdown_text_color=T["text"], command=self._on_auto_menu)
        cur = next((lbl for lbl, (m, h) in opts
                    if m == mode and (m != "hours" or h == hours)), opts[0][0])
        self.auto_menu.set(cur)
        self.auto_menu.pack(side="right")
        self.auto_time_row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        ctk.CTkLabel(self.auto_time_row, text=tr('Time (HH:MM):'), font=ctk.CTkFont(size=13),
                     text_color=T["text"]).pack(side="left")
        self.auto_time_entry = ctk.CTkEntry(
            self.auto_time_row, width=80, height=30, corner_radius=RADIUS, fg_color=T["field"],
            border_color=T["line"], text_color=T["text"], justify="center")
        self.auto_time_entry.insert(0, hhmm)
        self.auto_time_entry.pack(side="right")
        self.auto_time_entry.bind("<Return>", self._on_auto_time)
        self.auto_time_entry.bind("<FocusOut>", self._on_auto_time)
        if mode == "daily":
            self.auto_time_row.pack(fill="x", pady=(6, 0))
        self.auto_next_lbl = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=11),
                                          text_color=T["muted"], justify="left")
        self.auto_next_lbl.pack(anchor="w", pady=(2, 0))
        self._auto_refresh_label()

    def _on_auto_menu(self, label):
        mode, hours = dict(self._auto_options()).get(label, ("off", 24))
        old_mode = settings.get_auto_sync(self.settings)[0]
        self.settings["auto_sync_mode"] = mode
        self.settings["auto_sync_hours"] = hours
        if old_mode == "off" and mode != "off":
            self.settings["auto_sync_last"] = time.time()    # отсчёт — с момента включения
        if mode == "daily":
            self.auto_time_row.pack(fill="x", pady=(6, 0), before=self.auto_next_lbl)
        else:
            self.auto_time_row.pack_forget()
        self._save_settings()
        self._auto_refresh_label()

    def _on_auto_time(self, _event=None):
        text = self.auto_time_entry.get().strip()
        if autosync.parse_time(text) is None:
            self._toast(tr('Enter the time as HH:MM, for example 03:00'), "warn")
            self.auto_time_entry.delete(0, "end")
            self.auto_time_entry.insert(0, settings.get_auto_sync(self.settings)[2])
            return
        h, m = autosync.parse_time(text)
        text = f"{h:02d}:{m:02d}"
        self.auto_time_entry.delete(0, "end")
        self.auto_time_entry.insert(0, text)
        if self.settings.get("auto_sync_time") != text:
            self.settings["auto_sync_time"] = text
            self._save_settings()
            self._auto_refresh_label()

    def _auto_refresh_label(self):
        try:
            mode, hours, hhmm, last = settings.get_auto_sync(self.settings)
            nxt = autosync.next_run(mode, hours, hhmm, last)
            if nxt is None:
                text = tr('Off. The app must stay open for scheduled sync.')
            else:
                text = tr('Next sync: {0}', nxt.strftime("%d.%m %H:%M"))
            self.auto_next_lbl.configure(text=text)
        except Exception as _exc:
            _LOG.debug("auto label: %r", _exc)

    # ----------------------- автосинхронизация: таймер -----------------------
    def _auto_tick(self):
        try:
            mode, hours, hhmm, last = settings.get_auto_sync(self.settings)
            if mode != "off" and last <= 0:                 # старые настройки без отметки
                self.settings["auto_sync_last"] = last = time.time()
                self._save_settings()
            if (mode != "off" and not self._busy and self.engine.deps_ready
                    and autosync.is_due(mode, hours, hhmm, last)):
                self._auto_run()
            self._auto_refresh_label()
        except Exception as _exc:
            _LOG.debug("auto tick: %r", _exc, exc_info=True)
        finally:
            try:
                self.after(TICK_MS, self._auto_tick)
            except tk.TclError:
                pass

    def _auto_run(self):
        if not db.db_accounts():
            self.settings["auto_sync_last"] = time.time()
            self._save_settings()
            return
        if not self._ready():
            return
        self.settings["auto_sync_last"] = time.time()
        self._save_settings()
        self._append_log(LEVEL_SYS, tr('[Auto] Scheduled sync started.'))
        self._set_busy(True)
        threading.Thread(target=self.engine.task_sync_all, kwargs={"auto": True},
                         daemon=True).start()
