"""Интерфейс TokSaver (customtkinter): две темы — тёмная и светлая.

Флэт-стиль: прямые углы, оранжевый акцент, обычные текстовые кнопки без
значков. Панели и карточки отделены от фона заметной обводкой и жёсткой
тенью (тёмная кромка справа и снизу), сайдбар отделён вертикальной линией —
ничего не сливается. Ширина кнопок считается по реальной ширине текста,
поэтому надписи не обрезаются ни при каком масштабе экрана. Слева — сайдбар
(добавление аккаунта с диапазоном, разовое видео, настройки, авторизация),
справа — крупные карточки аккаунтов: обложка последнего видео (пока её нет —
иконка аккаунта, вписанная в кружок; когда аватар скачан — в кружке
настоящее фото профиля, обрезанное по кругу), статистика, прогресс
скачивания, всплывающие уведомления без следов после исчезновения.
Внизу — журнал событий с явной кнопкой «Показать/Скрыть». Тема
переключается кнопкой в шапке и запоминается (data/settings.json);
пересборка при смене темы происходит на скрытом окне — без видимой
прогрузки элементов, состояние «развёрнуто на весь экран» сохраняется.

Скроллбары свои (FlatScrollbar/FlatScroll): тонкие, пропорциональные
содержимому, колесо мыши скроллит зону под курсором. Ctrl+V/C/X/A и
Ctrl+Insert в полях ввода работают при любой раскладке Windows
(роутинг по физическому коду клавиши), есть меню правого клика.

Интерфейс не ходит в сеть сам: всю работу делает engine.py в фоновых потоках,
а события приходят через очередь (log / busy / accounts / card / stats /
progress / toast / thumb / avatar / deps).
"""
import os
import sys
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

import customtkinter as ctk

import applog
import db
import paths
import i18n
from i18n import tr
import settings
import sorting
from engine import Engine
from engine import LEVEL_HEAD, LEVEL_WARN
from ui_theme import THEMES
from ui_theme import BROWSERS
from ui_theme import BTN_H
from ui_theme import RADIUS
from tkinter import messagebox
from ui_widgets import FlatScroll
from ui_widgets import VStack
from ui_widgets import FlatScrollbar
from ui_calendar import CalendarMixin
from ui_edit import EditMixin
from ui_log import LogMixin
from ui_cards import CardsMixin
from acclist import AccountList
from ui_auto import AutoSyncMixin
from ui_posts import PostsMixin
from ui_actions import ActionsMixin

_LOG = applog.get(__name__)


