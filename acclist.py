"""Список аккаунтов, нарисованный на ОДНОМ холсте (tk.Canvas).

Раньше каждая карточка была набором из ~50 дочерних окон (CTkButton/CTkLabel —
это холст+метка, то есть несколько HWND на Windows). Прокрутка двигала сотни
дочерних окон, Windows не успевала их перерисовать: карточки «рассыпались»,
накладывались, а список местами оставался пустым. Теперь карточка — это
обычные элементы холста (прямоугольники, текст, картинки): холст прокручивает
их одной операцией, а рисуем мы только карточки, попавшие в видимую область.
Дочерних окон нет вообще.
"""
import tkinter as tk
import tkinter.font as tkfont
import math

import applog
from ui_widgets import FlatScroll, FlatScrollbar

_LOG = applog.get(__name__)

# Размеры в «логических» пикселях (умножаются на k — масштаб экрана).
GAP = 10
SIDE = 2
CARD_H = 184
PAD = 12
COVER_W, COVER_H = 232, 148
BTN_H = 34
BTN_Y = 108
BAR_Y = 156
MARGIN = 360


def rrect(cv, x0, y0, x1, y1, r, **kw):
    """Скруглённый прямоугольник (многоугольник со сглаживанием)."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
           x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
           x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class AccountList(FlatScroll):
    """Прокручиваемый список карточек аккаунтов.

    app — объект с методами: ``_card_images(acc_id) -> (cover, avatar, badge)``,
    ``_on_card_button(key, acc_id)``, ``_on_card_open(acc_id)``,
    ``_on_card_menu(acc_id, x_root, y_root)``; словарь карточек ``cards``
    (acc_id → dict с полями nick, no_id, paused, status, status_color,
    stats, prog) принадлежит приложению.
    """

    def __init__(self, master, app, T, font_family, bg, thumb, thumb_h, gutter=0, **kw):
        tk.Frame.__init__(self, master, bg=bg, highlightthickness=0, bd=0, **kw)
        self.app = app
        self.T = T
        self.cards = app._cards
        self.order = []
        self.busy = False
        self.empty_text = ""
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=1)
        self.vsb = FlatScrollbar(self, thumb=thumb, thumb_h=thumb_h,
                                 command=self._sb_cmd, bg=bg)
        self.vsb.pack(side="right", fill="y", padx=(gutter, 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.body = self.canvas
        try:
            self.k = max(1.0, float(self.winfo_fpixels("1i")) / 96.0)
        except tk.TclError:
            self.k = 1.0
        k = self.k
        px = lambda n: -max(1, int(round(n * k)))     # отрицательный размер = пиксели
        fam = font_family
        self.f_name = tkfont.Font(family=fam, size=px(17), weight="bold")
        self.f_chip = tkfont.Font(family=fam, size=px(12))
        self.f_chip_b = tkfont.Font(family=fam, size=px(12), weight="bold")
        self.f_stats = tkfont.Font(family=fam, size=px(13))
        self.f_btn = tkfont.Font(family=fam, size=px(13))
        self.f_btn_b = tkfont.Font(family=fam, size=px(13), weight="bold")
        self.f_small = tkfont.Font(family=fam, size=px(11))
        self.f_empty = tkfont.Font(family=fam, size=px(14))
        self._width = 0
        self._drawn = set()
        self._hit = {}              # acc_id → [(x0, y0, x1, y1, key)]
        self._hover = None          # (acc_id, key | "card")
        self._press = None
        self._job = None
        self._vis_job = None
        self._debt = 0.0
        self._anim = None
        c = self.canvas
        c.bind("<Configure>", self._on_configure, add="+")
        c.bind("<Motion>", self._on_motion, add="+")
        c.bind("<Leave>", self._on_leave, add="+")
        c.bind("<ButtonPress-1>", self._on_press, add="+")
        c.bind("<ButtonRelease-1>", self._on_release, add="+")
        c.bind("<Button-3>", self._on_menu, add="+")
        c.bind("<Button-2>", self._on_menu, add="+")

    # ---------------------------------------------------------------- масштаб
    def s(self, n):
        return int(round(n * self.k))

    # ---------------------------------------------------------------- модель
    def set_order(self, ids):
        self.order = list(ids)
        self._reset()

    def refresh(self, acc_id):
        """Перерисовать карточку, если она сейчас на экране."""
        if acc_id in self._drawn:
            self._draw_card(acc_id)

    def refresh_all(self):
        for a in list(self._drawn):
            self._draw_card(a)

    def set_busy(self, busy):
        if busy != self.busy:
            self.busy = busy
            self.refresh_all()

    def set_empty(self, text):
        self.empty_text = text or ""
        self._reset()

    def is_shown(self, acc_id):
        return acc_id in self._drawn

    def clear(self):
        self.order = []
        self._reset()

    def card_top(self, index):
        return self.s(SIDE) + index * (self.s(CARD_H) + self.s(GAP))

    def _reset(self):
        try:
            self.canvas.delete("all")
        except tk.TclError:
            return
        self._drawn.clear()
        self._hit.clear()
        self._hover = None
        self._schedule()

    # ---------------------------------------------------------------- прокрутка
    def wheel_scroll(self, event):
        """Колесо копит «долг» прокрутки, кадры (~60/с) отрабатывают его порциями."""
        num = getattr(event, "num", None)
        delta = getattr(event, "delta", 0) or 0
        if num == 4 or delta > 0:
            sign = -1
        elif num == 5 or delta < 0:
            sign = 1
        else:
            return
        notches = min(3.0, abs(delta) / 120.0) if delta else 1.0
        self._debt = max(-1500.0, min(1500.0,
                         self._debt + sign * 96 * max(0.25, notches) * self.k))
        if self._anim is None:
            self._anim = self.after(0, self._scroll_step)

    def _scroll_step(self):
        self._anim = None
        debt = self._debt
        if abs(debt) < 1:
            self._debt = 0.0
            return
        mag = abs(debt)
        # шаг не больше остатка: раньше минимум 4 px перелетал малый остаток, и список дрожал туда-сюда
        px = max(1, int(min(mag, max(4.0, min(mag * 0.3, 70 * self.k)))))
        px = px if debt > 0 else -px
        try:
            self.canvas.yview_scroll(px, "units")
            self._debt = debt - px
            self._anim = self.after(14, self._scroll_step)
        except tk.TclError:
            self._debt = 0.0

    def _on_yscroll(self, a, b):
        self.vsb.set(a, b)
        if self._vis_job is None:
            try:
                self._vis_job = self.after_idle(self._sync_visible)
            except tk.TclError:
                self._vis_job = None

    # ---------------------------------------------------------------- раскладка
    def _on_configure(self, event):
        if event.width != self._width:
            self._width = event.width
            self._reset()
        else:
            self._schedule()

    def _schedule(self):
        if self._job is None:
            try:
                self._job = self.after_idle(self._layout)
            except tk.TclError:
                self._job = None

    def _layout(self):
        self._job = None
        try:
            w = self.canvas.winfo_width()
            view_h = self.canvas.winfo_height()
        except tk.TclError:
            return
        if w < 50:
            return
        self._width = w
        n = len(self.order)
        total = self.card_top(n) - self.s(GAP) + self.s(SIDE) if n else 0
        self.canvas.configure(scrollregion=(0, 0, w, max(total, view_h)))
        self._sync_visible()

    def _sync_visible(self):
        self._vis_job = None
        c = self.canvas
        try:
            top = c.canvasy(0)
            view_h = c.winfo_height()
        except tk.TclError:
            return
        if self._width < 50:
            return
        if not self.order:
            if not c.find_withtag("empty"):
                self._draw_empty(view_h)
            return
        c.delete("empty")
        step = self.s(CARD_H) + self.s(GAP)
        m = self.s(MARGIN)
        first = max(0, int((top - m - self.s(SIDE)) // step))
        last = min(len(self.order) - 1, int((top + view_h + m) // step))
        want = set(self.order[first:last + 1]) if first <= last else set()
        for a in list(self._drawn - want):
            c.delete(f"c{a}")
            self._drawn.discard(a)
            self._hit.pop(a, None)
        for a in self.order[first:last + 1]:
            if a not in self._drawn:
                self._draw_card(a)

    def _draw_empty(self, view_h):
        c = self.canvas
        c.delete("empty")
        c.create_text(self._width / 2, max(view_h, 200) / 2 - self.s(10),
                      text=self.empty_text, fill=self.T["muted"], font=self.f_empty,
                      justify="center", tags="empty")

    # ---------------------------------------------------------------- рисование
    def _fit(self, text, font, limit):
        if font.measure(text) <= limit:
            return text
        while len(text) > 1 and font.measure(text + "…") > limit:
            text = text[:-1]
        return text + "…"

    def _buttons(self, card):
        """Описание кнопок: (key, текст, стиль, min_w)."""
        from i18n import tr
        paused = card.get("paused")
        return [("sync", tr('Sync'), "primary", 150),
                ("folder", tr('Folder'), "plain", 84),
                ("pause", tr('Resume') if paused else tr('Pause'), "plain", 78),
                ("delete", tr('Delete'), "danger", 90)]

    def _draw_card(self, acc_id):
        from i18n import tr
        card = self.cards.get(acc_id)
        if card is None or acc_id not in self.order:
            return
        c, T, s = self.canvas, self.T, self.s
        tag = f"c{acc_id}"
        c.delete(tag)
        idx = self.order.index(acc_id)
        y0 = self.card_top(idx)
        x0, x1 = s(SIDE), self._width - s(SIDE)
        h = s(CARD_H)
        self._drawn.add(acc_id)
        rrect(c, x0, y0, x1, y0 + h, s(12), fill=T["card"], outline=T["line"],
              width=1, tags=(tag, "card"))
        hits = []

        # --- обложка / аватар ---
        cw, ch = s(COVER_W), s(COVER_H)
        cx0, cy0 = x0 + s(PAD), y0 + (h - ch) // 2
        cover, avatar, badge = self.app._card_images(acc_id)
        if cover is not None:
            c.create_rectangle(cx0, cy0, cx0 + cw, cy0 + ch, fill=T["thumb_bg"],
                               outline=T["line"], tags=tag)
            c.create_image(cx0 + cw // 2, cy0 + ch // 2, image=cover, tags=tag)
            if badge is not None:
                c.create_image(cx0 + int(cw * 0.05), cy0 + int(ch * 0.95), image=badge,
                               anchor="sw", tags=tag)
        elif avatar is not None:
            c.create_image(cx0 + cw // 2, cy0 + ch // 2, image=avatar, tags=tag)
        else:
            self._silhouette(c, cx0 + cw // 2, cy0 + ch // 2, s(120), tag)

        tx = cx0 + cw + s(16)
        right = x1 - s(16)

        # --- имя, чипы, статус ---
        cy = y0 + s(26)
        status = card.get("status") or ""
        st_w = self.f_chip_b.measure(status) if status else 0
        if status:
            c.create_text(right, cy, text=status, anchor="e", font=self.f_chip_b,
                          fill=card.get("status_color") or T["accent"], tags=tag)
        limit = right - tx - (st_w + s(12) if status else 0)
        nick = "@" + card.get("nick", "")
        chips = []
        if card.get("no_id"):
            chips.append((tr('  ·  no ID'), self.f_chip, T["muted"]))
        if card.get("paused"):
            chips.append((tr('  ·  PAUSED'), self.f_chip_b, T["warn"]))
        chips_w = sum(f.measure(t) for t, f, _c in chips)
        nick = self._fit(nick, self.f_name, max(s(60), limit - chips_w))
        c.create_text(tx, cy, text=nick, anchor="w", font=self.f_name,
                      fill=T["text"], tags=tag)
        xx = tx + self.f_name.measure(nick)
        for t, f, col in chips:
            c.create_text(xx, cy, text=t, anchor="w", font=f, fill=col, tags=tag)
            xx += f.measure(t)

        # --- статистика ---
        ty = y0 + s(48)
        text = card.get("stats") or ""
        it = c.create_text(tx, ty, text=text, anchor="nw", font=self.f_stats,
                           fill=T["muted"], width=max(s(120), right - tx), tags=tag)
        lim = y0 + s(BTN_Y) - s(4)
        for _i in range(8):
            bb = c.bbox(it)
            if not bb or bb[3] <= lim or len(text) < 12:
                break
            text = text[:max(8, len(text) - max(12, len(text) // 6))].rstrip() + "…"
            c.itemconfigure(it, text=text)

        # --- кнопки ---
        bx = tx
        by = y0 + s(BTN_Y)
        for key, label, style, min_w in self._buttons(card):
            f = self.f_btn_b if style == "primary" else self.f_btn
            wide = max(f.measure(t) for t in (label, ) + (
                (self._buttons({"paused": not card.get("paused")})[2][1],) if key == "pause" else ()))
            bw = max(s(min_w), wide + s(32))
            enabled = (not self.busy) or key == "pause"
            hov = enabled and self._hover == (acc_id, key)
            if style == "primary":
                fill = T["accent_h"] if hov else T["accent"]
                outline, fg = fill, T["on_accent"]
            else:
                fill = T["card_h"] if hov else T["field"]
                outline = T["line"]
                fg = T["danger"] if style == "danger" else T["text"]
            if not enabled:
                fill = T["field"]
                outline = T["line"]
                fg = T["muted"]
            rrect(c, bx, by, bx + bw, by + s(BTN_H), s(RADIUS_B), fill=fill,
                  outline=outline, width=1, tags=tag)
            c.create_text(bx + bw / 2, by + s(BTN_H) / 2, text=label, font=f, fill=fg,
                          tags=tag)
            if enabled:
                hits.append((bx, by, bx + bw, by + s(BTN_H), key))
            bx += bw + s(6)

        # --- прогресс + «Стоп» ---
        prog = card.get("prog")
        if prog is not None:
            i, total = prog
            sy = y0 + s(BAR_Y) + s(10)
            stop_w = max(s(64), self.f_btn.measure(tr('Stop')) + s(28))
            sx1 = right
            sx0 = sx1 - stop_w
            rrect(c, sx0, sy - s(14), sx1, sy + s(14), s(RADIUS_B),
                  fill=T["card_h"] if self._hover == (acc_id, "stop") else T["field"],
                  outline=T["line"], width=1, tags=tag)
            c.create_text((sx0 + sx1) / 2, sy, text=tr('Stop'), font=self.f_btn,
                          fill=T["danger"], tags=tag)
            hits.append((sx0, sy - s(14), sx1, sy + s(14), "stop"))
            lbl = f"{i}/{total}" if total else tr('preparing the list…')
            lw = s(96)
            c.create_text(sx0 - s(10), sy, text=lbl, anchor="e", font=self.f_small,
                          fill=T["muted"], tags=tag)
            b0, b1 = tx, sx0 - s(10) - lw - s(10)
            if b1 > b0 + s(20):
                c.create_line(b0, sy, b1, sy, width=s(6), capstyle="round",
                              fill=T["line"], tags=tag)
                frac = max(0.02, min(1.0, i / total)) if total else 0.05
                c.create_line(b0, sy, b0 + (b1 - b0) * frac, sy, width=s(6),
                              capstyle="round", fill=T["accent"], tags=tag)
        self._hit[acc_id] = hits

    def _silhouette(self, c, cx, cy, size, tag):
        """Кружок-заглушка с силуэтом (пока нет аватара и обложки)."""
        T = self.T
        r = size / 2 - 2
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T["card"], outline=T["line"],
                      width=2, tags=tag)
        hr = size * 0.17
        top = cy - size / 2 + size * 0.145
        c.create_oval(cx - hr, top, cx + hr, top + 2 * hr, fill=T["muted"], outline="",
                      tags=tag)
        rx, ry = size * 0.30, size * 0.26
        dy = math.sqrt(max(r * r - rx * rx, 0.0))
        yb = cy + dy
        pts = []
        for i in range(25):
            th = math.pi + math.pi * i / 24
            pts += [cx + rx * math.cos(th), yb - ry + ry * (1 + math.sin(th))]
        a0 = math.atan2(dy, rx)
        for i in range(13):
            th = a0 + (math.pi - 2 * a0) * i / 12
            pts += [cx + r * math.cos(th), cy + r * math.sin(th)]
        c.create_polygon(pts, fill=T["muted"], outline="", smooth=True, tags=tag)

    # ---------------------------------------------------------------- мышь
    def _locate(self, event):
        """(acc_id, key|'card') под курсором или None."""
        if not self.order:
            return None
        c = self.canvas
        x, y = c.canvasx(event.x), c.canvasy(event.y)
        step = self.s(CARD_H) + self.s(GAP)
        idx = int((y - self.s(SIDE)) // step)
        if idx < 0 or idx >= len(self.order):
            return None
        top = self.card_top(idx)
        if y > top + self.s(CARD_H):
            return None                      # зазор между карточками
        acc_id = self.order[idx]
        for x0, y0, x1, y1, key in self._hit.get(acc_id, ()):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return (acc_id, key)
        return (acc_id, "card")

    def _on_motion(self, event):
        hit = self._locate(event)
        if hit != self._hover:
            old = self._hover
            self._hover = hit
            for h in (old, hit):
                if h:
                    self.refresh(h[0])
            try:
                self.canvas.configure(cursor="hand2" if hit else "")
            except tk.TclError:
                pass

    def _on_leave(self, _e):
        if self._hover:
            old = self._hover
            self._hover = None
            self.refresh(old[0])
        try:
            self.canvas.configure(cursor="")
        except tk.TclError:
            pass

    def _on_press(self, event):
        self._press = self._locate(event)

    def _on_release(self, event):
        hit, pressed = self._locate(event), self._press
        self._press = None
        if not hit or hit != pressed:
            return
        acc_id, key = hit
        try:
            if key == "card":
                self.app._on_card_open(acc_id)
            else:
                self.app._on_card_button(key, acc_id)
        except Exception as exc:
            _LOG.debug('card click failed: %r', exc, exc_info=True)

    def _on_menu(self, event):
        hit = self._locate(event)
        if hit:
            self.app._on_card_menu(hit[0], event.x_root, event.y_root)


RADIUS_B = 10
