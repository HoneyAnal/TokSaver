"""Действия пользователя: кнопки, меню карточек, cookies, ByeDPI, закрытие.

Часть интерфейса TokSaver: миксин для класса App (ui.py). Методы перенесены
из прежнего ui.py без изменений."""
from i18n import tr
import os
import subprocess
import sys
import threading
from tkinter import filedialog
from tkinter import messagebox
from tkinter import simpledialog

import customtkinter as ctk

import applog
import db
import sorting
import paths
from engine import LEVEL_ERROR
from engine import LEVEL_INFO
from engine import LEVEL_OK
from engine import LEVEL_WARN
from engine import fmt_date
from engine import parse_date
from ui_theme import RADIUS
from ui_widgets import hide_until_built, show_built
from ui_theme import BTN_H

_LOG = applog.get(__name__)


class ActionsMixin:
    # ------------------------------ действия ------------------------------
    def _collect_range(self):
        """Собирает диапазон из левой панели. None — ошибка (написана в журнал).
        Защиты: непонятная дата — ошибка, а не молчаливый пропуск поля;
        «с» позже «по» — ошибка; «Последние» ограничено 1..500."""
        mode = self.range_mode.get()
        if mode == tr('Latest'):
            try:
                n = int(self.count_entry.get().strip())
            except ValueError:
                n = 0
            if n <= 0:
                self._append_log(LEVEL_ERROR, tr('[Range] Enter the number of posts (for example 50).'))
                return None
            if n > 500:
                self._append_log(LEVEL_WARN,
                                 tr('[Range] The limit is 500 posts at a time (was {0}).', n))
                n = 500
            return {"kind": "tail", "limit_count": n, "date_from": None, "date_to": None}
        if mode == tr('Period'):
            raw_df = self.date_from_entry.get().strip()
            raw_dt = self.date_to_entry.get().strip()
            df = parse_date(raw_df) if raw_df else None
            dt = parse_date(raw_dt) if raw_dt else None
            if raw_df and not df:
                self._append_log(LEVEL_ERROR,
                                 tr('[Range] Could not understand the “from” date — “{0}”. Format: 01.01.2024 or 2024-01-01.', raw_df))
                return None
            if raw_dt and not dt:
                self._append_log(LEVEL_ERROR,
                                 tr('[Range] Could not understand the “to” date — “{0}”. Format: 01.01.2024 or 2024-01-01.', raw_dt))
                return None
            if not df and not dt:
                self._append_log(LEVEL_ERROR,
                                 tr('[Range] Enter dates like 01.01.2024 or 2024-01-01.'))
                return None
            if df and dt and df > dt:
                self._append_log(LEVEL_ERROR,
                                 tr('[Range] The “from” date is later than the “to” date — swap them.'))
                return None
            return {"kind": "period", "limit_count": None, "date_from": df, "date_to": dt}
        return {"kind": "all", "limit_count": None, "date_from": None, "date_to": None}

    def on_add(self):
        if not self._ready():
            return
        target = self.add_entry.get().strip()
        if not target:
            return
        rng = self._collect_range()
        if rng is None:
            return
        self._set_busy(True)
        threading.Thread(target=self.engine.task_add_account, args=(target, rng),
                         daemon=True).start()

    def on_single(self):
        if not self._ready():
            return
        target = self.single_entry.get().strip()
        if not target:
            return
        self._set_busy(True)
        threading.Thread(target=self.engine.task_single_video, args=(target,),
                         daemon=True).start()

    def on_sync_one(self, acc_id):
        if not self._ready():
            return
        self._set_busy(True)
        threading.Thread(target=self.engine.task_sync_one, args=(acc_id,), daemon=True).start()

    def on_sync_all(self):
        if not self._ready():
            return
        if not db.db_accounts():
            self._append_log(LEVEL_INFO, tr('[Database] The account list is empty — add at least one.'))
            return
        self._set_busy(True)
        threading.Thread(target=self.engine.task_sync_all, daemon=True).start()

    def on_cancel_all(self):
        """«Остановить всех»: останавливает все идущие и ожидающие задачи;
        очереди аккаунтов уходят в «пропущенные» (вернуть можно через
        «Скачать пропущенное»)."""
        if not self._busy:
            self._toast(tr('Nothing is downloading right now'), "info")
            return
        self.engine.request_cancel_all()
        self._append_log(LEVEL_INFO, tr('[Database] Stop all: will stop after the current posts, queues will be cleared.'))
        self._toast(tr('Stopping all tasks…'), "info")

    def on_pause(self, acc_id):
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        was_paused = bool(acc["paused"])
        db.db_set_paused(acc_id, not was_paused)
        msg = tr('resumed.') if was_paused else tr('paused.')
        self._append_log(LEVEL_INFO, tr('[Database] “{0}”: {1}', acc['nickname'], msg))
        self._toast(f"@{acc['nickname']}: {msg[:-1]}", "info")
        # точечное обновление карточки — без пересборки (не рвём идущие задачи)
        card = self._cards.get(acc_id)
        if card:
            self._update_card_stats(acc_id)
        if was_paused:
            # «Пуск» = продолжить: сразу докачиваем остаток очереди
            _t, _d, pend, _s = db.db_counts(acc_id)
            if self._busy:
                self._append_log(LEVEL_INFO,
                                 tr('[Database] “{0}”: resumed — will download on the next sync (another task is running now).', acc['nickname']))
            elif pend and self._ready():
                self._append_log(LEVEL_INFO,
                                 tr('[Database] “{0}”: continuing — {1} posts in the queue.', acc['nickname'], pend))
                self._set_busy(True)
                threading.Thread(target=self.engine.task_sync_one, args=(acc_id,),
                                 daemon=True).start()

    def on_delete(self, acc_id):
        if self._busy:
            return
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        # Да      — удалить аккаунт вместе с его папкой (база постов + все файлы);
        # Нет     — убрать только из списка, папка останется на диске;
        # Отмена  — ничего не делать.
        ans = messagebox.askyesnocancel(
            tr('Delete account'),
            tr('Account: @{0}\nFolder: {1}\n\n“Yes” — delete WITH the folder (database + downloaded files).\n“No” — remove from the list only, keep the folder.', acc['nickname'], db.account_dir(acc) or '?'),
            parent=self)
        if ans is None:
            return
        if ans:
            ok, folder, err = db.db_delete_account(acc_id, remove_files=True)
            if err:
                self._append_log(LEVEL_WARN, tr('[Database] “{0}” removed from the list, but {1}.', acc['nickname'], err))
            else:
                self._append_log(LEVEL_OK, tr('[Database] “{0}” removed together with the folder {1}.', acc['nickname'], os.path.basename(folder or '')))
        else:
            ok, folder, err = db.db_delete_account(acc_id, remove_files=False)
            self._append_log(LEVEL_INFO, tr('[Database] “{0}” removed from the list. The folder remains on disk: {1}', acc['nickname'], folder))
        self._rebuild_accounts()

    def on_note(self, acc_id):
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        note = simpledialog.askstring(tr('Note'), tr('Note for @{0}:', acc['nickname']),
                                      initialvalue=acc["note"] or "", parent=self)
        if note is None:
            return
        db.db_set_note(acc_id, note.strip())
        self._rebuild_accounts()

    def on_edit_range(self, acc_id):
        """Диалог «Изменить диапазон…»: диапазон — ПОСТОЯННАЯ задача аккаунта.
        Действует при каждой синхронизации, пока не изменят: «Последние N»
        держат нижнюю границу (старьё не тянется), «Период» — обе границы
        (вне периода не качается ни старьё, ни посты новее даты «по»),
        «Все» снимает фильтры. Подтянуть посты вне диапазона можно только
        явно: «Скачать пропущенное» / «Докачать весь профиль»."""
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        T = self.T
        win = ctk.CTkToplevel(self, fg_color=T["bg"])
        hide_until_built(win)                       # покажем, когда всё построено
        win.after(60, lambda: show_built(win))
        win.title(tr('Range @{0} — TokSaver', acc['nickname']))
        win.geometry("470x420")
        win.resizable(False, False)
        win.transient(self)
        win.after(150, lambda: (win.lift(), win.focus_force()))

        ctk.CTkLabel(win, text=tr('Range for @{0}', acc['nickname']),
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=T["text"]).pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(win, text=tr('Applies on every sync until you change it.'),
                     font=ctk.CTkFont(size=11), text_color=T["muted"]).pack(
            anchor="w", padx=18, pady=(0, 10))

        kind_map = {tr('All'): "all", tr('Latest'): "tail", tr('Period'): "period"}
        inv_map = {v: k for k, v in kind_map.items()}
        mode = ctk.StringVar(value=inv_map.get(acc.get("range_kind") or "all", tr('All')))

        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="x", padx=18)
        ctk.CTkSegmentedButton(
            body, values=[tr('All'), tr('Latest'), tr('Period')], variable=mode,
            command=lambda _v: _show_rows(), height=BTN_H, corner_radius=RADIUS,
            font=ctk.CTkFont(size=12), fg_color=T["field"], selected_color=T["accent"],
            selected_hover_color=T["accent_h"], unselected_color=T["field"],
            unselected_hover_color=T["card_h"], text_color=T["text"]).pack(fill="x")

        count_row = ctk.CTkFrame(body, fg_color="transparent")
        count_entry = ctk.CTkEntry(count_row, width=90, height=BTN_H, corner_radius=RADIUS,
                                   fg_color=T["field"], border_color=T["line"],
                                   text_color=T["text"], justify="center")
        count_entry.insert(0, str(acc.get("limit_count") or 50))
        count_entry.pack(side="left")
        self._attach_edit_keys(count_entry)
        ctk.CTkLabel(count_row, text=tr('latest posts (1…500)'),
                     font=ctk.CTkFont(size=12), text_color=T["muted"]).pack(
            side="left", padx=8)

        period_row = ctk.CTkFrame(body, fg_color="transparent")
        prow1 = ctk.CTkFrame(period_row, fg_color="transparent")
        prow1.pack(anchor="w", pady=(4, 0))
        ctk.CTkLabel(prow1, text=tr('from'), font=ctk.CTkFont(size=12),
                     text_color=T["muted"], width=20).pack(side="left")
        df_entry = ctk.CTkEntry(prow1, width=124, height=BTN_H, corner_radius=RADIUS,
                                fg_color=T["field"], border_color=T["line"],
                                text_color=T["text"], justify="center",
                                placeholder_text="01.01.2024")
        if acc.get("date_from"):
            df_entry.insert(0, fmt_date(acc["date_from"]))
        df_entry.pack(side="left", padx=(2, 0))
        self._attach_edit_keys(df_entry)
        prow2 = ctk.CTkFrame(period_row, fg_color="transparent")
        prow2.pack(anchor="w", pady=(4, 0))
        ctk.CTkLabel(prow2, text=tr('to'), font=ctk.CTkFont(size=12),
                     text_color=T["muted"], width=20).pack(side="left")
        dt_entry = ctk.CTkEntry(prow2, width=124, height=BTN_H, corner_radius=RADIUS,
                                fg_color=T["field"], border_color=T["line"],
                                text_color=T["text"], justify="center",
                                placeholder_text="31.12.2024")
        if acc.get("date_to"):
            dt_entry.insert(0, fmt_date(acc["date_to"]))
        dt_entry.pack(side="left", padx=(2, 0))
        self._attach_edit_keys(dt_entry)

        hint = ctk.CTkLabel(body, text="", font=ctk.CTkFont(size=11),
                            text_color=T["muted"], justify="left")
        hint.pack(anchor="w", pady=(8, 0))
        err_lbl = ctk.CTkLabel(win, text="", font=ctk.CTkFont(size=11),
                               text_color=T["danger"], justify="left")

        _HINTS = {
            "tail": tr('Download only the latest N posts; older ones will not\nbe downloaded on their own (to fetch them: “Download whole profile”).'),
            "period": tr('Download posts within a period. Both bounds are optional:\nempty “from” = start of the profile, empty “to” = up to today.'),
            "all": tr('Download everything new. Old posts — only via\n“Download whole profile” in the card menu.'),
        }

        def _show_rows():
            k = kind_map.get(mode.get(), "all")
            count_row.pack_forget()
            period_row.pack_forget()
            hint.configure(text=_HINTS.get(k, ""))
            err_lbl.configure(text="")
            if k == "tail":
                count_row.pack(anchor="w", pady=(10, 0), before=hint)
            elif k == "period":
                period_row.pack(anchor="w", pady=(10, 0), before=hint)

        def _save():
            k = kind_map.get(mode.get(), "all")
            if k == "tail":
                try:
                    n = int(count_entry.get().strip())
                except ValueError:
                    n = 0
                if n <= 0 or n > 500:
                    err_lbl.configure(text=tr('The number of posts must be from 1 to 500.'))
                    return
                db.db_set_range(acc_id, "tail", limit_count=n)
                txt = tr('latest {0}', n)
            elif k == "period":
                raw_df, raw_dt = df_entry.get().strip(), dt_entry.get().strip()
                df = parse_date(raw_df) if raw_df else None
                dt = parse_date(raw_dt) if raw_dt else None
                if (raw_df and not df) or (raw_dt and not dt):
                    err_lbl.configure(text=tr('Date format: 01.01.2024 or 2024-01-01.'))
                    return
                if not df and not dt:
                    err_lbl.configure(text=tr('Enter at least one date (or choose “All”).'))
                    return
                if df and dt and df > dt:
                    err_lbl.configure(text=tr('The “from” date is later than the “to” date — swap them.'))
                    return
                db.db_set_range(acc_id, "period", date_from=df, date_to=dt)
                txt = f"{fmt_date(df) if df else tr('beginning')} — {fmt_date(dt) if dt else tr('today')}"
            else:
                db.db_set_range(acc_id, "all")
                txt = tr('all posts')
            self._append_log(LEVEL_OK, tr('[Database] “{0}”: range is now — {1}. Applies on the next sync.', acc['nickname'], txt))
            self._toast(tr('@{0}: range — {1}', acc['nickname'], txt), "ok")
            self._rebuild_accounts()
            win.destroy()

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=18, pady=(10, 16))
        ctk.CTkButton(btn_row, text=tr('Save'),
                      width=self._btn_w(tr('Save'), 13, bold=True, min_w=124),
                      height=38, corner_radius=RADIUS, font=ctk.CTkFont(size=13, weight="bold"),
                      fg_color=T["accent"], hover_color=T["accent_h"],
                      text_color=T["on_accent"], command=_save).pack(side="right")
        ctk.CTkButton(btn_row, text=tr('Cancel'), width=self._btn_w(tr('Cancel'), 13, min_w=94),
                      height=38, corner_radius=RADIUS, fg_color=T["field"],
                      hover_color=T["card_h"], text_color=T["text"],
                      border_width=1, border_color=T["line"],
                      command=win.destroy).pack(side="right", padx=(0, 8))
        err_lbl.pack(side="bottom", anchor="w", padx=18, pady=(0, 6))
        _show_rows()

    def on_copy_link(self, acc_id):
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        url = f"https://www.tiktok.com/@{acc['nickname']}"
        self.clipboard_clear()
        self.clipboard_append(url)
        self._append_log(LEVEL_INFO, tr('[Database] Link copied: {0}', url))
        self._toast(tr('Profile link copied'), "info")

    def on_reset_skipped(self, acc_id):
        """«Скачать пропущенное»: возвращает вне-диапазонные посты в очередь
        и СРАЗУ качает их — раньше возвращённые посты снова помечались
        пропущенными при следующей синхронизации, и кнопка была бесполезной."""
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        n = db.db_reset_skipped(acc_id)
        if not n:
            self._append_log(LEVEL_INFO, tr('[Database] “{0}”: no skipped posts.', acc['nickname']))
            return
        self._update_card_stats(acc_id)
        if self._busy:
            self._append_log(LEVEL_OK, tr('[Database] “{0}”: {1} posts returned to the queue. Another task is running now — press “Sync” later.', acc['nickname'], n))
            self._toast(tr('@{0}: {1} posts waiting in the queue', acc['nickname'], n), "ok")
            return
        self._append_log(LEVEL_OK, tr('[Database] “{0}”: {1} posts returned to the queue — downloading…', acc['nickname'], n))
        self._toast(tr('@{0}: downloading {1} returned posts', acc['nickname'], n), "ok")
        self._set_busy(True)
        threading.Thread(target=self.engine.task_sync_one, args=(acc_id,), daemon=True).start()

    def on_backfill(self, acc_id):
        """«Докачать весь профиль» (контекстное меню): полный список постов,
        всё нескачанное — в очередь и в скачивание. Явное действие вместо
        молчаливой докачки старья при обычной синхронизации."""
        if not self._ready():
            return
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        if acc["paused"]:
            self._toast(tr('@{0}: account is paused — press “Resume” first', acc['nickname']), "info")
            return
        self._set_busy(True)
        threading.Thread(target=self.engine.task_backfill, args=(acc_id,), daemon=True).start()

    def on_verify_files(self, acc_id):
        """«Проверить файлы и докачать» (контекстное меню): сверить базу
        с media/, отсутствующее — в очередь и скачивание."""
        if not self._ready():
            return
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        if acc["paused"]:
            self._toast(tr('@{0}: account is paused — press “Resume” first', acc['nickname']), "info")
            return
        self._set_busy(True)
        threading.Thread(target=self.engine.task_verify_files, args=(acc_id,),
                         daemon=True).start()

    def on_cancel(self, acc_id):
        """«Стоп» рядом с прогрессом: остановить и ОЧИСТИТЬ очередь аккаунта.
        Отличие от «Пауза»: пауза сохраняет очередь («Пуск» докачает), стоп —
        убирает её в «пропущенные» (вернуть: ПКМ → «Скачать пропущенное»)."""
        if not self._busy:
            self._toast(tr('Nothing is downloading right now'), "info")
            return
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        self.engine.request_cancel(acc_id)
        _t, _d, pend, _s = db.db_counts(acc_id)
        self._append_log(LEVEL_INFO,
                         tr('[Database] “{0}”: stop — will stop after the current post, {1} posts will be removed from the queue.', acc['nickname'], pend))
        self._toast(tr('@{0}: stop — the queue will be cleared', acc['nickname']), "info")

    def on_open_account(self, acc_id):
        acc = db.db_get_account(acc_id)
        if acc:
            self._open_folder(db.account_dir(acc))

    def on_open_data(self):
        self._open_folder(paths.DATA_DIR)

    def on_pick_cookies_file(self):
        path = filedialog.askopenfilename(
            parent=self, title=tr('Choose cookies.txt'),
            filetypes=[("cookies.txt", "*.txt"), (tr('All files'), "*.*")])
        if not path:
            return
        if not self.engine.set_cookies_file(path):      # проверка файла и права доступа
            return
        self.cookies_file_path = path
        self._remember_cookies()
        name = os.path.basename(path)
        self.cookies_file_lbl.configure(text="…" + name[-38:] if len(name) > 39 else name,
                                        text_color=self.T["ok"])
        self._append_log(LEVEL_OK, tr('[Auth] cookies.txt chosen: {0}', name))

    def on_clear_cookies_file(self):
        self.cookies_file_path = None
        self.engine.set_cookies_file(None)
        self._remember_cookies()
        self.cookies_file_lbl.configure(text=tr('no file selected'), text_color=self.T["muted"])

    def on_open_downloads(self):
        self._open_folder(paths.OUTPUT_DIR)

    def _open_folder(self, path):
        os.makedirs(path, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self._append_log(LEVEL_ERROR, tr('[System] Could not open the folder: {0}', e))

    def _open_path(self, path):
        """Открыть ФАЙЛ системным плеером/просмотрщиком (папки — _open_folder)."""
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self._append_log(LEVEL_ERROR, tr('[System] Could not open the file: {0}', e))

    # ------------------------------ настройки ------------------------------
    def _on_cookies_changed(self, choice):
        """Пользователь выбрал браузер для cookies. Это даёт доступ ко всем cookies
        браузера, поэтому сначала просим явное подтверждение."""
        if choice == tr('none'):
            self.engine.cookies = ""
            self._remember_cookies()
            self._append_log(LEVEL_INFO, tr('[Auth] Cookies disabled — working without login.'))
            return
        ok = messagebox.askokcancel(
            tr('Browser cookie access'),
            tr('The app will read ALL cookies of the browser “{0}”: sessions of mail, banks and social networks, not only TikTok.\n\nIt is safer to export a cookies.txt file for tiktok.com only and choose it with the button above.\n\nUse the browser anyway?', choice),
            icon="warning", parent=self)
        if not ok:
            self.cookies_menu.set(tr('none'))
            self.engine.cookies = ""
            self._remember_cookies()
            self._append_log(LEVEL_INFO, tr('[Auth] Browser cookies are not enabled.'))
            return
        self.engine.cookies = choice
        self._remember_cookies()
        self.engine.cookies_file = self.cookies_file_path
        self._append_log(LEVEL_OK, tr('[Auth] Cookies are taken from {0} (the profile where you are logged in to TikTok).', choice))
        if self.cookies_file_path:
            self._append_log(LEVEL_WARN, tr('[Auth] A cookies.txt file is also chosen — it has priority, the browser will not be used.'))
        else:
            self._append_log(LEVEL_INFO, tr('[Auth] Did you forget to press “Sync” on the card?'))

    def _on_accounts_sort(self, label):
        """Сортировка списка аккаунтов: запоминается и применяется сразу."""
        self.settings["accounts_sort"] = sorting.key_for(sorting.ACCOUNT_SORTS, label)
        self._save_settings()
        self._rebuild_accounts()

    def _on_parallel_changed(self, choice):
        """Число параллельных загрузок: сохраняется в настройках и действует
        со следующей очереди."""
        self.settings["parallel_downloads"] = int(choice)
        self._save_settings()
        self._append_log(LEVEL_INFO, tr('[System] Parallel downloads: {0}.', choice)
                         + (tr(' TikTok may limit frequent requests — the app will then pause by itself.') if int(choice) > 1 else ""))

    def _toggle_byedpi(self):
        self.engine.use_byedpi = bool(self.byedpi_var.get())
        if self.engine.use_byedpi:
            self.engine.start_byedpi()
            self._append_log(LEVEL_INFO, tr('[System] ByeDPI enabled.'))
        else:
            self.engine.stop_byedpi()
            self._append_log(LEVEL_INFO, tr('[System] ByeDPI disabled — working directly.'))

    def _update_deps(self, d):
        self._deps_last = d
        T = self.T
        if d.get("proxy"):
            txt, color = tr('SOCKS 127.0.0.1:{0} — working', self.engine.proxy_port), T["ok"]
        elif paths.IS_WIN:
            txt, color = tr('unavailable — working directly'), T["muted"]
        else:
            txt, color = tr('not needed on this OS'), T["muted"]
        try:
            self.proxy_status.configure(text=txt, text_color=color)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass
        # Как только поднялся обходчик — у аватаров и обложек, сорвавшихся на
        # старте (карточки запрашивают их раньше, чем ByeDPI успевает
        # запуститься, и у заблокированных провайдером попытки падают),
        # появляется второй шанс: чистим «сбойные» метки и просим заново.
        alive = bool(d.get("proxy"))
        if alive and not self._proxy_was_alive:
            self._avatar_failed.clear()
            self._thumb_failed.clear()
            for acc_id in list(self._cards):
                if acc_id not in self._avatar_img:
                    self._maybe_fetch_avatar(acc_id)
                if acc_id not in self._thumb_img:
                    self._maybe_fetch_thumb(acc_id)
        self._proxy_was_alive = alive

    # ------------------------------ закрытие ------------------------------
    def on_close(self, restart=False):
        self._hide_calendar()
        self._close_posts_window()
        self._save_settings()
        self.engine.shutdown()          # ByeDPI и все дочерние yt-dlp/gallery-dl
        self.destroy()
        if restart:
            try:
                subprocess.Popen(([sys.executable] + sys.argv[1:]) if paths.FROZEN
                                 else ([sys.executable] + sys.argv), cwd=paths.APP_DIR)
            except OSError as _exc:
                _LOG.warning('restart failed: %r', _exc)
