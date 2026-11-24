"""Свои плоские скроллбары TokSaver (FlatScrollbar / FlatScroll)."""
from i18n import tr
import tkinter as tk

import applog

_LOG = applog.get(__name__)


# ------------------------------ скроллбары ------------------------------
# Встроенные ползунки customtkinter рисуются на холсте и выглядят криво:
# широкий «хвост» на всю высоту, чужие отступы, нет пропорции к содержимому.
# Заменяем на свои: тонкая плоская полоса, бегунок строго пропорционален
# видимой части, перетаскивается мышью, клик по желобу — постраничный прыжок.
class FlatScrollbar(tk.Canvas):
    """Плоский вертикальный скроллбар на tk.Canvas.

    set(first, last) — стандартный протокол (подключается к yscrollcommand),
    command("moveto", f) / command("scroll", n, "pages") — прокрутка вью.
    """

    def __init__(self, master, thumb, thumb_h, command, width=10, bg="#000000", **kw):
        self.thumb_color = thumb
        self.thumb_hover = thumb_h
        self._cmd = command
        self._bw = width
        self._lo, self._hi = 0.0, 1.0
        self._dragging = False
        self._drag_off = 0
        super().__init__(master, width=width, highlightthickness=0, bd=0,
                         bg=bg, **kw)
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", self._enter, add="+")
        self.bind("<Leave>", self._leave, add="+")
        self.bind("<Button-1>", self._press, add="+")
        self.bind("<B1-Motion>", self._motion, add="+")
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_dragging", False),
                  add="+")

    # --- подключение к прокручиваемому виджету ---
    def set(self, first, last):
        try:
            first, last = float(first), float(last)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            return
        first, last = max(0.0, first), min(1.0, last)
        if (first, last) == (self._lo, self._hi):
            return   # положение не изменилось — перерисовка не нужна (анти-тормоз)
        self._lo, self._hi = first, last
        self._redraw()

    # --- отрисовка ---
    def _thumb_px(self):
        """Границы бегунка в пикселях (учитывая минимум 30px)."""
        h = float(max(self.winfo_height(), 1))
        frac = max(self._hi - self._lo, 0.02)
        y0, y1 = self._lo * h, (self._lo + frac) * h
        if y1 - y0 < 30:
            mid = (y0 + y1) / 2
            y0, y1 = max(0, mid - 15), min(h, mid + 15)
        return y0, y1

    def _redraw(self):
        self.delete("all")
        if self._hi - self._lo >= 0.999:
            return  # всё видно — ползунок не нужен
        y0, y1 = self._thumb_px()
        self.create_rectangle(1, y0, self._bw - 1, y1,
                              fill=self.thumb_color, width=0, tags="thumb")

    # --- мышь ---
    def _enter(self, _e):
        self.itemconfig("thumb", fill=self.thumb_hover)

    def _leave(self, _e):
        self.itemconfig("thumb", fill=self.thumb_color)

    def _press(self, event):
        if self._hi - self._lo >= 0.999:
            return
        y0, y1 = self._thumb_px()
        if y0 <= event.y <= y1:
            self._dragging = True
            self._drag_off = event.y - y0
        else:
            self._cmd("scroll", 1 if event.y > y1 else -1, "pages")

    def _motion(self, event):
        if not self._dragging:
            return
        h = float(max(self.winfo_height(), 1))
        frac = max(self._hi - self._lo, 0.02)
        lo = min(max((event.y - self._drag_off) / h, 0.0), 1.0 - frac)
        self._cmd("moveto", lo)


