"""Карточки аккаунтов: аватары, обложки, статусы, прогресс.

Часть интерфейса TokSaver: миксин для класса App (ui.py). Методы перенесены
из прежнего ui.py без изменений."""
from i18n import tr
import os
import threading
import tkinter as tk

import customtkinter as ctk

import applog
import db
import sorting
from engine import LEVEL_INFO
from engine import fmt_date
from ui_widgets import PopupMenu
from ui_theme import RADIUS
from ui_theme import AVATAR_SIZE
from ui_theme import PREVIEW_W
from ui_theme import PREVIEW_H
from ui_theme import PREVIEW_R

_LOG = applog.get(__name__)


class CardsMixin:
    # ------------------------------ карточки: обложки ------------------------------
    def _avatar_photo_or_none(self, acc_id):
        """CTkImage фото-аватара из кэша или с диска (avatar.jpg); None — нет."""
        img = self._avatar_img.get(acc_id)
        if img is not None:
            return img
        acc = db.db_get_account(acc_id)
        adir = db.account_dir(acc_id) if acc else None
        path = os.path.join(adir, "avatar.jpg") if adir else None
        if not path or not os.path.exists(path):
            return None
        try:
            img = self._avatar_photo_icon(path)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            return None
        self._avatar_img[acc_id] = img
        return img

    def _avatar_small_or_none(self, acc_id, size=44):
        """CTkImage мини-аватара для бейджа на обложке; None — нет фото."""
        img = self._avatar_small.get(acc_id)
        if img is not None:
            return img
        acc = db.db_get_account(acc_id)
        adir = db.account_dir(acc_id) if acc else None
        path = os.path.join(adir, "avatar.jpg") if adir else None
        if not path or not os.path.exists(path):
            return None
        try:
            img = self._avatar_photo_icon(path, size=size)
        except Exception as _exc:
            _LOG.debug('suppressed error: %r', _exc, exc_info=True)
            return None
        self._avatar_small[acc_id] = img
        return img

    def _avatar_photo_icon(self, path, size=AVATAR_SIZE):
        """Фото профиля, вписанное в кружок-рамку: центр-кроп до квадрата,
        круглая маска, обводка в цветах активной темы. 4x суперсэмплинг —
        край кружка ровный, без лесенок. При смене темы кэш аватаров
        сбрасывается — обводка перерисуется в новых цветах."""
        from PIL import Image, ImageDraw, ImageOps
        T = self.T
        from PIL import ImageTk
        size = int(size * self._k())
        ss = 4                          # суперсэмплинг: рисуем крупно, сжимаем
        S = size * ss
        edge = ss                       # запас под обводку, чтобы не резалась
        img = Image.open(path).convert("RGBA")
        img = ImageOps.fit(img, (S, S))       # центр-кроп до квадрата
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).ellipse((edge, edge, S - 1 - edge, S - 1 - edge), fill=255)
        img.putalpha(mask)
        ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))   # обводка поверх фото
        ImageDraw.Draw(ring).ellipse((edge, edge, S - 1 - edge, S - 1 - edge),
                                     outline=T["line"], width=2 * ss)
        img = Image.alpha_composite(img, ring)
        img = img.resize((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img, master=self)

    def _maybe_fetch_avatar(self, acc_id):
        """Попросить движок аватар профиля (одна попытка за раз на карточку)."""
        card = self._cards.get(acc_id)
        if card is None or card.get("avatar_busy") or acc_id in self._avatar_failed:
            return
        if acc_id in self._avatar_img:
            return
        card["avatar_busy"] = True
        threading.Thread(target=self.engine.task_fetch_avatar, args=(acc_id,),
                         daemon=True).start()

    def _rounded_cover(self, path, w=PREVIEW_W, h=PREVIEW_H, r=PREVIEW_R):
        from PIL import ImageTk
        k = self._k()
        w, h, r = int(w * k), int(h * k), int(r * k)
        """Картинка-обложка, обрезанная под размер (углы прямые — r=0)."""
        from PIL import Image, ImageOps
        img = Image.open(path).convert("RGB")
        img = ImageOps.fit(img, (w, h))
        if r > 0:
            from PIL import ImageDraw
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
            img.putalpha(mask)
        return ImageTk.PhotoImage(img, master=self)

    def _maybe_fetch_thumb(self, acc_id):
        """Попросить движок обложку (не чаще одной попытки за раз на карточку)."""
        card = self._cards.get(acc_id)
        if card is None or card.get("thumb_busy") or acc_id in self._thumb_failed:
            return
        if acc_id in self._thumb_img:
            return
        card["thumb_busy"] = True
        threading.Thread(target=self.engine.task_fetch_thumb, args=(acc_id,),
                         daemon=True).start()

    def _k(self):
        """Масштаб экрана (1.0 = 96 dpi): картинки готовим сразу в нужном размере."""
        box = getattr(self, "accounts_box", None)
        return getattr(box, "k", 1.0)

    def _card_images(self, acc_id):
        """(обложка, аватар, мини-аватар) из кэша — без обращения к диску."""
        return (self._thumb_img.get(acc_id), self._avatar_img.get(acc_id),
                self._avatar_small.get(acc_id))

    def _on_avatar(self, acc_id, path):
        """Событие «аватар готов»: кэшируем и перерисовываем карточку."""
        card = self._cards.get(acc_id)
        if card:
            card["avatar_busy"] = False
        if not path or not os.path.exists(path):
            self._avatar_failed.add(acc_id)
            return
        self._avatar_failed.discard(acc_id)
        for cache, size in ((self._avatar_img, AVATAR_SIZE), (self._avatar_small, 44)):
            if acc_id not in cache:
                try:
                    cache[acc_id] = self._avatar_photo_icon(path, size=size)
                except Exception as _exc:
                    _LOG.debug('suppressed error: %r', _exc, exc_info=True)
        self.accounts_box.refresh(acc_id)

    def _on_thumb(self, acc_id, path):
        """Событие «обложка готова»: кэшируем и перерисовываем карточку."""
        card = self._cards.get(acc_id)
        if card:
            card["thumb_busy"] = False
        if not path or not os.path.exists(path):
            self._thumb_failed.add(acc_id)
            return
        self._thumb_failed.discard(acc_id)
        if acc_id not in self._thumb_img:
            try:
                self._thumb_img[acc_id] = self._rounded_cover(path)
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                return
        self.accounts_box.refresh(acc_id)

    # ------------------------------ действия карточек ------------------------------
    def _on_card_open(self, acc_id):
        self.on_show_posts(acc_id)

    def _on_card_button(self, key, acc_id):
        fn = {"sync": self.on_sync_one, "folder": self.on_open_account,
              "pause": self.on_pause, "delete": self.on_delete,
              "stop": self.on_cancel}.get(key)
        if fn:
            fn(acc_id)

    def _on_card_menu(self, acc_id, x, y):
        a = acc_id
        menu = PopupMenu(self.accounts_box.canvas, self.T, self._font_family)
        menu.add_command(label=tr('Sync'), command=lambda: self.on_sync_one(a))
        menu.add_command(label=tr('Open account folder'), command=lambda: self.on_open_account(a))
        menu.add_command(label=tr('Post list'), command=lambda: self.on_show_posts(a))
        menu.add_command(label=tr('Copy link'), command=lambda: self.on_copy_link(a))
        menu.add_command(label=tr('Change range…'), command=lambda: self.on_edit_range(a))
        menu.add_command(label=tr('Note…'), command=lambda: self.on_note(a))
        menu.add_separator()
        menu.add_command(label=tr('Download skipped'), command=lambda: self.on_reset_skipped(a))
        menu.add_command(label=tr('Check files and download'), command=lambda: self.on_verify_files(a))
        menu.add_command(label=tr('Download whole profile'), command=lambda: self.on_backfill(a))
        menu.add_command(label=tr('Pause / Resume'), command=lambda: self.on_pause(a))
        menu.add_separator()
        menu.add_command(label=tr('Delete from database'), command=lambda: self.on_delete(a),
                         danger=True)
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()


    # ------------------------------ занятость ------------------------------
    def _set_busy(self, busy):
        self._busy = busy

        def _do():
            state = "disabled" if busy else "normal"
            for w in self._toggling:
                try:
                    w.configure(state=state)
                except Exception as _exc:
                    _LOG.debug('suppressed error: %r', _exc, exc_info=True)
                    pass
            try:
                self.stop_all_btn.configure(state="normal" if busy else "disabled")
            except Exception as _exc:
                _LOG.debug('suppressed error: %r', _exc)
            self.accounts_box.set_busy(busy)
        self.after(0, _do)

    def _ready(self):
        if self._busy:
            self._append_log(LEVEL_INFO, tr('[System] Please wait: another task is already running.'))
            return False
        if not self.engine.deps_ready:
            self._append_log(LEVEL_INFO, tr('[System] Components are still being checked, one second…'))
            return False
        browser = self.cookies_menu.get()
        self.engine.cookies = "" if browser == tr('none') else browser
        self.engine.cookies_file = self.cookies_file_path
        return True

    # ------------------------------ список аккаунтов ------------------------------
    @staticmethod
    def _card_sig(acc):
        """Что на карточке зависит от записи в базе."""
        return (acc["nickname"], bool(acc["sec_uid"]), bool(acc["paused"]), acc["note"],
                acc["limit_count"], acc["date_from"], acc["date_to"], acc["range_kind"],
                acc["last_sync"])

    def _rebuild_accounts(self, force=False):
        """Приводит карточки к базе: новые создаёт, лишние убирает, у остальных
        обновляет поля. Карточка — просто запись в словаре (рисует её холст),
        поэтому это дёшево и не сбрасывает ни статус, ни прогресс."""
        rows = sorting.sort_accounts(db.db_accounts(),
                                     self.settings.get("accounts_sort", "added"), db.db_counts)
        self.count_lbl.configure(text=f"· {len(rows)}")
        if force:
            self._cards.clear()
        by_id = {a["id"]: a for a in rows}
        for aid in list(self._cards):
            if aid not in by_id:
                del self._cards[aid]
        for acc in rows:
            card = self._cards.get(acc["id"])
            sig = self._card_sig(acc)
            if card is None:
                card = self._cards[acc["id"]] = {"id": acc["id"], "status": "",
                                                 "status_color": None, "prog": None}
                card["sig"] = None
            if card["sig"] != sig:
                card["sig"] = sig
                card["nick"] = acc["nickname"]
                card["no_id"] = not acc["sec_uid"]
                card["paused"] = bool(acc["paused"])
                card["stats"] = self._stat_text(acc)
        order = [a["id"] for a in rows]
        self.accounts_box.set_empty("" if rows else tr(
            'Nothing here yet\n\nAdd an account on the left: a nickname or link,\n'
            'choose a range — and it will appear here'))
        if order != self.accounts_box.order or force:
            self.accounts_box.set_order(order)
        else:
            self.accounts_box.refresh_all()
        for acc in rows:
            aid = acc["id"]
            if aid not in self._avatar_img:
                self._avatar_photo_or_none(aid)
                self._maybe_fetch_avatar(aid)
            if aid not in self._avatar_small:
                self._avatar_small_or_none(aid)
            if aid not in self._thumb_img:
                self._maybe_fetch_thumb(aid)


    def _status_color(self, text):
        """Цвет статуса на карточке по его смыслу (текущая тема окна)."""
        T = self.T
        t = text or ""
        if any(k in t for k in (tr('Error'), tr('failed'))):
            return T["danger"]
        if t == tr('Paused') or any(k in t for k in (tr('login required'), tr('did not respond'))):
            return T["warn"]
        if t in (tr('No posts'),):
            return T["muted"]
        if any(k in t for k in (tr('Downloading'), tr('Building'))):
            return T["accent"]
        return T["info"]

    def _set_card_status(self, card, text):
        card["status"] = text or ""
        card["status_color"] = self._status_color(text)
        self.accounts_box.refresh(card["id"])

    def _set_card_progress(self, card, value):
        """value: None — погасить; (0,0) — «готовлю список»; (i, total) — доля."""
        if card.get("prog") == value:
            return
        card["prog"] = value
        self.accounts_box.refresh(card["id"])


    def _range_text(self, acc):
        """Человекочитаемый диапазон аккаунта (постоянная задача) или None.
        Показывается на карточке, в окне постов и пишется в account.txt."""
        rk = acc.get("range_kind")
        lc = acc.get("limit_count")
        df, dt = acc.get("date_from"), acc.get("date_to")
        if rk == "tail" and lc:
            return tr('range: last {0}', lc)
        if rk == "period" and (df or dt):
            f_ = fmt_date(df) if df else tr('beginning')
            t_ = fmt_date(dt) if dt else tr('today')
            return tr('range: {0} — {1}', f_, t_)
        if not rk and lc and not (df or dt):
            return tr('range: last {0}', lc)
        if not rk and (df or dt):
            f_ = fmt_date(df) if df else tr('beginning')
            t_ = fmt_date(dt) if dt else tr('today')
            return tr('range: {0} — {1}', f_, t_)
        return None

    def _stat_text(self, acc):
        total, done, pending, skipped = db.db_counts(acc["id"])
        known = total - skipped
        parts = [tr('{0}/{1} downloaded', done, known)]
        if pending:
            parts.append(tr('{0} queued', pending))
        if skipped:
            parts.append(tr('{0} outside range', skipped))
        # выбранный диапазон — постоянная задача аккаунта: видно прямо на карточке
        rng = self._range_text(acc)
        if rng:
            parts.append(rng)
        parts.append(tr('synced {0}', acc['last_sync']) if acc["last_sync"]
                     else tr('not synced yet'))
        text = "   ·   ".join(parts)
        if not db.db_folder_exists(acc["id"]):
            text += tr('\nWarning: the account folder was deleted — it will be recreated on sync')
        elif acc["note"]:
            note_ = acc["note"] if len(acc["note"]) <= 46 else acc["note"][:45] + "…"
            text += tr('\nNote: {0}', note_)
        return text

    def _update_card_stats(self, acc_id):
        """Точечное обновление счётчиков карточки (событие stats от движка)."""
        acc = db.db_get_account(acc_id)
        card = self._cards.get(acc_id)
        if not acc or not card:
            return
        card["stats"] = self._stat_text(acc)
        card["paused"] = bool(acc["paused"])
        card["sig"] = self._card_sig(acc)
        self.accounts_box.refresh(acc_id)