class App(AutoSyncMixin, CalendarMixin, EditMixin, LogMixin, CardsMixin, PostsMixin, ActionsMixin, ctk.CTk):
    def __init__(self):
        self.settings = self._load_settings()
        self.theme_name = self.settings.get("theme") if self.settings.get("theme") in THEMES \
            else "dark"
        self.T = THEMES[self.theme_name]
        ctk.set_appearance_mode(self.T["mode"])
        super().__init__(fg_color=self.T["bg"])
        self.title(tr('TokSaver — TikTok account database'))
        self.geometry("1320x800")
        self.minsize(1120, 680)

        self._q = queue.Queue(maxsize=20000)   # события от движка → интерфейс (с потолком)
        self._cards = {}             # acc_id → виджеты карточки
        self._busy = False
        self._toggling = []          # КНОПКИ, которые блокируются во время задач
        self._acc_dirty = False      # список аккаунтов требует перестроения
        self._last_rebuild = 0.0
        self._cal_panel = None       # встроенная панель календарика в сайдбаре
        self._cal_entry = None       # поле, куда пишется выбранная дата
        self._cal_state = None       # {"y": год, "m": месяц, "sel": выбранная дата}
        self._log_buffer = []        # журнал переживает смену темы
        self._log_open = self._log_default_open()
        self._thumb_img = {}         # acc_id → CTkImage обложки
        self._thumb_failed = set()   # acc_id без обложки (не дёргать сеть зря)
        self._avatar_img = {}        # acc_id → CTkImage фото-аватара в кружке
        self._avatar_small = {}      # acc_id → CTkImage мини-аватара (бейдж на обложке)
        self._avatar_failed = set()  # acc_id без аватара (не дёргать сеть зря)
        self._deps_last = None
        self._proxy_was_alive = False  # для повторного запроса аватаров после старта ByeDPI
        self._font_cache = {}        # шрифты-измерители для _btn_w (создаются один раз)

        self.engine = Engine(self._emit)
        self._load_cookie_prefs()           # источник cookies — с прошлого запуска
        try:
            self._font_family = tkfont.nametofont("TkDefaultFont").actual("family") or "Segoe UI"
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            self._font_family = "Segoe UI"

        # Окно появляется ГОТОВЫМ: вся сборка идёт на скрытом окне. Иначе
        # виден поэтапный прогруз — каждый виджет рисуется по очереди, и на
        # слабых машинах это выглядит как «работа в один поток из 90-х».
        self.withdraw()
        self._build()

        # Колесо мыши: один глобальный роутер на все прокручиваемые зоны
        # (FlatScroll-ы и журнал), привязан к корню — переживает смену темы.
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind_all("<Button-4>", self._on_wheel, add="+")
        self.bind_all("<Button-5>", self._on_wheel, add="+")

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        db.db_init(paths.DATA_DIR)
        for n in db.take_notes():
            self._append_log(LEVEL_HEAD, tr('[Database] {0}', n))
        self._rebuild_accounts()
        self.after(80, self._poll)
        self.after(5000, self._auto_tick)       # автосинхронизация по расписанию
        for _m in getattr(self, "_pending_log", []):
            self._append_log(LEVEL_WARN, _m)

        if os.environ.get("TOKSAVER_SKIP_DEPS") != "1":
            threading.Thread(target=self.engine.check_deps, daemon=True).start()

        # геометрию считаем скрытой (update_idletasks), потом показываем всё
        # сразу — готовое окно, без поэтапного прогруза виджетов.
        # ВАЖНО: флаг _window_exists=True обязателен ДО deiconify. mainloop()
        # customtkinter (5.x и 6.x) на Windows «перекрашивает заголовок»:
        # прячет окно, а показывает обратно ТОЛЬКО если до первого показа не
        # вызывался withdraw(). Наш withdraw ломал это условие — окно
        # мигало на мгновение и оставалось скрытым навсегда. Флаг
        # пропускает перекраску: окно уже показано и готово.
        try:
            self.update_idletasks()
            self._window_exists = True
            self._withdraw_called_before_window_exists = False
            self._iconify_called_before_window_exists = False
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass

    # ------------------------- настройки (тема и пр.) -------------------------
    @staticmethod
    def _load_settings():
        return settings.load()

    def _save_settings(self):
        settings.save(self.settings)    # атомарная запись; сбой не критичен

    def _log_default_open(self):
        """Журнал по умолчанию раскрыт, пока пользователь сам его не свернёт.
        Старая настройка log_open=false из прежних версий игнорируется один раз
        (флаг log_v2): иначе после обновления журнал остаётся спрятанным и
        пользователь снова не находит его."""
        if self.settings.get("log_v2") != 1:
            return True
        return bool(self.settings.get("log_open", True))

    # ------------------------------ темы ------------------------------
    def _apply_theme(self, name):
        """Переключить тему: пересобрать окно с новыми цветами, сохранив
        введённые данные, журнал, карточки и состояние движка.
        Пересборка идёт на СКРЫТОМ окне (withdraw → … → deiconify), иначе
        виден поэтапный прогруз виджетов — это выглядело как мигание.
        Состояние окна (в т.ч. «развёрнуто на весь экран») сохраняется."""
        if name not in THEMES or name == self.theme_name:
            return
        was_shown = bool(self.winfo_viewable())
        win_state = self.state()          # normal / zoomed (развёрнуто) / icon
        if was_shown:
            try:
                self.withdraw()           # прячем: пользователь не видит пересборку
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass
        try:
            cap = self._capture_state()
            self._hide_calendar()
            self._close_posts_window()   # окно постов пересоберётся в новой теме по клику
            for w in self.winfo_children():
                w.destroy()
            self.theme_name = name
            self.T = THEMES[name]
            self._avatar_img.clear()   # обводка кружка аватара в новых цветах
            self._avatar_small.clear()  # бейдж-аватар тоже перерисовывается
            self.settings["theme"] = name
            self._save_settings()
            ctk.set_appearance_mode(self.T["mode"])
            self.configure(fg_color=self.T["bg"])
            self._build()
            self._restore_state(cap)
            self._refill_log()
            self._rebuild_accounts(force=True)
            self._set_busy(self._busy)
        finally:
            if was_shown:
                # геометрию считаем пока окно скрыто — на экран всё появится сразу
                self.update_idletasks()
                try:
                    self.deiconify()
                    if win_state == "zoomed":
                        self.state("zoomed")   # вернуть развёрнутый вид
                except Exception as _exc:
                    _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                    pass

    def _on_theme_seg(self, label):
        self._apply_theme("dark" if label == tr('Dark') else "light")

    def _on_language_changed(self, name):
        """Язык применяется после перезапуска окна: предлагаем перезапуск сразу."""
        code = i18n.code_for_name(name)
        if not code or code == i18n.current():
            return
        self.settings["language"] = code
        self._save_settings()
        if messagebox.askyesno(tr("Language"), tr("Language changed.\nRestart TokSaver now to apply it?"),
                               parent=self):
            self._restart_app()

    def _restart_app(self):
        """Перезапуск процесса (тот же интерпретатор и аргументы). Идущие задачи
        аккуратно останавливаются, как при закрытии окна."""
        try:
            self.on_close(restart=True)
        except TypeError:
            self.on_close()

    def _toggle_theme(self):
        self._apply_theme("light" if self.theme_name == "dark" else "dark")

    def _capture_state(self):
        def grab(attr):
            w = getattr(self, attr, None)
            try:
                return w.get().strip() if w is not None and w.winfo_exists() else ""
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                return ""
        try:
            rng = self.range_mode.get()
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            rng = tr('All')
        return {"add": grab("add_entry"), "single": grab("single_entry"),
                "count": grab("count_entry"), "df": grab("date_from_entry"),
                "dt": grab("date_to_entry"), "range": rng}

    def _restore_state(self, cap):
        def put(entry, value):
            if value:
                entry.delete(0, "end")
                entry.insert(0, value)
        put(self.add_entry, cap.get("add"))
        put(self.single_entry, cap.get("single"))
        put(self.count_entry, cap.get("count") or "50")
        put(self.date_from_entry, cap.get("df"))
        put(self.date_to_entry, cap.get("dt"))
        self.range_mode.set(cap.get("range") or tr('All'))
        self._on_range_mode()
        self.cookies_menu.set(self.engine.cookies or tr('none'))
        if self.cookies_file_path:
            name = os.path.basename(self.cookies_file_path)
            self.cookies_file_lbl.configure(
                text="…" + name[-38:] if len(name) > 39 else name, text_color=self.T["ok"])
        try:
            self.byedpi_var.set(bool(self.engine.use_byedpi))
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass

    # ------------------------- события от движка -------------------------
    def _emit(self, kind, payload=None):
        """Вызывается из рабочих потоков — только put в очередь. Очередь ограничена:
        если окно не успевает читать, лишние строки ЖУРНАЛА отбрасываются, а важные
        события (статус, прогресс) ждут место до 2 секунд."""
        try:
            self._q.put_nowait((kind, payload))
        except queue.Full:
            if kind == "log":
                return
            try:
                self._q.put((kind, payload), timeout=2)
            except queue.Full:
                _LOG.warning('event queue overflow, event %s lost', kind)

    def _poll(self):
        """Разбор очереди событий. Логи копятся и вставляются пачкой,
        перестроение списка — не чаще раза в секунду (анти-тормоза).
        Обычные обновления (статус, счётчики, прогресс) — точечные."""
        logs = []
        t_start = time.perf_counter()
        if not hasattr(self, "_pend_card"):
            self._pend_card, self._pend_prog, self._pend_stats = {}, {}, set()
            self._stats_last = {}
        try:
            for _ in range(3000):
                try:
                    kind, payload = self._q.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "log":
                        logs.append(payload)
                    elif kind == "busy":
                        self._set_busy(payload)
                    elif kind == "accounts":
                        self._acc_dirty = True
                    elif kind == "card":
                        # склеиваем: за тик нужно только ПОСЛЕДНЕЕ значение
                        self._pend_card[payload[0]] = payload[1]
                    elif kind == "stats":
                        self._pend_stats.add(payload)
                        self._thumb_failed.discard(payload)   # после синка — шанс взять обложку
                    elif kind == "progress":
                        self._pend_prog[payload[0]] = payload[1]
                    elif kind == "toast":
                        level, text = payload
                        self._toast(text, level)
                    elif kind == "thumb":
                        acc_id, path = payload
                        self._on_thumb(acc_id, path)
                    elif kind == "avatar":
                        acc_id, path = payload
                        self._on_avatar(acc_id, path)
                    elif kind == "deps":
                        self._update_deps(payload)
                    elif kind == "vcov":
                        # превью поста готово — окно постов обновит плитку
                        acc_id_, vid, vpath = payload
                        win = getattr(self, "_posts_win", None)
                        if win is not None and getattr(win, "_acc_id", None) == acc_id_:
                            win.on_cover(vid, vpath)
                    elif kind == "vcov_done":
                        win = getattr(self, "_posts_win", None)
                        if win is not None and getattr(win, "_acc_id", None) == payload:
                            win.on_cover_done()
                    elif kind == "posts_dirty":
                        # посты аккаунта изменились (скачка/отмена/перекачка) —
                        # открытое окно постов перечитывает базу само
                        win = getattr(self, "_posts_win", None)
                        if win is not None and getattr(win, "_acc_id", None) == payload:
                            win.refresh()
                except Exception as _exc:
                    _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                    pass  # одно плохое событие не должно ломать опрос
            if logs:
                self._append_logs(logs)
            self._apply_pending(t_start)
            if self._acc_dirty and time.time() - self._last_rebuild >= 0.25:
                self._acc_dirty = False
                self._last_rebuild = time.time()
                self._rebuild_accounts()
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            pass  # окно закрыто
        finally:
            try:
                more = bool(self._pend_card or self._pend_prog or self._pend_stats
                            or not self._q.empty())
                self.after(30 if more else 100, self._poll)
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass

    STATS_MIN_GAP = 0.4        # счётчики одной карточки — не чаще раза в 0,4 с
    POLL_BUDGET = 0.012        # сколько секунд за тик тратим на обновление виджетов

    def _apply_pending(self, t_start):
        """Применяет склеенные события порциями. Раньше КАЖДОЕ событие движка
        (сотни в секунду при параллельной загрузке) обновляло виджеты и ходило
        в базу — окно замирало. Теперь за тик берём последнее значение на карточку
        и укладываемся в бюджет времени; остальное доделается следующий тик."""
        budget = self.POLL_BUDGET

        def over():
            return time.perf_counter() - t_start > budget

        for acc_id in list(self._pend_card):
            text = self._pend_card.pop(acc_id)
            card = self._cards.get(acc_id)
            if card:
                self._set_card_status(card, text)
            if over():
                return
        for acc_id in list(self._pend_prog):
            value = self._pend_prog.pop(acc_id)
            card = self._cards.get(acc_id)
            if card:
                self._set_card_progress(card, value)
            if over():
                return
        now = time.monotonic()
        for acc_id in list(self._pend_stats):
            if now - self._stats_last.get(acc_id, 0) < self.STATS_MIN_GAP:
                continue                      # слишком часто — оставим на следующий тик
            self._pend_stats.discard(acc_id)
            self._stats_last[acc_id] = now
            card = self._cards.get(acc_id)
            if card and not self.accounts_box.is_shown(acc_id):
                card["sig"] = None                  # скрытая: пересчитаем при ближайшей пересборке
                continue
            self._update_card_stats(acc_id)
            if over():
                return

    # ------------------------------ построение ------------------------------
    def _build(self):
        self._build_sidebar()
        self._build_main()

    def _separator(self, parent):
        """Тонкая линия-разделитель между блоками сайдбара. Обычный tk.Frame:
        рисуется ровно высотой 1px. CTkFrame высотой 1px отрисовывался
        ПУСТЫМ (линии не было видно вовсе), а заодно стоил дороже при сборке."""
        tk.Frame(parent, height=1, bg=self.T["line"], bd=0,
                 highlightthickness=0).pack(fill="x", pady=(16, 0))

    def _btn_w(self, text, size=12, bold=False, pad=32, min_w=0):
        """Ширина кнопки по фактической ширине надписи. Фиксированные ширины
        обрезали текст («Синхронизировать всех») при масштабе 125% и выше.
        Шрифты-измерители кэшируются: при сборке окна этот вызов срабатывает
        десятки раз, и создание Font'а каждый раз заметно тормозит сборку."""
        try:
            key = (size, bool(bold))
            f = self._font_cache.get(key)
            if f is None:
                f = tkfont.Font(family=self._font_family, size=size,
                                weight="bold" if bold else "normal")
                self._font_cache[key] = f
            w = int(f.measure(text)) + pad
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            w = 0
        return max(min_w, w, 64)

    def _shadow_panel(self, parent, pack_kwargs=None):
        """Скруглённая панель с тонкой обводкой. Возвращает (обёртка, панель);
        содержимое кладётся в панель."""
        T = self.T
        try:
            bg = parent.cget("bg")
        except Exception:
            bg = T["bg"]
        # обычный tk.Frame: у CTkFrame событие <Configure> привязывается к внутреннему
        # холсту и не отражает реальной высоты — стек накладывал карточки друг на друга
        wrap = tk.Frame(parent, bg=bg, bd=0, highlightthickness=0)
        if pack_kwargs is not None:
            wrap.pack(**pack_kwargs)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(0, weight=1)
        panel = ctk.CTkFrame(wrap, fg_color=T["card"], corner_radius=RADIUS + 2,
                             border_width=1, border_color=T["line"])
        panel.grid(row=0, column=0, sticky="nsew")
        return wrap, panel

    def _section(self, parent, title):
        ctk.CTkLabel(parent, text=title, font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self.T["muted"]).pack(anchor="w", pady=(16, 6))

    def _build_sidebar(self):
        T = self.T
        side = ctk.CTkFrame(self, width=340, corner_radius=RADIUS, fg_color=T["side"])
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        # вертикальная линия: сайдбар не сливается с основной зоной
        ctk.CTkFrame(self, width=1, fg_color=T["line"]).pack(side="left", fill="y")

        # Сайдбар прокручиваемый: содержимое выше окна (настройки + авторизация),
        # без прокрутки нижние блоки просто отрезаются и «куда-то пропадают».
        # Своя прокрутка (FlatScroll) — у CTk ползунок кривой и на всю высоту.
        scroll = FlatScroll(side, bg=T["side"], thumb=T["scroll"],
                            thumb_h=T["scroll_h"], gutter=10)
        scroll.pack(fill="both", expand=True, padx=(18, 6), pady=(16, 10))
        self.sidebar_scroll = scroll
        inner = scroll.body
        self.sidebar_inner = inner   # используется календариком (панель внутри сайдбара)

        # --- логотип ---
        head = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        head.pack(anchor="w")
        ctk.CTkLabel(head, text="Tok", font=ctk.CTkFont(size=26, weight="bold"),
                     text_color=T["text"]).pack(side="left")
        ctk.CTkLabel(head, text="Saver", font=ctk.CTkFont(size=26, weight="bold"),
                     text_color=T["accent"]).pack(side="left")
        ctk.CTkLabel(inner, text=tr('TikTok downloader with an account database'),
                     font=ctk.CTkFont(size=12), text_color=T["muted"]).pack(anchor="w")

        # --- новый аккаунт ---
        self._section(inner, tr('NEW ACCOUNT'))
        self.add_entry = ctk.CTkEntry(inner, placeholder_text=tr('@nickname or profile link'),
                                      height=BTN_H, corner_radius=RADIUS, fg_color=T["field"],
                                      border_color=T["line"], text_color=T["text"])
        self.add_entry.pack(fill="x")
        self.add_entry.bind("<Return>", lambda e: self.on_add())
        self._attach_edit_keys(self.add_entry)

        ctk.CTkLabel(inner, text=tr('How much to download at once:'), font=ctk.CTkFont(size=13),
                     text_color=T["muted"]).pack(anchor="w", pady=(10, 4))
        self.range_mode = ctk.StringVar(value=tr('All'))
        self.range_seg = ctk.CTkSegmentedButton(
            inner, values=[tr('All'), tr('Latest'), tr('Period')], variable=self.range_mode,
            command=self._on_range_mode, height=BTN_H, font=ctk.CTkFont(size=13),
            corner_radius=RADIUS, fg_color=T["field"], selected_color=T["accent"],
            selected_hover_color=T["accent_h"], unselected_color=T["field"],
            unselected_hover_color=T["card_h"], text_color=T["text"])
        self.range_seg.pack(fill="x")

        self.count_row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        self.count_entry = ctk.CTkEntry(self.count_row, width=78, height=BTN_H, corner_radius=RADIUS,
                                        fg_color=T["field"], border_color=T["line"],
                                        text_color=T["text"])
        self.count_entry.insert(0, "50")
        self.count_entry.pack(side="left")
        self._attach_edit_keys(self.count_entry)
        ctk.CTkLabel(self.count_row, text=tr('latest posts'), font=ctk.CTkFont(size=13),
                     text_color=T["muted"]).pack(side="left", padx=8)

        self.period_row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        self.date_from_entry = self._date_field(self.period_row, tr('from'), "01.01.2024")
        self.date_to_entry = self._date_field(self.period_row, tr('to'), "31.12.2024")

        self.range_hint = ctk.CTkLabel(inner, text=tr('“All” downloads the WHOLE profile (slow).\nThe range applies on the first download of an account,\nafter that only new posts are downloaded.'),
                                       font=ctk.CTkFont(size=11), text_color=T["muted"],
                                       justify="left")
        self.range_hint.pack(anchor="w", pady=(6, 0))

        self.add_btn = ctk.CTkButton(inner, text=tr('Add and download'), height=46,
                                     corner_radius=RADIUS, font=ctk.CTkFont(size=14, weight="bold"),
                                     fg_color=T["accent"], hover_color=T["accent_h"],
                                     text_color=T["on_accent"], command=self.on_add)
        self.add_btn.pack(fill="x", pady=(10, 0))
        self._toggling += [self.add_btn]   # поля ввода НЕ блокируем — можно копировать/вставлять

        self._separator(inner)

        # --- разовое видео ---
        self._section(inner, tr('ONE-OFF DOWNLOAD'))
        self.single_entry = ctk.CTkEntry(inner, placeholder_text=tr('video/photo post link'),
                                         height=BTN_H, corner_radius=RADIUS, fg_color=T["field"],
                                         border_color=T["line"], text_color=T["text"])
        self.single_entry.pack(fill="x")
        self.single_entry.bind("<Return>", lambda e: self.on_single())
        self._attach_edit_keys(self.single_entry)
        self.single_btn = ctk.CTkButton(inner, text=tr('Download without database'), height=BTN_H,
                                        corner_radius=RADIUS, fg_color=T["field"],
                                        hover_color=T["card_h"], text_color=T["text"],
                                        border_width=1, border_color=T["line"],
                                        command=self.on_single)
        self.single_btn.pack(fill="x", pady=(8, 0))
        self._toggling += [self.single_btn]

        self._separator(inner)

        # --- настройки ---
        self._section(inner, tr('SETTINGS'))
        self.byedpi_var = ctk.BooleanVar(value=True)
        self.byedpi_switch = ctk.CTkSwitch(
            inner, text=tr('Bypass blocking (ByeDPI)'), variable=self.byedpi_var,
            command=self._toggle_byedpi, progress_color=T["accent"], corner_radius=RADIUS,
            font=ctk.CTkFont(size=13), text_color=T["text"])
        self.byedpi_switch.pack(anchor="w")
        self.proxy_status = ctk.CTkLabel(inner, text=tr('status: checking…'),
                                         font=ctk.CTkFont(size=11), text_color=T["muted"])
        self.proxy_status.pack(anchor="w", padx=(28, 0))
        par_row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        par_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(par_row, text=tr('Parallel downloads:'), font=ctk.CTkFont(size=13),
                     text_color=T["text"]).pack(side="left")
        self.parallel_menu = ctk.CTkOptionMenu(
            par_row, values=["1", "2", "3", "4"], width=64, height=30, corner_radius=RADIUS,
            font=ctk.CTkFont(size=13), fg_color=T["field"], button_color=T["card_h"],
            button_hover_color=T["sel_line"], text_color=T["text"],
            dropdown_fg_color=T["card"], dropdown_hover_color=T["card_h"],
            dropdown_text_color=T["text"], command=self._on_parallel_changed)
        self.parallel_menu.set(str(settings.get_parallel(self.settings)))
        self.parallel_menu.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(inner, text=tr('1 — one by one (more reliable). 2-4 is faster, but TikTok\nmay start limiting requests: the app then\npauses by itself.'),
                     font=ctk.CTkFont(size=11), text_color=T["muted"], justify="left").pack(
            anchor="w", pady=(2, 0))
        self._build_auto_sync(inner, T)
        # --- вид: тема и язык ---
        look_row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        look_row.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(look_row, text=tr('Theme:'), font=ctk.CTkFont(size=13),
                     text_color=T["text"]).pack(side="left")
        self.theme_seg = ctk.CTkSegmentedButton(
            look_row, values=[tr('Dark'), tr('Light')], command=self._on_theme_seg, height=30,
            corner_radius=RADIUS, font=ctk.CTkFont(size=13), fg_color=T["field"],
            selected_color=T["accent"], selected_hover_color=T["accent_h"],
            unselected_color=T["field"], unselected_hover_color=T["card_h"],
            text_color=T["text"])
        self.theme_seg.set(tr('Dark') if self.theme_name == "dark" else tr('Light'))
        self.theme_seg.pack(side="right")
        lang_row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        lang_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(lang_row, text=tr('Language:'), font=ctk.CTkFont(size=13),
                     text_color=T["text"]).pack(side="left")
        self.lang_menu = ctk.CTkOptionMenu(
            lang_row, values=i18n.language_names(), width=170, height=30,
            corner_radius=RADIUS, font=ctk.CTkFont(size=13), fg_color=T["field"],
            button_color=T["card_h"], button_hover_color=T["sel_line"], text_color=T["text"],
            dropdown_fg_color=T["card"], dropdown_hover_color=T["card_h"],
            dropdown_text_color=T["text"], command=self._on_language_changed)
        self.lang_menu.set(i18n.language_name(i18n.current()))
        self.lang_menu.pack(side="right")
        self._separator(inner)
        if self._deps_last:
            self._update_deps(self._deps_last)

        # --- авторизация (закрытые аккаунты) ---
        self._section(inner, tr('AUTHORIZATION — ACCESS TO POSTS'))
        ctk.CTkLabel(inner, text=tr('TikTok sometimes requires a login even for public\nprofiles: it shows “Log in to TikTok” and does not return\nthe post list (private accounts — always).\nRECOMMENDED: export cookies.txt (for tiktok.com\nonly) — the app will not see your other\nsessions. The “Get cookies.txt LOCALLY”\nextension in your browser can export the file.'),
                     font=ctk.CTkFont(size=11), text_color=T["muted"], justify="left").pack(
            anchor="w", pady=(0, 6))

        file_row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        file_row.pack(fill="x", pady=(0, 0))
        ctk.CTkButton(file_row, text=tr('Choose cookies.txt file'), height=36, corner_radius=RADIUS,
                      fg_color=T["field"], hover_color=T["card_h"], text_color=T["text"],
                      border_width=1, border_color=T["line"], font=ctk.CTkFont(size=13),
                      command=self.on_pick_cookies_file).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(file_row, text=tr('Reset'), width=self._btn_w(tr('Reset'), 13, min_w=62),
                      height=36, corner_radius=RADIUS,
                      fg_color=T["field"], hover_color=T["card_h"], text_color=T["muted"],
                      border_width=1, border_color=T["line"], font=ctk.CTkFont(size=13),
                      command=self.on_clear_cookies_file).pack(side="left", padx=(6, 0))
        self.cookies_file_lbl = ctk.CTkLabel(inner, text=tr('no file selected'),
                                             font=ctk.CTkFont(size=11), text_color=T["muted"])
        self.cookies_file_lbl.pack(anchor="w", pady=(2, 0))
        self.cookies_file_path = getattr(self, "cookies_file_path", None)
        if self.cookies_file_path:
            _nm = os.path.basename(self.cookies_file_path)
            self.cookies_file_lbl.configure(
                text="…" + _nm[-38:] if len(_nm) > 39 else _nm, text_color=T["ok"])

        ctk.CTkLabel(inner, text=tr('Or straight from the browser (less safe):'),
                     font=ctk.CTkFont(size=12), text_color=T["muted"]).pack(
            anchor="w", pady=(10, 3))
        self.cookies_menu = ctk.CTkOptionMenu(
            inner, values=BROWSERS, height=BTN_H, corner_radius=RADIUS, anchor="w",
            font=ctk.CTkFont(size=13), fg_color=T["field"], button_color=T["card_h"],
            button_hover_color=T["sel_line"], text_color=T["text"],
            dropdown_fg_color=T["card"], dropdown_hover_color=T["card_h"],
            dropdown_text_color=T["text"], command=self._on_cookies_changed)
        self.cookies_menu.set(self.engine.cookies or tr('none'))
        self.cookies_menu.pack(fill="x")
        ctk.CTkLabel(inner, text=tr('⚠ This mode gives the app access to ALL\ncookies of the chosen browser (mail, banks,\nsocial networks), not only TikTok. If you have\nalready seen signs of a hack — do not use it.\nIf a cookies.txt file is chosen, the browser is ignored.\nImportant: you must follow the private\naccount — otherwise the post list is invisible to you too.'),
                     font=ctk.CTkFont(size=11), text_color=T["warn"], justify="left").pack(
            anchor="w", pady=(6, 0))

        self._separator(inner)
        ctk.CTkLabel(inner, text=tr('Data folder (account database):'), font=ctk.CTkFont(size=12),
                     text_color=T["muted"]).pack(anchor="w", pady=(12, 3))
        folder_row = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        folder_row.pack(fill="x")
        short = paths.DATA_DIR if len(paths.DATA_DIR) <= 42 else "…" + paths.DATA_DIR[-41:]
        ctk.CTkLabel(folder_row, text=short, font=ctk.CTkFont(size=11),
                     text_color=T["muted"]).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(folder_row, text=tr('Open'), width=self._btn_w(tr('Open'), 12, min_w=74),
                      height=34, corner_radius=RADIUS,
                      fg_color=T["field"], hover_color=T["card_h"], text_color=T["text"],
                      border_width=1, border_color=T["line"], font=ctk.CTkFont(size=12),
                      command=self.on_open_data).pack(side="right")

        self._separator(inner)
        ctk.CTkLabel(inner, text=tr('One-off downloads (no database):'), font=ctk.CTkFont(size=12),
                     text_color=T["muted"]).pack(anchor="w", pady=(10, 3))
        folder_row2 = tk.Frame(inner, bg=T["side"], bd=0, highlightthickness=0)
        folder_row2.pack(fill="x")
        short2 = paths.OUTPUT_DIR if len(paths.OUTPUT_DIR) <= 42 else "…" + paths.OUTPUT_DIR[-41:]
        ctk.CTkLabel(folder_row2, text=short2, font=ctk.CTkFont(size=11),
                     text_color=T["muted"]).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(folder_row2, text=tr('Open'), width=self._btn_w(tr('Open'), 12, min_w=74),
                      height=34, corner_radius=RADIUS,
                      fg_color=T["field"], hover_color=T["card_h"], text_color=T["text"],
                      border_width=1, border_color=T["line"], font=ctk.CTkFont(size=12),
                      command=self.on_open_downloads).pack(side="right")

        # низ сайдбара
        ctk.CTkLabel(inner, text=tr('engine: yt-dlp + ByeDPI + gallery-dl'),
                     font=ctk.CTkFont(size=11), text_color=T["muted"]).pack(
            side="bottom", anchor="w")
        self._on_range_mode()  # начальное состояние полей диапазона

    def _build_main(self):
        T = self.T
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=16, pady=14)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # --- заголовок (tk.Frame вместо CTkFrame: сплошной цвет без холста —
        # меньше перерисовок, выше FPS при перетаскивании/наведении) ---
        head = tk.Frame(main, bg=T["bg"], bd=0, highlightthickness=0)
        head.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(head, text=tr('Accounts'), font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=T["text"]).pack(side="left")
        self.count_lbl = ctk.CTkLabel(head, text="· 0", font=ctk.CTkFont(size=14),
                                      text_color=T["muted"])
        self.count_lbl.pack(side="left", padx=(8, 0))
        self.sync_all_btn = ctk.CTkButton(
            head, text=tr('Sync all'),
            width=self._btn_w(tr('Sync all'), 14, bold=True, min_w=176),
            height=BTN_H, corner_radius=RADIUS,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=T["accent"], hover_color=T["accent_h"],
            text_color=T["on_accent"], command=self.on_sync_all)
        self.stop_all_btn = ctk.CTkButton(
            head, text=tr('Stop all'),
            width=self._btn_w(tr('Stop all'), 14, min_w=150), height=BTN_H,
            corner_radius=RADIUS, font=ctk.CTkFont(size=14), fg_color=T["field"],
            hover_color=T["card_h"], text_color=T["danger"], border_width=1,
            border_color=T["line"], state="disabled", command=self.on_cancel_all)
        self.stop_all_btn.pack(side="right")
        self.sync_all_btn.pack(side="right", padx=(0, 8))
        self._toggling.append(self.sync_all_btn)
        # сортировка списка аккаунтов (выбор запоминается в настройках)
        self.sort_menu = ctk.CTkOptionMenu(
            head, values=[t for _k, t in sorting.ACCOUNT_SORTS], width=190, height=BTN_H,
            corner_radius=RADIUS, font=ctk.CTkFont(size=13), fg_color=T["field"],
            button_color=T["card_h"], button_hover_color=T["sel_line"], text_color=T["text"],
            dropdown_fg_color=T["card"], dropdown_hover_color=T["card_h"],
            dropdown_text_color=T["text"], command=self._on_accounts_sort)
        self.sort_menu.set(sorting.label_for(sorting.ACCOUNT_SORTS,
                                             self.settings.get("accounts_sort", "added")))
        self.sort_menu.pack(side="right", padx=(0, 10))
        ctk.CTkLabel(head, text=tr('Sort:'), font=ctk.CTkFont(size=12),
                     text_color=T["muted"]).pack(side="right", padx=(0, 6))

        # --- список аккаунтов (своя прокрутка: ползунок плоский и пропорциональный) ---
        self.accounts_box = AccountList(main, self, T, self._font_family, bg=T["bg"],
                                        thumb=T["scroll"], thumb_h=T["scroll_h"])
        self.accounts_box.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        # --- журнал событий (панель на всю ширину, явная кнопка) ---
        # tk.Frame с обводкой вместо CTkFrame — на 1 холст меньше
        logf = tk.Frame(main, bg=T["card"], bd=0, highlightthickness=1,
                        highlightbackground=T["line"])
        logf.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        lhead = tk.Frame(logf, bg=T["card"], height=38, bd=0, highlightthickness=0)
        lhead.pack(fill="x", padx=10, pady=(6, 4))
        lhead.pack_propagate(False)
        ctk.CTkLabel(lhead, text=tr('EVENT LOG'), font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=T["text"]).pack(side="left")
        self.log_count_lbl = ctk.CTkLabel(lhead, text="", font=ctk.CTkFont(size=12),
                                          text_color=T["muted"])
        self.log_count_lbl.pack(side="left", padx=(8, 0))
        self._log_toggle_btn = ctk.CTkButton(
            lhead, text=tr('Hide log') if self._log_open else tr('Show log'),
            width=self._btn_w(tr('Hide log'), 13, min_w=148), height=36, corner_radius=RADIUS,
            fg_color=T["field"], hover_color=T["card_h"], text_color=T["text"],
            border_width=1, border_color=T["line"], font=ctk.CTkFont(size=13),
            command=self._toggle_log)
        self._log_toggle_btn.pack(side="right")
        ctk.CTkButton(lhead, text=tr('Clear'), width=self._btn_w(tr('Clear'), 13, min_w=86),
                      height=36, corner_radius=RADIUS,
                      fg_color=T["field"], hover_color=T["card_h"], text_color=T["muted"],
                      border_width=1, border_color=T["line"], font=ctk.CTkFont(size=13),
                      command=lambda: self._clear_log()).pack(side="right", padx=(0, 6))
        # подпись тоже кликабельна — куда ни попади, журнал откроется
        for w in (lhead, lhead.winfo_children()[0], self.log_count_lbl):
            w.bind("<Button-1>", lambda _e: self._toggle_log())

        # линия под шапкой журнала: отделяет кнопки от текста, панель выглядит
        # аккуратно и в раскрытом, и в свёрнутом виде
        tk.Frame(logf, height=1, bg=T["line"], bd=0,
                 highlightthickness=0).pack(fill="x", padx=10)

        self.log_body = tk.Frame(logf, bg=T["card"], bd=0, highlightthickness=0)
        # activate_scrollbars=False: у CTkTextbox ползунок кривой — подключаем
        # к тексту свой плоский скроллбар (справа, той же высоты, что текст)
        self.logbox = ctk.CTkTextbox(self.log_body, height=150, fg_color=T["log_bg"],
                                     text_color=T["log_text"], border_width=0,
                                     corner_radius=RADIUS, activate_scrollbars=False,
                                     font=ctk.CTkFont(family="Consolas", size=12))
        self.logbox.pack(side="left", fill="both", expand=True, padx=(10, 0),
                         pady=(2, 10))
        self.log_scroll = FlatScrollbar(self.log_body, thumb=T["scroll"],
                                        thumb_h=T["scroll_h"],
                                        command=self.logbox._textbox.yview,
                                        bg=T["card"])
        self.log_scroll.pack(side="right", fill="y", padx=(6, 10), pady=(2, 10))
        self.logbox._textbox.configure(yscrollcommand=self.log_scroll.set)
        self.logbox.configure(state="disabled")
        for lvl, color in self._log_tag_colors().items():
            try:
                self.logbox.tag_config(lvl, foreground=color)
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                self.logbox._textbox.tag_config(lvl, foreground=color)
        if self._log_open:
            self.log_body.pack(fill="x")
        self._update_log_count()

        # --- всплывающие уведомления (тосты) ---
        # Общего контейнера НЕТ: CTkFrame после уничтожения детей не
        # схлопывается обратно (проверено: остаётся 419x85 при пустом списке
        # детей) и висит серой «заплаткой» поверх журнала, перекрывая кнопку
        # «Показать журнал» и клики. Каждый тост кладётся place() прямо на
        # окно с личным отступом от угла — см. _toast / _toast_relayout.
        self._toasts = []                    # живые тосты, старшие первыми



def run():
    # Фоновые потоки (загрузки, превью) держат GIL по 5 мс — окно успевает «дёрнуться».
    # Короткий интервал переключения отдаёт слово потоку интерфейса заметно чаще.
    sys.setswitchinterval(0.001)
    app = App()
    app.mainloop()
