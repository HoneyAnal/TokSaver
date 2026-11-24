"""Журнал событий и всплывающие уведомления.

Часть интерфейса TokSaver: миксин для класса App (ui.py). Методы перенесены
из прежнего ui.py без изменений."""
from i18n import tr
import time
import tkinter as tk


import applog

_LOG = applog.get(__name__)


LOG_TAGS = ("info", "warn", "error", "ok", "head", "dl", "sys", "auth", "sum", "dim")


class LogMixin:
    def _log_tag_colors(self):
        T = self.T
        return {"info": T["info"], "warn": T["warn"], "error": T["danger"], "ok": T["ok"],
                "head": T["head"], "dl": T["log_dl"], "sys": T["log_sys"],
                "auth": T["log_auth"], "sum": T["log_sum"], "dim": T["log_dim"]}

    @staticmethod
    def _log_tag(level, line):
        """Тег оформления строки: уровень, а «подробности» (отступ из 4 пробелов
        после времени) приглушаются, чтобы главное выделялось."""
        if level == "info":
            body = line.split("] ", 1)[-1] if line.startswith("[") else line
            if body.startswith("    "):
                return "dim"
        return level if level in LOG_TAGS else "info"

    # ------------------------------ журнал ------------------------------
    def _append_logs(self, items):
        """Пачка строк за один раз: configure/see — по одному на всю пачку.
        В буфер пишем уже отформатированный текст — он переживает смену темы."""
        if not items:
            return
        stamp = time.strftime("%H:%M")
        for level, msg in items:
            self._log_buffer.append((level, f"[{stamp}] {msg}"))
        if len(self._log_buffer) > 800:
            del self._log_buffer[:len(self._log_buffer) - 800]
        try:
            self.logbox.configure(state="normal")
            for level, line in self._log_buffer[-len(items):]:
                self.logbox.insert("end", line + "\n", self._log_tag(level, line))
            # окно журнала не должно расти бесконечно: Text с тысячами строк
            # тормозит вставку и прокрутку — держим не больше ~800 строк
            self._log_lines = getattr(self, "_log_lines", 0) + len(items)
            if self._log_lines > 1000:
                self.logbox.delete("1.0", f"{self._log_lines - 800 + 1}.0")
                self._log_lines = 800
            self.logbox.configure(state="disabled")
            self.logbox.see("end")
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
        self._update_log_count()

    def _append_log(self, level, msg):
        self._append_logs([(level, msg)])

    def _refill_log(self):
        """После смены темы: пересозданные tag-цвета + текст из буфера."""
        for lvl, color in self._log_tag_colors().items():
            try:
                self.logbox.tag_config(lvl, foreground=color)
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                self.logbox._textbox.tag_config(lvl, foreground=color)
        try:
            self.logbox.configure(state="normal")
            self.logbox.delete("1.0", "end")
            self._log_lines = len(self._log_buffer)
            for level, line in self._log_buffer:
                self.logbox.insert("end", line + "\n", self._log_tag(level, line))
            self.logbox.configure(state="disabled")
            self.logbox.see("end")
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass

    def _update_log_count(self):
        try:
            self.log_count_lbl.configure(text=tr('· {0} events', len(self._log_buffer)))
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass

    def _toggle_log(self):
        self._log_open = not self._log_open
        self.settings["log_open"] = self._log_open
        self.settings["log_v2"] = 1        # настройка свернутости больше не устаревшая
        self._save_settings()
        if self._log_open:
            self._log_toggle_btn.configure(text=tr('Hide log'))
            self.log_body.pack(fill="x")
            try:
                self.logbox.see("end")
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass
        else:
            self._log_toggle_btn.configure(text=tr('Show log'))
            self.log_body.pack_forget()

    def _clear_log(self):
        self._log_buffer.clear()
        self._log_lines = 0
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")
        self._update_log_count()

    # ------------------------------ тосты ------------------------------
    def _toast(self, text, kind="info"):
        """Всплывашка в правом нижнем углу: «скачано», ошибки и пр.
        Живёт 4.2 секунды, кликом снимается раньше. Собрана из обычных
        tk-виджетов с явными размерами (ширина 250–430, высота по тексту
        от 46px) — ровная горизонтальная плашка. Кладётся place() прямо на
        окно: общий контейнер оставлял серое пятно (см. _build), а через
        личный отступ стопка сама перестраивается после закрытия любого
        из тостов."""
        T = self.T
        color = {"ok": T["accent"], "error": T["danger"], "warn": T["warn"]}.get(
            kind, T["muted"])
        try:
            box = tk.Frame(self, bg=T["card"], bd=0,
                           highlightthickness=1, highlightbackground=T["line"])
            bar = tk.Frame(box, width=5, bg=color)
            lbl = tk.Label(box, text=str(text), justify="left", wraplength=380,
                           bg=T["card"], fg=T["text"], font=(self._font_family, 11))
            bar.pack(side="left", fill="y", padx=(10, 0), pady=10)
            lbl.pack(side="left", fill="both", expand=True, padx=(10, 14), pady=10)
            box.update_idletasks()
            w = min(430, max(250, box.winfo_reqwidth()))
            h = min(120, max(46, box.winfo_reqheight()))
            box.pack_propagate(False)
            box.configure(width=w, height=h)
            rec = {"frame": box, "after": None}
            self._toasts.append(rec)
            rec["after"] = box.after(4200, lambda: self._toast_close(rec))

            def _click(_e=None):
                self._toast_close(rec)

            for x in (box, bar, lbl):
                x.bind("<Button-1>", _click)
            self._toast_relayout()
            for old in self._toasts[:-4]:      # больше 4 тостов — старшие гасим
                self._toast_close(old)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass

    def _toast_relayout(self):
        """Разложить живые тосты стопкой от правого нижнего угла: новенький
        снизу, над ним — старшие. Заодно подчищаем записи о тостах, которые
        уничтожила смена темы (пересборка окна)."""
        self._toasts = [r for r in self._toasts if r["frame"].winfo_exists()]
        y = 18
        for rec in reversed(self._toasts):
            box = rec["frame"]
            box.place(relx=1.0, rely=1.0, x=-18, y=-y, anchor="se")
            box.lift()
            y += max(box.winfo_reqheight(), 46) + 8

    def _toast_close(self, rec):
        """Снять тост: отменить таймер, уничтожить плашку и переложить
        оставшиеся, чтобы внизу не было дыры."""
        try:
            if rec.get("after"):
                rec["frame"].after_cancel(rec["after"])
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
        rec["after"] = None
        try:
            if rec["frame"].winfo_exists():
                rec["frame"].destroy()
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
        if rec in self._toasts:
            self._toasts.remove(rec)
        self._toast_relayout()
