"""Окно «Посты аккаунта».

Часть интерфейса TokSaver: миксин для класса App (ui.py). Методы перенесены
из прежнего ui.py без изменений."""
from i18n import tr
import os
import threading
import tkinter as tk
import webbrowser

import customtkinter as ctk

import applog
from ui_theme import RADIUS
import db
import paths
import sorting
import thumbs
import tilegrid
from engine import LEVEL_ERROR
from engine import fmt_date
from ui_widgets import FlatScrollbar
from ui_widgets import FlowRow, PopupMenu, hide_until_built, show_built
import postmedia

_LOG = applog.get(__name__)


class PostsMixin:
    # ------------------------------ окно постов аккаунта ------------------------------
    def _close_posts_window(self):
        """Закрыть окно списка постов (при смене темы и закрытии программы)."""
        win = getattr(self, "_posts_win", None)
        if win is not None:
            try:
                win.destroy()
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass
            self._posts_win = None

    def on_show_posts(self, acc_id):
        """Окно «Посты аккаунта»: клик по карточке или пункт контекстного меню.
        Список из базы: дата, тип, статус, название; двойной клик — открыть
        файл; описание поста и путь — под списком."""
        acc = db.db_get_account(acc_id)
        if not acc:
            return
        win = getattr(self, "_posts_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    if getattr(win, "_acc_id", None) == acc_id:
                        win.lift()
                        win.focus_force()
                        return
                    win.destroy()
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass
            self._posts_win = None
        self._posts_win = self._build_posts_window(acc)

    def _build_posts_window(self, acc):
        """Окно «Посты аккаунта» — быстрый список строк (tk.Listbox): дата,
        тип, статус, название. Без картинок и без сети: открывается мгновенно
        даже на сотни постов и не просаживает FPS (сетка плиток с превью и
        догрузка обложек по сети тормозили — убраны совсем).
        Клик по строке — подробности, двойной клик — открыть файл, правый
        клик — меню. «Перекачать» — единственный способ вернуть файл,
        удалённый из папки вручную: автоматом программа его НЕ качает.
        Диапазон аккаунта — постоянная задача, показан в шапке окна.
        Фильтры: Все / Скачанные / Очередь / Пропуск."""
        T = self.T
        acc_id = acc["id"]
        win = ctk.CTkToplevel(self, fg_color=T["bg"])
        hide_until_built(win)                       # покажем, когда всё построено
        win.after(80, lambda: show_built(win))
        win._acc_id = acc_id
        win.title(tr('Posts of @{0} — TokSaver', acc['nickname']))
        win.geometry("920x640")
        win.minsize(760, 480)

        def _raise():
            try:
                win.lift()
                win.focus_force()
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass
        win.after(150, _raise)

        state = {"posts": [], "by_id": {}, "rows": [], "filter": tr('All'),
                 "sort": self.settings.get("posts_sort", "new"), "sel_id": None,
                 "imgs": {}, "ready": 0, "warm": 0,
                 "view": "grid" if self.settings.get("posts_view") == "grid" else "list",
                 "g": None, "tiles": {}, "timgs": {}, "idx": {}, "redraw_job": None,
                 "no_pil": False}
        adir = db.account_dir(acc_id) or os.path.join(paths.DATA_DIR, "_orphan")
        cache_dir = os.path.join(adir, "thumbs")
        # единственный фоновый поток превью: окно само ничего не декодирует
        worker = thumbs.ThumbWorker(os.path.join(adir, "media"), cache_dir)
        worker.start()

        def _on_destroy(e):
            if e.widget is win:
                worker.stop()
        win.bind("<Destroy>", _on_destroy, add="+")

        # --- шапка (строки-контейнеры — tk.Frame, без холстов) ---
        head = tk.Frame(win, bg=T["bg"], bd=0, highlightthickness=0)
        head.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(head, text=tr('Posts of @{0}', acc['nickname']),
                     font=ctk.CTkFont(size=17, weight="bold"),
                     text_color=T["text"]).pack(side="left")
        ctk.CTkButton(head, text=tr('Close'), width=self._btn_w(tr('Close'), 12, min_w=78),
                      height=30, corner_radius=RADIUS, fg_color=T["field"],
                      hover_color=T["card_h"], text_color=T["text"],
                      border_width=1, border_color=T["line"],
                      command=win.destroy).pack(side="right")
        ctk.CTkButton(head, text=tr('Refresh'), width=self._btn_w(tr('Refresh'), 12, min_w=92),
                      height=30, corner_radius=RADIUS, fg_color=T["field"],
                      hover_color=T["card_h"], text_color=T["text"],
                      border_width=1, border_color=T["line"],
                      command=lambda: fill()).pack(side="right", padx=(0, 8))

        # --- фильтры (переносятся на новую строку, если окно узкое) ---
        filt = tk.Frame(win, bg=T["bg"], bd=0, highlightthickness=0)
        filt.pack(fill="x", padx=14, pady=(2, 6))
        flow = FlowRow(filt, gap_x=8, gap_y=6)
        flow.add(ctk.CTkLabel(filt, text=tr('Show:'), font=ctk.CTkFont(size=12),
                              text_color=T["muted"]), gap=(0, 0))
        fvar = ctk.StringVar(value=tr('All'))

        def _on_filter(_choice=None):
            state["reset_scroll"] = True
            state["filter"] = fvar.get()
            fill()

        flow.add(ctk.CTkSegmentedButton(
            filt, values=[tr('All'), tr('Downloaded'), tr('Queue'), tr('Skipped')], variable=fvar,
            command=_on_filter, height=30, corner_radius=RADIUS, font=ctk.CTkFont(size=12),
            fg_color=T["field"], selected_color=T["accent"],
            selected_hover_color=T["accent_h"], unselected_color=T["field"],
            unselected_hover_color=T["card_h"], text_color=T["text"]), gap=(0, 14))
        view_btn = ctk.CTkSegmentedButton(
            filt, values=[tr('List'), tr('Tiles')], command=lambda v: _on_view(v), height=30,
            corner_radius=RADIUS, font=ctk.CTkFont(size=12), fg_color=T["field"],
            selected_color=T["sel_line"], selected_hover_color=T["sel_line"],
            unselected_color=T["field"], unselected_hover_color=T["card_h"],
            text_color=T["text"])
        view_btn.set(tr('Tiles') if state["view"] == "grid" else tr('List'))
        flow.add(view_btn, gap=(0, 14))
        sort_var = ctk.StringVar(value=sorting.label_for(sorting.POST_SORTS, state["sort"]))

        def _on_sort(label):
            state["reset_scroll"] = True
            state["sort"] = sorting.key_for(sorting.POST_SORTS, label)
            self.settings["posts_sort"] = state["sort"]
            self._save_settings()
            fill()

        flow.add(ctk.CTkLabel(filt, text=tr('Sort:'), font=ctk.CTkFont(size=12),
                              text_color=T["muted"]), gap=(0, 0))
        flow.add(ctk.CTkOptionMenu(
            filt, values=[t for _k, t in sorting.POST_SORTS], variable=sort_var,
            command=_on_sort, width=190, height=30, corner_radius=RADIUS,
            font=ctk.CTkFont(size=12), fg_color=T["field"], button_color=T["card_h"],
            button_hover_color=T["sel_line"], text_color=T["text"],
            dropdown_fg_color=T["card"], dropdown_hover_color=T["card_h"],
            dropdown_text_color=T["text"]), gap=(0, 14))
        # диапазон аккаунта и счётчики — на своей строке, с переносом текста
        info_lbl = ctk.CTkLabel(filt, text="", font=ctk.CTkFont(size=11), text_color=T["muted"],
                                justify="left", anchor="w")
        rng_lbl = ctk.CTkLabel(filt, text="", font=ctk.CTkFont(size=11, weight="bold"),
                               text_color=T["accent"], justify="left", anchor="w")
        flow.add(info_lbl, gap=(0, 14))
        flow.add(rng_lbl, gap=(0, 0))

        # --- список постов: tk.Listbox — мгновенный даже на сотни строк ---
        wrap = tk.Frame(win, bg=T["card"], bd=0, highlightthickness=1,
                        highlightbackground=T["line"])
        wrap.pack(fill="both", expand=True, padx=14)
        pane_w = 216
        pane = tk.Frame(wrap, bg=T["thumb_bg"], width=pane_w, bd=0, highlightthickness=1,
                        highlightbackground=T["line"])
        pane.pack(side="right", fill="y", padx=(0, 4), pady=4)
        pane.pack_propagate(False)
        prev_lbl = tk.Label(pane, bg=T["thumb_bg"], fg=T["muted"], text=tr('Select a post\nin the list'),
                            wraplength=pane_w - 16, justify="center", bd=0)
        prev_lbl.pack(expand=True, fill="both")
        prev_stat = tk.Label(pane, bg=T["thumb_bg"], fg=T["muted"], text="", bd=0,
                             font=("TkDefaultFont", 8))
        prev_stat.pack(side="bottom", fill="x", pady=(0, 3))

        # два взаимозаменяемых вида: «Список» (быстрый текст) и «Плитки» (холст)
        view_list = tk.Frame(wrap, bg=T["card"], bd=0, highlightthickness=0)
        view_grid = tk.Frame(wrap, bg=T["card"], bd=0, highlightthickness=0)
        lb = tk.Listbox(view_list, bg=T["card"], fg=T["text"], selectbackground=T["accent"],
                        selectforeground=T["on_accent"], relief="flat", bd=0,
                        highlightthickness=0, activestyle="none", exportselection=False,
                        font=("TkFixedFont", 10))
        sb = FlatScrollbar(view_list, thumb=T["scroll"], thumb_h=T["scroll_h"],
                           command=lb.yview, bg=T["card"])
        sb.pack(side="right", fill="y", padx=(0, 3), pady=4)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)

        gcv = tk.Canvas(view_grid, bg=T["card"], bd=0, highlightthickness=0, takefocus=1,
                        yscrollincrement=40)
        gsb = FlatScrollbar(view_grid, thumb=T["scroll"], thumb_h=T["scroll_h"],
                            command=gcv.yview, bg=T["card"])
        gsb.pack(side="right", fill="y", padx=(0, 3), pady=4)
        gcv.configure(yscrollcommand=lambda a, b: _gyscroll(a, b))
        gcv.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)

        # --- подробности выбранного поста ---
        det_hint = (tr('Click a row for details, double-click to open the file, right-click for the menu'))
        det_box = tk.Frame(win, bg=T["bg"], bd=0, highlightthickness=0)
        det_box.pack(fill="x", padx=16, pady=(8, 0))
        det = ctk.CTkLabel(det_box, text=det_hint, justify="left", anchor="nw",
                           wraplength=600, font=ctk.CTkFont(size=12),
                           text_color=T["text"])
        det.pack(fill="x")
        # лента фото поста: все картинки карусели по порядку, с номерами
        strip_box = tk.Frame(det_box, bg=T["bg"], bd=0, highlightthickness=0)
        strip_lbl = tk.Label(strip_box, bg=T["bg"], fg=T["muted"], anchor="w",
                             font=(self._font_family, 10))
        strip_lbl.pack(fill="x")
        strip = tk.Canvas(strip_box, bg=T["bg"], height=76, bd=0, highlightthickness=0)
        strip.pack(fill="x", pady=(2, 0))
        strip_state = {"imgs": {}, "files": []}

        def _strip_wheel(e):
            n = -1 if (getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0) else 1
            strip.xview_scroll(n * 3, "units")
            return "break"

        def _strip_click(e):
            x = strip.canvasx(e.x)
            i = int(x // 72)
            files = strip_state["files"]
            if 0 <= i < len(files):
                self._open_path(files[i])

        strip.configure(xscrollincrement=24)
        strip.bind("<MouseWheel>", _strip_wheel)
        strip.bind("<Button-4>", _strip_wheel)
        strip.bind("<Button-5>", _strip_wheel)
        strip.bind("<Button-1>", _strip_click)

        def _thumb(path):
            img = strip_state["imgs"].get(path)
            if img is None:
                from PIL import Image, ImageOps, ImageTk
                im = Image.open(path)
                try:
                    im.draft("RGB", (128, 128))
                except Exception:
                    pass
                im = ImageOps.fit(im.convert("RGB"), (64, 64))
                img = strip_state["imgs"][path] = ImageTk.PhotoImage(im, master=win)
            return img

        def _show_strip(p):
            """Лента показывается только у фото-постов, у которых есть картинки на диске."""
            files = []
            if p and postmedia.is_photo(p, state.get("media")):
                files = postmedia.photo_files(os.path.join(adir, "media"), p["video_id"])
            strip_state["files"] = files
            strip.delete("all")
            if len(files) < 2:
                strip_box.pack_forget()
                return
            strip_lbl.configure(text=tr('Photos in this post: {0} (click one to open)', len(files)))
            for i, f in enumerate(files[:60]):
                try:
                    img = _thumb(f)
                except Exception as _exc:
                    _LOG.debug('strip thumb: %r', _exc)
                    continue
                x = i * 72
                strip.create_image(x + 4, 6, image=img, anchor="nw")
                strip.create_rectangle(x + 4, 6, x + 24, 22, fill=T["card"], outline="")
                strip.create_text(x + 14, 14, text=str(i + 1), fill=T["text"],
                                  font=(self._font_family, 9, "bold"))
            strip.configure(scrollregion=(0, 0, min(len(files), 60) * 72, 76))
            strip.xview_moveto(0)
            strip_box.pack(fill="x", pady=(6, 0))

        def _det_wrap(e):
            """Перенос текста по ширине КОНТЕЙНЕРА. Нельзя слушать сам CTkLabel:
            его ширина зависит от переноса, и wraplength начинал «качаться»
            туда-обратно (зависание при выборе поста с длинным текстом)."""
            w = max(240, e.width - 8)
            if abs(w - state.get("det_w", 0)) > 6:
                state["det_w"] = w
                det.configure(wraplength=w)
        det_box.bind("<Configure>", _det_wrap)

        # --- кнопки (тоже переносятся; подсказка про правый клик убрана — она в
        # строке подробностей, раньше текст налезал на кнопки) ---
        btns = tk.Frame(win, bg=T["bg"], bd=0, highlightthickness=0)
        btns.pack(fill="x", padx=14, pady=(6, 12))
        bflow = FlowRow(btns, gap_x=8, gap_y=8)
        # кнопки и подробности закрепляем у нижнего края ДО списка в порядке упаковки:
        # при нехватке высоты (лента фото) сжимается список, а не кнопки
        btns.pack_forget()
        det_box.pack_forget()
        wrap.pack_forget()
        btns.pack(side="bottom", fill="x", padx=14, pady=(6, 12))
        det_box.pack(side="bottom", fill="x", padx=16, pady=(8, 0))
        wrap.pack(fill="both", expand=True, padx=14)

        def _btn(label, cmd, color=None, min_w=104):
            b = ctk.CTkButton(btns, text=label, width=self._btn_w(label, 12, min_w=min_w),
                              height=32, corner_radius=RADIUS, fg_color=T["field"],
                              hover_color=T["card_h"], text_color=color or T["text"],
                              border_width=1, border_color=T["line"], command=cmd)
            bflow.add(b, gap=(0, 0))
            return b

        _btn(tr('Open file'), lambda: _open_post(), min_w=104)
        _btn(tr('Open on TikTok'), lambda: _open_tiktok(), min_w=130)
        _btn(tr('Re-download'), lambda: _redownload(), T["warn"])
        _btn(tr('Copy link'), lambda: _copy("link"), min_w=104)
        _btn(tr('Copy description'), lambda: _copy("desc"), min_w=140)
        _btn(tr('Rebuild previews'), lambda: _rebuild_thumbs(), min_w=140)

        # ---------------- логика окна ----------------
        def _status_meta(p):
            if p["downloaded"] == 1:
                if p["file_path"] and os.path.isfile(p["file_path"]):
                    return tr('downloaded'), T["ok"]
                return tr('no file'), T["danger"]   # удалён вручную — вернёт «Перекачать»
            if p["downloaded"] == 0:
                return tr('queued'), T["warn"]
            if p["downloaded"] == 2:
                return tr('skipped'), T["muted"]
            return "?", T["muted"]

        def _kind_label(p):
            """«photo×12» для каруселей — сразу видно, что это набор картинок."""
            if postmedia.is_photo(p, state.get("media")):
                n = postmedia.picture_count(p, state.get("media"))
                return tr('photo') + (f"×{n}" if n else "")
            return {"video": tr('video')}.get(p["kind"], tr('post'))

        def fill():
            """Перечитать базу и перерисовать строки (текст — мгновенно)."""
            if not win.winfo_exists():
                return
            state["posts"] = db.db_posts(acc_id)
            state["media"] = postmedia.scan(os.path.join(adir, "media"),
                                            {q["video_id"] for q in state["posts"]})
            state["by_id"] = {p["video_id"]: p for p in state["posts"]}
            want = {tr('All'): None, tr('Downloaded'): 1, tr('Queue'): 0, tr('Skipped'): 2}.get(
                state["filter"])
            shown = [p for p in state["posts"] if want is None or p["downloaded"] == want]
            shown = sorting.sort_posts(shown, state["sort"])
            state["rows"] = shown
            lb.delete(0, "end")
            for i, p in enumerate(shown):
                st_word, st_color = _status_meta(p)
                kind = _kind_label(p)
                title = (p["title"] or "").replace("\n", " ").strip()
                if len(title) > 56:
                    title = title[:55] + "…"
                date = fmt_date(p["upload_date"]) or "—"
                lb.insert("end", f"{date:>10}  {kind:<10}{st_word:<10} {title}")
                fg = T["text"] if st_word == tr('downloaded') else st_color
                try:
                    lb.itemconfigure(i, fg=fg)
                except Exception as _exc:
                    _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                    pass
            total, done, pend, skipped = db.db_counts(acc_id)
            info = tr('total {0}   ·   downloaded {1}   ·   queued {2}', total, done, pend)
            if skipped:
                info += tr('   ·   outside range {0}', skipped)
            info_lbl.configure(text=info)
            acc2 = db.db_get_account(acc_id)
            rng_lbl.configure(text=self._range_text(acc2) or "")
            try:
                lb.yview_moveto(0.0)
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                pass
            state["idx"] = {p["video_id"]: i for i, p in enumerate(shown)}
            if state["view"] == "grid":
                _grid_layout(keep_scroll=not state.pop("reset_scroll", False))
            else:
                state.pop("reset_scroll", None)
            _warm(shown)
            keep = state.get("sel_id")
            idx = next((i for i, p in enumerate(shown) if p["video_id"] == keep), None)
            if idx is not None:                       # выбранный пост остаётся выбранным
                lb.selection_set(idx)
                lb.see(idx)
                _details()
            else:
                det.configure(text=det_hint)

        def _warm(rows):
            """Прогрев кэша: фоновому потоку отдаются скачанные посты по порядку списка."""
            n = 0
            for p in rows:
                if p["downloaded"] == 1:
                    worker.request(p["video_id"])
                    n += 1
            state["warm"] = n
            state["ready"] = sum(1 for p in rows if p["downloaded"] == 1 and os.path.isfile(
                thumbs.cache_path(cache_dir, p["video_id"])))
            _stat()

        def _stat():
            prev_stat.configure(text=(tr('previews: {0} of {1}', state['ready'], state['warm'])
                                      if state["warm"] else ""))

        def _set_image(path):
            """Показывает превью из кэша. Маленький JPEG открывается за миллисекунды;
            недавние картинки хранятся в небольшом LRU для быстрой навигации стрелками."""
            try:
                from PIL import Image, ImageTk
            except ImportError:
                prev_lbl.configure(image="", text=tr('Preview unavailable:\nPillow is not installed'))
                return
            try:
                key = (path, os.path.getmtime(path))
                img = state["imgs"].pop(key, None)
                if img is None:
                    with Image.open(path) as im:
                        img = ImageTk.PhotoImage(im.copy())
                state["imgs"][key] = img                 # свежие — в конец
                while len(state["imgs"]) > 24:
                    state["imgs"].pop(next(iter(state["imgs"])))
                prev_lbl.configure(image=img, text="")
                prev_lbl.image = img
            except Exception as _exc:
                _LOG.debug('preview not shown: %r', _exc)
                prev_lbl.configure(image="", text=tr('Could not open the preview'))

        def _show_preview(p):
            state["sel_id"] = p["video_id"] if p else None
            if not p:
                prev_lbl.configure(image="", text=tr('Select a post\nin the list'))
                return
            cp = thumbs.cache_path(cache_dir, p["video_id"])
            if os.path.isfile(cp):
                _set_image(cp)
            elif p["downloaded"] == 1:
                prev_lbl.configure(image="", text=tr('Preparing the preview…'))
                worker.request(p["video_id"], thumbs.PRIORITY_NOW)   # вне общей очереди
            else:
                prev_lbl.configure(image="", text=tr('Post not downloaded —\nno preview'))

        def _pump():
            """Забирает готовые превью у фонового потока (по 30 за раз — без подвисаний)."""
            try:
                if not win.winfo_exists():
                    return
            except Exception:
                return
            changed = False
            for _ in range(30):
                try:
                    vid, path = worker.results.get_nowait()
                except Exception:
                    break
                changed = True
                if path:
                    state["ready"] = min(state["warm"], state["ready"] + 1)
                if state["view"] == "grid":
                    _grid_retile(state["idx"].get(vid))
                if vid == state["sel_id"]:
                    if path:
                        _set_image(path)
                    else:
                        prev_lbl.configure(image="", text=tr('No preview\n(no picture in the folder)'))
            if changed:
                _stat()
            win.after(150, _pump)

        def _rebuild_thumbs():
            """Удаляет кэш превью и строит заново (если картинки в папке обновились)."""
            try:
                for name in os.listdir(cache_dir):
                    if name.endswith(".jpg"):
                        os.remove(os.path.join(cache_dir, name))
            except OSError as _exc:
                _LOG.debug('preview cache cleanup: %r', _exc)
            state["imgs"].clear()
            state["ready"] = 0
            _warm(state["rows"])
            _show_preview(_selected())
            self._toast(tr('Previews are being recreated in the background'), "info")

        def _selected():
            if state["view"] == "grid":
                return state["by_id"].get(state.get("sel_id"))
            sel = lb.curselection()
            if not sel:
                return None
            try:
                return state["rows"][sel[0]]
            except IndexError:
                return None

        def _details(_e=None):
            p = _selected()
            _show_preview(p)
            _show_strip(p)
            if not p:
                det.configure(text="")
                return
            st_word, _c = _status_meta(p)
            parts = [p["title"] or tr('(untitled)')]
            n = postmedia.picture_count(p, state.get("media"))
            kind_txt = (tr('{0} photos', n) if n else tr('photo')) \
                if postmedia.is_photo(p, state.get("media")) else tr('video')
            parts.append(f"{fmt_date(p['upload_date']) or tr('date unknown')}   ·   "
                         f"{kind_txt}   ·   {st_word}   ·   id {p['video_id']}")
            if p.get("description"):
                d = p["description"]
                parts.append("")
                parts.append(d if len(d) <= 800 else d[:799] + "…")
            if p["downloaded"] == 1:
                fp = p["file_path"]
                ok = bool(fp) and os.path.isfile(fp)
                parts.append("")
                parts.append(tr('File: {0}', fp or '—')
                             + ("" if ok else tr('   (file missing — “Re-download” will bring it back)')))
            det.configure(text="\n".join(parts))

        def _open_post(vid=None):
            """Открыть ФАЙЛ поста (у каруселей — первую картинку, а не музыку)."""
            p = state["by_id"].get(vid) if vid else _selected()
            if not p:
                return
            if p["downloaded"] != 1:
                _open_tiktok(p["video_id"])          # файла ещё нет — открываем сам пост
                return
            fp = p["file_path"]
            if postmedia.is_photo(p, state.get("media")):
                pics = postmedia.photo_files(os.path.join(adir, "media"), p["video_id"])
                if pics:
                    fp = pics[0]
            if fp and os.path.isfile(fp):
                self._open_path(fp)
            else:
                self._toast(tr('The file is not on disk — “Re-download” will bring it back'), "warn")

        def _open_tiktok(vid=None):
            """Открыть пост на сайте TikTok. Ник берём из базы (на случай смены),
            для каруселей — адрес /photo/, для видео — /video/."""
            p = state["by_id"].get(vid) if vid else _selected()
            if not p:
                return
            a2 = db.db_get_account(acc_id) or acc
            url = postmedia.tiktok_url(a2["nickname"], p["video_id"],
                                       postmedia.is_photo(p, state.get("media")))
            try:
                webbrowser.open(url)
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                self._append_log(LEVEL_ERROR,
                                 tr('[System] Could not open the link in the browser.'))

        def _redownload(vid=None):
            p = state["by_id"].get(vid) if vid else _selected()
            if not p:
                return
            if self._busy:
                self._toast(tr('Another task is running — try later'), "info")
                return
            self._set_busy(True)
            threading.Thread(target=self.engine.task_redownload,
                             args=(acc_id, p["video_id"]), daemon=True).start()

        def _copy(what, vid=None):
            p = state["by_id"].get(vid) if vid else _selected()
            if not p:
                return
            if what == "link":
                a2 = db.db_get_account(acc_id) or acc
                text = postmedia.tiktok_url(a2["nickname"], p["video_id"],
                                            postmedia.is_photo(p, state.get("media")))
            elif what == "desc":
                text = (p.get("description") or p.get("title") or "").strip()
                if not text:
                    self._toast(tr('This post has no description'), "info")
                    return
            else:
                text = p.get("title") or ""
            self.clipboard_clear()
            self.clipboard_append(text)
            self._toast(tr('Copied'), "ok")

        def _menu(event):
            if state["view"] == "list":            # выбираем строку под курсором
                i = lb.nearest(event.y)
                if 0 <= i < lb.size():
                    lb.selection_clear(0, "end")
                    lb.selection_set(i)
                    _details()
            menu = PopupMenu(win, T, self._font_family)
            menu.add_command(label=tr('Open file'), command=_open_post)
            menu.add_command(label=tr('Open on TikTok'), command=_open_tiktok)
            menu.add_command(label=tr('Re-download post'), command=_redownload)
            menu.add_command(label=tr('Copy link'), command=lambda: _copy("link"))
            menu.add_command(label=tr('Copy description'), command=lambda: _copy("desc"))
            menu.add_command(label=tr('Copy title'), command=lambda: _copy("title"))
            menu.add_command(label=tr('Open account folder'),
                             command=lambda: self.on_open_account(acc_id))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        # ---------------- плитки (виртуальная отрисовка на холсте) ----------------
        def _gyscroll(a, b):
            gsb.set(a, b)
            _schedule_redraw()

        def _schedule_redraw(delay=1):
            """Слияние перерисовок: сколько бы раз ни дёрнули прокрутку, за кадр
            перерисовка одна."""
            if state["redraw_job"] is None and state["view"] == "grid":
                try:
                    state["redraw_job"] = win.after(delay, _grid_redraw)
                except Exception as _exc:
                    _LOG.debug("after: %r", _exc)

        def _grid_layout(keep_scroll=True):
            """Пересчёт раскладки (изменилось окно или список): холст очищается,
            плитки появятся при перерисовке — создаются только видимые."""
            w, h = gcv.winfo_width(), gcv.winfo_height()
            old_top = gcv.canvasy(0) if state["g"] is not None else 0
            g = tilegrid.Grid(w, len(state["rows"]))
            state["g"] = g
            gcv.delete("all")
            state["tiles"].clear()
            total = max(g.height, h)
            gcv.configure(scrollregion=(0, 0, g.width, total))
            top = min(old_top if keep_scroll else 0, max(0, total - h))
            gcv.yview_moveto(top / total if total else 0)
            _schedule_redraw()

        def _timg(path, budget):
            """PhotoImage плитки из кэша превью. Не больше budget[0] новых картинок
            за проход — прокрутка не подвисает, остальное дорисуется следующим кадром."""
            try:
                key = (path, os.path.getmtime(path), "tile")
            except OSError:
                return None
            img = state["timgs"].pop(key, None)
            if img is None:
                if budget[0] <= 0:
                    return None
                budget[0] -= 1
                try:
                    from PIL import Image, ImageTk
                except ImportError:
                    state["no_pil"] = True
                    return None
                try:
                    with Image.open(path) as im:
                        im = im.convert("RGB")
                        im.thumbnail((tilegrid.IMG_W, tilegrid.IMG_H), Image.BILINEAR)
                        img = ImageTk.PhotoImage(im)
                except Exception as _exc:
                    _LOG.debug('tile %s: %r', path, _exc)
                    return None
            state["timgs"][key] = img
            while len(state["timgs"]) > 160:                  # небольшой LRU
                state["timgs"].pop(next(iter(state["timgs"])))
            return img

        def _draw_tile(i, budget):
            """Рисует плитку i. True — окончательно, False — нужна дорисовка
            (не хватило бюджета загрузки картинок)."""
            g = state["g"]
            p = state["rows"][i]
            x0, y0, x1, y1 = g.rect(i)
            tag = f"t{i}"
            sel = p["video_id"] == state["sel_id"]
            gcv.create_rectangle(x0, y0, x1, y1, fill=T["field"], tags=tag,
                                 outline=T["accent"] if sel else T["line"],
                                 width=3 if sel else 1)
            ix = x0 + (tilegrid.TILE_W - tilegrid.IMG_W) // 2
            iy = y0 + 6
            gcv.create_rectangle(ix, iy, ix + tilegrid.IMG_W, iy + tilegrid.IMG_H,
                                 fill=T["thumb_bg"], outline="", tags=tag)
            st_word, st_color = _status_meta(p)
            final, photo, note = True, None, ""
            if p["downloaded"] == 1:
                cp = thumbs.cache_path(cache_dir, p["video_id"])
                if os.path.isfile(cp):
                    photo = _timg(cp, budget)
                    if photo is None:
                        if state["no_pil"]:
                            note = tr('no Pillow')
                        elif budget[0] <= 0:
                            final, note = False, "…"
                        else:
                            note = tr('no preview')
                else:
                    note = "…"
                    worker.request(p["video_id"], thumbs.PRIORITY_VISIBLE)   # видимые — вне очереди
            else:
                note = {0: tr('in queue'), 2: tr('skipped')}.get(p["downloaded"], "")
            cx, cy = ix + tilegrid.IMG_W // 2, iy + tilegrid.IMG_H // 2
            if photo is not None:
                gcv.create_image(cx, cy, image=photo, tags=tag)
            elif note:
                gcv.create_text(cx, cy, text=note, fill=T["muted"], tags=tag,
                                font=("TkDefaultFont", 9))
            if postmedia.is_photo(p, state.get("media")):
                n = postmedia.picture_count(p, state.get("media"))
                label = tr('{0} photos', n) if n else tr('photo')
                bw = 8 + 7 * len(label)
                gcv.create_rectangle(ix + 6, iy + 6, ix + 6 + bw, iy + 26, fill=T["bg"],
                                     outline=T["line"], tags=tag)
                gcv.create_text(ix + 6 + bw // 2, iy + 16, text=label, fill=T["text"],
                                font=("TkDefaultFont", 8, "bold"), tags=tag)
            ty = iy + tilegrid.IMG_H + 6
            gcv.create_text(x0 + 6, ty, anchor="nw", tags=tag, fill=T["muted"],
                            font=("TkDefaultFont", 8), text=fmt_date(p["upload_date"]) or "—")
            gcv.create_text(x1 - 6, ty, anchor="ne", tags=tag, fill=st_color,
                            font=("TkDefaultFont", 8), text=st_word)
            title = (p["title"] or "").replace("\n", " ").strip()
            if len(title) > 44:
                title = title[:43] + "…"
            gcv.create_text(x0 + 6, ty + 15, anchor="nw", tags=tag, fill=T["text"],
                            width=tilegrid.TILE_W - 12, font=("TkDefaultFont", 9),
                            text=title or tr('(untitled)'))
            return final

        def _grid_redraw():
            state["redraw_job"] = None
            try:
                if state["view"] != "grid" or not win.winfo_exists():
                    return
            except Exception:
                return
            g = state["g"]
            if g is None:
                return
            top, h = gcv.canvasy(0), max(1, gcv.winfo_height())
            want = set(g.visible(top, h))
            for i in [i for i in state["tiles"] if i not in want]:   # ушедшие за экран — удаляем
                gcv.delete(f"t{i}")
                del state["tiles"][i]
            budget = [8]
            unfinished = False
            for i in sorted(want):
                if state["tiles"].get(i) is True:
                    continue
                gcv.delete(f"t{i}")
                done = _draw_tile(i, budget)
                state["tiles"][i] = done
                unfinished = unfinished or not done
            if unfinished:
                _schedule_redraw(25)

        def _grid_retile(i):
            """Перерисовать одну плитку (выбор, пришло превью)."""
            if i is None or state["g"] is None or i not in state["tiles"]:
                return
            gcv.delete(f"t{i}")
            state["tiles"][i] = _draw_tile(i, [4])

        def _grid_select(i, focus=True):
            if i is None or not (0 <= i < len(state["rows"])):
                return
            old = state["idx"].get(state.get("sel_id"))
            state["sel_id"] = state["rows"][i]["video_id"]
            _grid_retile(old)
            _grid_retile(i)
            if focus:
                gcv.focus_set()
            _details()

        def _grid_click(e):
            g = state["g"]
            if g is not None:
                _grid_select(g.index_at(gcv.canvasx(e.x), gcv.canvasy(e.y)))
            else:
                gcv.focus_set()

        def _grid_double(e):
            _grid_click(e)
            if _selected():
                _open_post()

        def _grid_menu(e):
            _grid_click(e)
            if _selected():
                _menu(e)

        def _grid_key(dcol, drow):
            def handler(_e):
                g = state["g"]
                if g is None or not state["rows"]:
                    return "break"
                cur = state["idx"].get(state.get("sel_id"))
                new = g.move(cur, dcol, drow)
                _grid_select(new, focus=False)
                top = g.scroll_to_show(new, gcv.canvasy(0), max(1, gcv.winfo_height()))
                total = max(g.height, gcv.winfo_height())
                gcv.yview_moveto(top / total)
                return "break"
            return handler

        def _grid_wheel(e):
            if getattr(e, "num", 0) == 4:
                n = -3
            elif getattr(e, "num", 0) == 5:
                n = 3
            else:
                n = -3 if e.delta > 0 else 3
            gcv.yview_scroll(n, "units")
            return "break"

        def _grid_configure(e):
            g = state["g"]
            if g is None or e.width != g.width:
                _grid_layout()                      # другая ширина — другое число колонок
            else:
                gcv.configure(scrollregion=(0, 0, g.width, max(g.height, e.height)))
                _schedule_redraw()

        gcv.bind("<Configure>", _grid_configure)
        gcv.bind("<Button-1>", _grid_click)
        gcv.bind("<Double-Button-1>", _grid_double)
        gcv.bind("<Button-3>", _grid_menu)
        gcv.bind("<MouseWheel>", _grid_wheel)
        gcv.bind("<Button-4>", _grid_wheel)
        gcv.bind("<Button-5>", _grid_wheel)
        gcv.bind("<Left>", _grid_key(-1, 0))
        gcv.bind("<Right>", _grid_key(1, 0))
        gcv.bind("<Up>", _grid_key(0, -1))
        gcv.bind("<Down>", _grid_key(0, 1))
        gcv.bind("<Return>", lambda _e: _open_post())

        def _apply_view():
            """Показывает выбранный вид. Выбранный пост сохраняется при переключении."""
            view_list.pack_forget()
            view_grid.pack_forget()
            if state["view"] == "grid":
                view_grid.pack(side="left", fill="both", expand=True)
                _grid_layout(keep_scroll=False)
                idx = state["idx"].get(state.get("sel_id"))
                if idx is not None and state["g"] is not None:
                    top = state["g"].scroll_to_show(idx, 0, max(1, gcv.winfo_height()))
                    gcv.yview_moveto(top / max(1, state["g"].height))
            else:
                view_list.pack(side="left", fill="both", expand=True)
                idx = state["idx"].get(state.get("sel_id"))
                lb.selection_clear(0, "end")
                if idx is not None:
                    lb.selection_set(idx)
                    lb.see(idx)

        def _on_view(label):
            state["view"] = "grid" if label == tr('Tiles') else "list"
            self.settings["posts_view"] = state["view"]
            self._save_settings()
            _apply_view()

        lb.bind("<<ListboxSelect>>", _details)
        lb.bind("<Double-Button-1>", lambda _e: _open_post())
        lb.bind("<Button-3>", _menu)

        # События движка: превью постов больше не догружаем — обработчики-пустышки
        # (vcov больше не приходит). posts_dirty перечитывает базу сам.
        win.on_cover = lambda vid, path: None
        win.on_cover_done = lambda: None
        win.refresh = fill
        # чистим превью удалённых постов (один раз при открытии окна)
        thumbs.prune(cache_dir, {q["video_id"] for q in db.db_posts(acc_id)})
        _apply_view()
        fill()
        win.after(150, _pump)
        return win
