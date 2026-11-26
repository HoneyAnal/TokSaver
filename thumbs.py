"""Кэш превью постов — без лагов интерфейса (без tkinter, проверяется тестами).

Как устроено (чтобы не повторить прежние тормоза):
  • превью делает ОДИН фоновый поток, интерфейс ничего не декодирует сам;
  • в кэш пишутся маленькие JPEG (до 240×360) — загрузка превью выбранного поста
    в окно занимается миллисекундами;
  • источник: картинка поста от yt-dlp (webp/jpg/png) → иначе кадр из видео
    через ffmpeg (если он установлен) → иначе превью нет;
  • выбранный пост обрабатывается вне очереди (приоритет), остальные — «прогревом»
    с небольшими паузами, чтобы не отнимать процессор у окна;
  • кэш лежит в папке аккаунта (thumbs/), хранится между запусками, устаревшие
    превью (источник новее) пересоздаются, превью удалённых постов — чистятся.
"""
import itertools
import os
import postmedia
import queue
import shutil
import subprocess
import threading
import time

import applog
import paths

_LOG = applog.get(__name__)

THUMB_SIZE = (240, 360)
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")
MAX_SRC_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 80_000_000
PRIORITY_NOW, PRIORITY_VISIBLE, PRIORITY_WARM = 0, 1, 10


def cache_path(cache_dir, video_id):
    return os.path.join(cache_dir, f"{video_id}.jpg")


def find_source(media_dir, video_id, names=None):
    """Источник превью поста в media/: ("image"|"video", путь) или None.
    names — заранее прочитанный os.listdir (чтобы не читать папку на каждый пост)."""
    if names is None:
        names = postmedia.list_names(media_dir)
    image = video = None
    for name in names:
        if video_id not in name:
            continue
        low = name.lower()
        if low.endswith(".cover.jpg"):
            continue
        path = os.path.join(media_dir, name)
        if low.endswith(IMG_EXTS) and image is None:
            image = path
        elif low.endswith(VIDEO_EXTS) and video is None:
            video = path
    if image:
        return "image", image
    if video:
        return "video", video
    return None


def is_fresh(cache, src):
    try:
        return os.path.getsize(cache) > 0 and os.path.getmtime(cache) >= os.path.getmtime(src)
    except OSError:
        return False


def build_thumb(src, dest, size=THUMB_SIZE):
    """Уменьшенная копия картинки src → JPEG dest (атомарно). True при успехе."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    try:
        if os.path.getsize(src) > MAX_SRC_BYTES:
            return False
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.draft("RGB", (size[0] * 2, size[1] * 2))      # JPEG: быстрое частичное декодирование
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, (0, 0, 0))
                im = im.convert("RGBA")
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            im.thumbnail(size, Image.BILINEAR)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".part"
            im.save(tmp, "JPEG", quality=82)
        os.replace(tmp, dest)
        return True
    except Exception as e:                                     # битая картинка — не повод падать
        _LOG.debug('preview %s not created: %r', src, e)
        return False


def extract_frame(video, dest, ffmpeg=None, seek="1"):
    """Кадр из видео через ffmpeg → dest (JPEG). False, если ffmpeg нет или сбой."""
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".frame.jpg"
    try:
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-ss", seek, "-i", video, "-frames:v", "1",
             "-vf", f"scale='min({THUMB_SIZE[0]},iw)':-2", tmp],
            capture_output=True, timeout=25, creationflags=paths.CREATE_NO_WINDOW)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            if seek != "0":
                return extract_frame(video, dest, ffmpeg, seek="0")   # ролик короче 1 с
            return False
        os.replace(tmp, dest)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        _LOG.debug('frame from %s not obtained: %r', video, e)
        return False
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def prune(cache_dir, valid_ids):
    """Удаляет превью постов, которых больше нет в базе. Возвращает число удалённых."""
    n = 0
    try:
        for name in os.listdir(cache_dir):
            stem, ext = os.path.splitext(name)
            if ext == ".jpg" and stem not in valid_ids:
                os.remove(os.path.join(cache_dir, name))
                n += 1
    except OSError:
        pass
    return n


class ThumbWorker(threading.Thread):
    """Фоновый поток превью. request() ставит пост в очередь; готовые результаты
    (video_id, путь|None) забирает интерфейс из .results (queue.Queue)."""

    def __init__(self, media_dir, cache_dir, pause=0.01):
        super().__init__(name="thumbs", daemon=True)
        self.media_dir = media_dir
        self.cache_dir = cache_dir
        self.pause = pause
        self.results = queue.Queue()
        self._q = queue.PriorityQueue()
        self._seq = itertools.count()
        self._stop_evt = threading.Event()
        self._queued = set()
        self._lock = threading.Lock()
        self._names = (0.0, [])

    # --- управление ---
    def request(self, video_id, priority=PRIORITY_WARM):
        with self._lock:
            if priority >= PRIORITY_WARM and video_id in self._queued:
                return                       # дубль прогрева; срочные запросы не отбрасываются
            self._queued.add(video_id)
        self._q.put((priority, next(self._seq), video_id))

    def stop(self):
        self._stop_evt.set()
        self._q.put((-1, next(self._seq), None))

    # --- работа ---
    def _listing(self):
        stamp, names = self._names
        if time.monotonic() - stamp > 5:
            names = postmedia.list_names(self.media_dir)
            self._names = (time.monotonic(), names)
        return names

    def make(self, video_id):
        """Гарантирует превью поста; путь к кэшу или None."""
        dest = cache_path(self.cache_dir, video_id)
        src = find_source(self.media_dir, video_id, self._listing())
        if src is None:
            return dest if os.path.isfile(dest) else None       # источника нет, но кэш мог остаться
        kind, path = src
        if is_fresh(dest, path):
            return dest
        ok = build_thumb(path, dest) if kind == "image" else extract_frame(path, dest)
        if not ok and kind == "video":
            return None
        return dest if ok else (dest if os.path.isfile(dest) else None)

    def run(self):
        while not self._stop_evt.is_set():
            try:
                _prio, _n, vid = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if vid is None or self._stop_evt.is_set():
                break
            with self._lock:
                self._queued.discard(vid)
            try:
                path = self.make(vid)
            except Exception as e:
                _LOG.debug('preview %s: %r', vid, e)
                path = None
            self.results.put((vid, path))
            if self.pause:
                time.sleep(self.pause)               # не отнимаем у окна процессор