class FlatScroll(tk.Frame):
    """Прокручиваемая зона: холст + плоский скроллбар + content-frame .body.

    Заменяет CTkScrollableFrame: колёсико работает над любым дочерним виджетом
    (роутится глобально через App._on_wheel), ширина .body всегда равна ширине
    холста, скроллбар тонкий и пропорциональный.
    """

    def __init__(self, master, bg, thumb, thumb_h, gutter=0, **kw):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                yscrollincrement=1)
        self.vsb = FlatScrollbar(self, thumb=thumb, thumb_h=thumb_h,
                                 command=self._sb_cmd, bg=bg)
        # gutter — зазор между содержимым и ползунком: без него ползунок
        # прилипает к элементам и выглядит частью колонки текста
        self.vsb.pack(side="right", fill="y", padx=(gutter, 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>",
                       lambda _e: self.canvas.configure(
                           scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._fit_width, add="+")

    def _fit_width(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _sb_cmd(self, *args):
        if args[0] == "moveto":
            self.canvas.yview_moveto(args[1])
        else:
            self.canvas.yview_scroll(args[1], args[2])

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def wheel_scroll(self, event):
        """Прокрутка колесом (вызывается глобальным роутером App._on_wheel)."""
        num = getattr(event, "num", None)
        delta = getattr(event, "delta", 0) or 0
        if num == 4 or delta > 0:
            self.canvas.yview_scroll(-48, "units")
        elif num == 5 or delta < 0:
            self.canvas.yview_scroll(48, "units")


# ------------------------------ стек с виртуализацией ------------------------------
class VStack(FlatScroll):
    """Вертикальный список «карточек» для длинных списков.

    Каждая карточка — отдельный элемент холста (create_window), а не ребёнок
    общего фрейма. Карточки вне видимой области СКРЫТЫ (state="hidden"): Tk не
    двигает и не перерисовывает их при прокрутке. Раньше на каждую карточку
    приходилось ~50 дочерних окон, и прокрутка двигала их все — отсюда «рассыпание».
    Сами виджеты живут постоянно, поэтому состояние карточек (статус, прогресс)
    обновляется как обычно.

    Использование: родитель карточки — vstack.body (это сам холст), затем
    vstack.add(widget); порядок — set_order([...]).
    """

    GAP = 10
    SIDE = 2
    MARGIN = 360          # сколько пикселей вокруг окна просмотра держать показанными

    def __init__(self, master, bg, thumb, thumb_h, gutter=0, **kw):
        tk.Frame.__init__(self, master, bg=bg, highlightthickness=0, bd=0, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=1)
        self.vsb = FlatScrollbar(self, thumb=thumb, thumb_h=thumb_h,
                                 command=self._sb_cmd, bg=bg)
        self.vsb.pack(side="right", fill="y", padx=(gutter, 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.body = self.canvas
        self._items = []          # [(widget, item_id)] в порядке показа
        self._layout_sig = None
        self._job = None
        self._vis_job = None
        self._rechecked = False
        self.on_show = None       # вызывается, когда карточка становится видимой
        self.canvas.bind("<Configure>", lambda _e: self._schedule_layout(), add="+")

    # --- плавная прокрутка колесом ---
    def wheel_scroll(self, event):
        """Колесо копит «долг» прокрутки, а кадры (~60/с) отрабатывают его порциями
        с принудительной дорисовкой. Раньше каждое деление колеса мгновенно
        сдвигало сразу всё окно на 48 px, а поток событий опережал отрисовку —
        на Windows это давало «смазывание» и наложение карточек."""
        num = getattr(event, "num", None)
        delta = getattr(event, "delta", 0) or 0
        if num == 4 or delta > 0:
            sign = -1
        elif num == 5 or delta < 0:
            sign = 1
        else:
            return
        notches = min(3.0, abs(delta) / 120.0) if delta else 1.0
        self._debt = getattr(self, "_debt", 0.0) + sign * 96 * max(0.25, notches)
        self._debt = max(-1500.0, min(1500.0, self._debt))
        if getattr(self, "_anim", None) is None:
            self._anim = self.after(0, self._scroll_step)

    def _scroll_step(self):
        self._anim = None
        debt = getattr(self, "_debt", 0.0)
        if abs(debt) < 1:
            self._debt = 0.0
            return
        mag = abs(debt)
        px = max(1, int(min(mag, max(4.0, min(mag * 0.3, 70)))))
        px = px if debt > 0 else -px
        try:
            self.canvas.yview_scroll(px, "units")
            self._debt = debt - px
            self.update_idletasks()               # дорисовать до следующего шага
            self._anim = self.after(14, self._scroll_step)
        except tk.TclError:
            self._debt = 0.0

    # --- наполнение ---
    def add(self, widget):
        item = self.canvas.create_window(0, 0, window=widget, anchor="nw", state="hidden")
        self._items.append((widget, item))
        widget.bind("<Configure>", lambda _e: self._schedule_layout(), add="+")
        self._schedule_layout()

    def set_order(self, widgets):
        by = {id(w): (w, i) for w, i in self._items}
        self._items = [by[id(w)] for w in widgets if id(w) in by]
        self._layout_sig = None
        self._schedule_layout()

    def remove(self, widget):
        keep = []
        for w, item in self._items:
            if w is widget:
                self.canvas.delete(item)
            else:
                keep.append((w, item))
        self._items = keep
        self._layout_sig = None
        self._schedule_layout()

    def clear(self):
        for w, item in self._items:
            self.canvas.delete(item)
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._items = []
        self._layout_sig = None
        self._schedule_layout()

    # --- раскладка и видимость ---
    def _schedule_layout(self):
        if self._job is None:
            try:
                self._job = self.after_idle(self._layout)
            except tk.TclError:
                self._job = None

    def _on_yscroll(self, a, b):
        self.vsb.set(a, b)
        if self._vis_job is None:
            try:
                self._vis_job = self.after_idle(self._update_visibility)
            except tk.TclError:
                self._vis_job = None

    def _layout(self):
        self._job = None
        try:
            width = self.canvas.winfo_width()
            view_h = self.canvas.winfo_height()
        except tk.TclError:
            return
        self.update_idletasks()          # геометрия карточек должна быть досчитана
        heights = []
        for w, _i in self._items:
            try:
                heights.append(w.winfo_reqheight())
            except tk.TclError:
                heights.append(0)
        sig = (width, view_h, tuple(heights), tuple(id(w) for w, _i in self._items))
        if sig == self._layout_sig:
            return
        self._layout_sig = sig
        y = 0
        self._ys = []
        for (w, item), h in zip(self._items, heights):
            self.canvas.coords(item, self.SIDE, y)
            self.canvas.itemconfigure(item, width=max(1, width - 2 * self.SIDE))
            self._ys.append((y, h))
            y += h + self.GAP
        total = max(0, y - self.GAP)
        self.canvas.configure(scrollregion=(0, 0, width, max(total, view_h)))
        self._update_visibility()
        # страховка: масштабирование/шрифты Windows доводят размеры уже после
        # первой раскладки — один повторный пересчёт чуть позже (без зацикливания)
        if not self._rechecked:
            self._rechecked = True
            self.after(350, self._recheck_layout)

    def _recheck_layout(self):
        self._layout_sig = None
        self._schedule_layout()

    def _update_visibility(self):
        self._vis_job = None
        try:
            top = self.canvas.canvasy(0)
            view_h = self.canvas.winfo_height()
        except tk.TclError:
            return
        lo, hi = top - self.MARGIN, top + view_h + self.MARGIN
        for (w, item), (y, h) in zip(self._items, getattr(self, "_ys", [])):
            want = "normal" if (y + h >= lo and y <= hi) else "hidden"
            if self.canvas.itemcget(item, "state") != want:
                self.canvas.itemconfigure(item, state=want)
                if want == "normal" and self.on_show is not None:
                    self.on_show(w)
            if want == "normal":
                try:
                    stale = w.winfo_reqheight() != h
                except tk.TclError:
                    stale = False
                if stale:                  # карточка выросла/уменьшилась после раскладки
                    self._layout_sig = None
                    self._schedule_layout()

    def is_shown(self, widget):
        for w, item in self._items:
            if w is widget:
                return self.canvas.itemcget(item, "state") == "normal"
        return False


# ------------------------------ строка с переносом ------------------------------
class FlowRow:
    """Раскладывает виджеты слева направо и переносит на новую строку, когда
    не хватает ширины — ничего не налезает друг на друга при любом размере окна.
    Виджеты должны быть созданы с родителем-контейнером и добавлены через add()."""

    def __init__(self, container, gap_x=8, gap_y=6):
        self.c = container
        self.gap_x, self.gap_y = gap_x, gap_y
        self.items = []          # (widget, (pad_left, pad_right))
        self._sig = None
        self._job = None
        container.bind("<Configure>", self._on_conf, add="+")

    def add(self, widget, gap=(0, 0)):
        self.items.append((widget, gap))
        self._sig = None
        self._schedule()
        return widget

    def _on_conf(self, _e=None):
        self._schedule()

    def _schedule(self):
        if self._job is None:
            try:
                self._job = self.c.after_idle(self._layout)
            except tk.TclError:
                self._job = None

    def _layout(self):
        self._job = None
        try:
            width = self.c.winfo_width()
        except tk.TclError:
            return
        if width <= 1:
            width = 10 ** 6                        # ещё не показано — одной строкой
        rows, cur, used = [], [], 0
        for w, (pl, pr) in self.items:
            need = w.winfo_reqwidth() + pl + pr + (self.gap_x if cur else 0)
            if cur and used + need > width:
                rows.append(cur)
                cur, used = [], 0
                need = w.winfo_reqwidth() + pl + pr
            cur.append((w, pl, pr))
            used += need
        if cur:
            rows.append(cur)
        sig = (width if len(rows) > 1 else 0,
               tuple(tuple(id(w) for w, _l, _r in r) for r in rows),
               tuple(w.winfo_reqwidth() for w, _g in self.items))
        if sig == self._sig:
            return
        self._sig = sig
        # place() с вычисленными координатами: у grid колонки общие для всех
        # строк и «растягивали» верхнюю строку по ширине нижней
        y = 0
        for row in rows:
            x = 0
            rh = max(w.winfo_reqheight() for w, _l, _r in row)
            for w, pl, pr in row:
                x += pl
                w.place(x=x, y=y + (rh - w.winfo_reqheight()) // 2)
                x += w.winfo_reqwidth() + pr + self.gap_x
            y += rh + self.gap_y
        self.c.configure(height=max(1, y - self.gap_y))


# ------------------------------ плавное появление окон ------------------------------
def hide_until_built(win):
    """Окно строится невидимым (alpha 0): пользователь не видит поэтапной
    отрисовки виджетов. Парный вызов — show_built(win)."""
    try:
        win.attributes("-alpha", 0.0)
    except tk.TclError:
        pass


def show_built(win):
    try:
        win.update_idletasks()
        win.attributes("-alpha", 1.0)
    except tk.TclError:
        pass


# ------------------------------ контекстное меню ------------------------------
class PopupMenu:
    """Контекстное меню с крупными строками (замена tk.Menu: у системного меню
    мелкий шрифт и тесные пункты, размер не настраивается).

    Совместимо по методам: add_command / add_separator / tk_popup / grab_release.
    Закрывается по Esc, клику мимо, потере фокуса и выбору пункта.
    """

    ROW_H = 38
    PAD_X = 18

    def __init__(self, master, T, font_family="Segoe UI", font_size=13, min_width=260):
        self.master = master
        self.T = T
        self.font = (font_family, font_size)
        self.min_width = min_width
        self._items = []          # (label, command, danger) | None для разделителя
        self._win = None
        self._click_id = None

    # --- наполнение ---
    def add_command(self, label, command, danger=False):
        self._items.append((label, command, danger))

    def add_separator(self):
        if self._items and self._items[-1] is not None:
            self._items.append(None)

    # --- показ ---
    def tk_popup(self, x, y):
        self.close()
        T = self.T
        root = self.master.winfo_toplevel()
        win = tk.Toplevel(root)
        win.withdraw()
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        win.configure(bg=T["line"])
        body = tk.Frame(win, bg=T["card"], bd=0)
        body.pack(padx=1, pady=1, fill="both", expand=True)
        rows = []
        items = list(self._items)
        while items and items[-1] is None:
            items.pop()
        for it in items:
            if it is None:
                tk.Frame(body, height=1, bg=T["line"]).pack(fill="x", padx=10, pady=4)
                continue
            label, command, danger = it
            row = tk.Frame(body, bg=T["card"], height=self.ROW_H, cursor="hand2")
            row.pack(fill="x", padx=4, pady=1)
            row.pack_propagate(False)
            fg = T["danger"] if danger else T["text"]
            lbl = tk.Label(row, text=label, anchor="w", bg=T["card"], fg=fg,
                           font=self.font, padx=self.PAD_X - 4)
            lbl.pack(fill="both", expand=True)
            rows.append((row, lbl, fg))

            def enter(_e, r=row, l=lbl):
                r.configure(bg=T["card_h"])
                l.configure(bg=T["card_h"])

            def leave(_e, r=row, l=lbl):
                r.configure(bg=T["card"])
                l.configure(bg=T["card"])

            def click(_e, cmd=command):
                self.close()
                if cmd is not None:
                    root.after_idle(cmd)

            for w in (row, lbl):
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)
                w.bind("<Button-1>", click)
        win.update_idletasks()
        w = max(self.min_width, win.winfo_reqwidth())
        h = win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        px = max(0, min(int(x), sw - w - 4))
        py = max(0, min(int(y), sh - h - 4))
        win.geometry(f"{w}x{h}+{px}+{py}")
        win.deiconify()
        win.lift()
        self._win = win
        win.bind("<Escape>", lambda _e: self.close())
        win.bind("<FocusOut>", lambda _e: win.after(60, self._maybe_close))
        try:
            win.focus_force()
        except tk.TclError:
            pass
        # клик мимо меню (в любом месте окна приложения) закрывает его
        self._click_id = root.bind("<Button-1>", self._outside, add="+")
        self._root = root

    def _outside(self, event):
        win = self._win
        if win is None:
            return
        try:
            if str(event.widget).startswith(str(win)):
                return
        except Exception as _exc:
            _LOG.debug("PopupMenu: %r", _exc)
        self.close()

    def _maybe_close(self):
        win = self._win
        if win is None:
            return
        try:
            if win.focus_displayof() is None:
                self.close()
        except Exception:
            self.close()

    def close(self):
        win, self._win = self._win, None
        if self._click_id is not None:
            try:
                self._root.unbind("<Button-1>", self._click_id)
            except Exception as _exc:
                _LOG.debug("PopupMenu: %r", _exc)
            self._click_id = None
        if win is not None:
            try:
                win.destroy()
            except Exception as _exc:
                _LOG.debug("PopupMenu: %r", _exc)

    def grab_release(self):          # совместимость с tk.Menu
        pass
