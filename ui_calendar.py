"""Календарь выбора даты и режим диапазона.

Часть интерфейса TokSaver: миксин для класса App (ui.py). Методы перенесены
из прежнего ui.py без изменений."""
from i18n import tr
import calendar as calmod
import datetime
import tkinter as tk

import customtkinter as ctk

import applog
from engine import parse_date
from ui_theme import RADIUS
from ui_theme import BTN_H

_LOG = applog.get(__name__)


class CalendarMixin:
    def _on_range_mode(self, _choice=None):
        """Показываем только нужные поля диапазона (число или две даты)."""
        self._hide_calendar()
        mode = self.range_mode.get()
        if mode == tr('Latest'):
            self.period_row.pack_forget()
            self.count_row.pack(fill="x", pady=(8, 0), before=self.range_hint)
        elif mode == tr('Period'):
            self.count_row.pack_forget()
            self.period_row.pack(fill="x", pady=(8, 0), before=self.range_hint)
        else:
            self.count_row.pack_forget()
            self.period_row.pack_forget()

    _CAL_MONTHS = [tr('January'), tr('February'), tr('March'), tr('April'), tr('May'), tr('June'), tr('July'),
                   tr('August'), tr('September'), tr('October'), tr('November'), tr('December')]

    _CAL_WD = [tr('Mo'), tr('Tu'), tr('We'), tr('Th'), tr('Fr'), tr('Sa'), tr('Su')]

    def _date_field(self, parent, label, placeholder):
        """Строка «с [поле] [Календарь]»: поле даты + кнопка вызова календарика."""
        row = tk.Frame(parent, bg=self.T["side"], bd=0, highlightthickness=0)
        row.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=13),
                     text_color=self.T["muted"], width=18).pack(side="left")
        entry = ctk.CTkEntry(row, width=100, height=BTN_H, corner_radius=RADIUS,
                             placeholder_text=placeholder,
                             fg_color=self.T["field"], border_color=self.T["line"],
                             text_color=self.T["text"], justify="center")
        entry.pack(side="left", padx=(2, 0))
        ctk.CTkButton(row, text=tr('Calendar'), width=self._btn_w(tr('Calendar'), 12, min_w=84),
                      height=BTN_H, corner_radius=RADIUS,
                      fg_color=self.T["field"], hover_color=self.T["card_h"],
                      text_color=self.T["text"], border_width=1,
                      border_color=self.T["line"], font=ctk.CTkFont(size=12),
                      command=lambda: self._toggle_calendar(entry)).pack(side="left", padx=(4, 0))
        self._attach_edit_keys(entry)
        return entry

    def _toggle_calendar(self, entry):
        """Кнопка «Календарь»: открыть панель для этого поля, а если она уже
        открыта для него же — спрятать."""
        if self._cal_panel is not None and self._cal_entry is entry \
                and self._cal_panel.winfo_ismapped():
            self._hide_calendar()
            return
        self._open_calendar(entry)

    def _hide_calendar(self):
        if self._cal_panel is not None:
            try:
                self._cal_panel.pack_forget()
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass
        self._cal_entry = None

    def _open_calendar(self, entry):
        if self._cal_panel is None:
            self._build_calendar_panel()
        self._cal_entry = entry
        cur = parse_date(entry.get())
        try:
            sel = (datetime.date(int(cur[:4]), int(cur[4:6]), int(cur[6:]))
                   if cur else datetime.date.today())
        except ValueError:
            sel = datetime.date.today()
        self._cal_state = {"y": sel.year, "m": sel.month, "sel": sel}
        self._cal_redraw()
        # панель появляется между строками периода и подсказкой
        self._cal_panel.pack(fill="x", pady=(8, 0), before=self.range_hint)

    def _build_calendar_panel(self):
        T = self.T
        p = tk.Frame(self.sidebar_inner, bg=T["card"], padx=10, pady=8,
                     highlightthickness=1, highlightbackground=T["line"])
        self._cal_panel = p

        nav = tk.Frame(p, bg=T["card"])
        nav.pack(fill="x")
        tk.Button(nav, text="‹", width=3, bd=0, relief="flat", cursor="hand2",
                  bg=T["card_h"], fg=T["text"], activebackground=T["accent_h"],
                  activeforeground=T["on_accent"], font=(self._font_family, 11, "bold"),
                  command=lambda: self._cal_shift(-1)).pack(side="left", pady=(0, 2))
        tk.Button(nav, text="›", width=3, bd=0, relief="flat", cursor="hand2",
                  bg=T["card_h"], fg=T["text"], activebackground=T["accent_h"],
                  activeforeground=T["on_accent"], font=(self._font_family, 11, "bold"),
                  command=lambda: self._cal_shift(1)).pack(side="right", pady=(0, 2))
        self._cal_title = tk.Label(nav, bg=T["card"], fg=T["text"],
                                   font=(self._font_family, 12, "bold"))
        self._cal_title.pack(side="left", expand=True, fill="x")

        self._cal_grid = tk.Frame(p, bg=T["card"])
        self._cal_grid.pack()

        bottom = tk.Frame(p, bg=T["card"])
        bottom.pack(fill="x", pady=(6, 0))
        tk.Button(bottom, text=tr('Today'), bd=0, relief="flat", cursor="hand2",
                  bg=T["card_h"], fg=T["accent"], activebackground=T["accent_h"],
                  activeforeground=T["on_accent"], font=(self._font_family, 11),
                  command=lambda: self._cal_pick(datetime.date.today())).pack(side="left")
        tk.Button(bottom, text=tr('Close'), bd=0, relief="flat", cursor="hand2",
                  bg=T["card_h"], fg=T["muted"], activebackground=T["accent_h"],
                  activeforeground=T["on_accent"], font=(self._font_family, 11, "bold"),
                  command=self._hide_calendar).pack(side="right")
        tk.Button(bottom, text=tr('Clear'), bd=0, relief="flat", cursor="hand2",
                  bg=T["card_h"], fg=T["muted"], activebackground=T["accent_h"],
                  activeforeground=T["on_accent"], font=(self._font_family, 11),
                  command=lambda: (self._cal_entry.delete(0, "end")
                                   if self._cal_entry else None,
                                   self._hide_calendar())).pack(side="right", padx=(0, 6))

    def _cal_shift(self, dx):
        st = self._cal_state
        m = st["m"] + dx
        if m < 1:
            m, st["y"] = 12, st["y"] - 1
        elif m > 12:
            m, st["y"] = 1, st["y"] + 1
        st["m"] = m
        self._cal_redraw()

    def _cal_pick(self, dt):
        if self._cal_entry is not None:
            self._cal_entry.delete(0, "end")
            self._cal_entry.insert(0, dt.strftime("%d.%m.%Y"))
        self._hide_calendar()

    def _cal_redraw(self):
        T = self.T
        st = self._cal_state
        y, m = st["y"], st["m"]
        self._cal_title.configure(text=f"{self._CAL_MONTHS[m - 1]} {y}")
        for w in self._cal_grid.winfo_children():
            w.destroy()
        for i, wd in enumerate(self._CAL_WD):
            tk.Label(self._cal_grid, text=wd, bg=T["card"], fg=T["muted"], width=3,
                     padx=0, font=(self._font_family, 10, "bold")).grid(row=0, column=i)
        first_wd = datetime.date(y, m, 1).weekday()      # 0 = понедельник
        ndays = calmod.monthrange(y, m)[1]
        today = datetime.date.today()
        for d in range(ndays):
            r, c = divmod(first_wd + d, 7)
            dt = datetime.date(y, m, d + 1)
            is_sel, is_today = dt == st["sel"], dt == today
            tk.Button(self._cal_grid, text=str(d + 1), width=3, bd=0, relief="flat",
                      cursor="hand2", padx=0, pady=0,
                      bg=T["accent"] if is_sel else T["card_h"],
                      fg=T["on_accent"] if is_sel else (T["accent"] if is_today else T["text"]),
                      activebackground=T["accent_h"], activeforeground=T["on_accent"],
                      font=(self._font_family, 11,
                            "bold" if is_sel or is_today else "normal"),
                      command=lambda dd=dt: self._cal_pick(dd)).grid(row=r + 1, column=c,
                                                                     padx=0, pady=1)
