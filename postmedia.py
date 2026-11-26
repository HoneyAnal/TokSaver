"""Сведения о медиа поста: фото-карусель или видео, число картинок, ссылка на TikTok."""
import os
import re

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic")
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")
_ID_RE = re.compile(r"\d{15,20}")


SUB_VIDEO = "Video"
SUB_PHOTOS = "Photos"
SUB_INFO = "Info"            # технические файлы: описания, json, обложки
SORT_DIRS = (SUB_VIDEO, SUB_PHOTOS, SUB_INFO)
META_EXTS = (".description", ".json")


def list_names(media_dir):
    """Имена файлов папки media/ плюс файлы её подпапок Video/ и Photos/
    (как «Video/имя.mp4»): os.path.join(media_dir, имя) даёт полный путь."""
    try:
        names = os.listdir(media_dir)
    except OSError:
        return []
    out = [n for n in names if n not in SORT_DIRS]
    for sub in SORT_DIRS:
        try:
            out += [sub + "/" + n for n in os.listdir(os.path.join(media_dir, sub))]
        except OSError:
            pass
    return out


def relocate(media_dir, video_id, sub):
    """Переносит файлы поста (кроме недокачанных .part) в media/<sub>/.
    Возвращает {старый путь: новый путь}."""
    moved = {}
    target = os.path.join(media_dir, sub)
    for name in list_names(media_dir):
        if video_id not in name or name.lower().endswith(".part"):
            continue
        if name.split("/")[0] in (sub, SUB_INFO) or name.lower().endswith(META_EXTS):
            continue
        old = os.path.join(media_dir, name)
        new = os.path.join(target, os.path.basename(name))
        if not os.path.isfile(old) or os.path.exists(new):
            continue
        try:
            os.makedirs(target, exist_ok=True)
            os.replace(old, new)
            moved[old] = new
        except OSError:
            pass
    return moved


def move_meta(src_dir, video_id, info_dir):
    """Описания и json поста из src_dir (верхний уровень) — в info_dir."""
    try:
        names = os.listdir(src_dir)
    except OSError:
        return
    for n in names:
        if video_id in n and n.lower().endswith(META_EXTS):
            try:
                os.makedirs(info_dir, exist_ok=True)
                os.replace(os.path.join(src_dir, n), os.path.join(info_dir, n))
            except OSError:
                pass


def expected_count(video_id, *dirs):
    """Сколько картинок в посте по данным TikTok (поле «count» из json gallery-dl);
    0 — неизвестно. Ищет json поста в переданных папках."""
    import json
    best = 0
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if video_id in n and n.lower().endswith(".json"):
                try:
                    with open(os.path.join(d, n), encoding="utf-8", errors="ignore") as f:
                        data = json.load(f)
                    c = int(data.get("count") or 0) if isinstance(data, dict) else 0
                except (OSError, ValueError, TypeError):
                    continue
                best = max(best, c)
    return best


def _is_picture(name):
    low = name.lower()
    return low.endswith(IMG_EXTS) and not low.endswith(".cover.jpg")


def scan(media_dir, ids):
    """Одним проходом по папке: {video_id: {"images": n, "videos": n}} для известных ID."""
    out = {}
    names = list_names(media_dir)
    for name in names:
        if name.startswith(SUB_INFO + "/"):
            continue
        pic = _is_picture(name)
        vid = name.lower().endswith(VIDEO_EXTS)
        if not (pic or vid):
            continue
        for m in set(_ID_RE.findall(name)):
            if m in ids:
                d = out.setdefault(m, {"images": 0, "videos": 0})
                d["images" if pic else "videos"] += 1
    return out


def picture_count(post, info):
    """Сколько картинок у поста (0 — неизвестно или это не фото-пост)."""
    d = (info or {}).get(post["video_id"]) or {}
    imgs, vids = d.get("images", 0), d.get("videos", 0)
    if post.get("kind") == "photo":
        return imgs
    return imgs if imgs >= 2 and not vids else 0


def is_photo(post, info):
    d = (info or {}).get(post["video_id"]) or {}
    imgs, vids = d.get("images", 0), d.get("videos", 0)
    # на диске лежит видео и нет картинок-галереи — это видео, что бы ни было в базе
    if vids and imgs < 2:
        return False
    return post.get("kind") == "photo" or picture_count(post, info) >= 2


def photo_files(media_dir, video_id):
    """Картинки поста по порядку имён."""
    names = sorted(list_names(media_dir))
    return [os.path.join(media_dir, n) for n in names if video_id in n and _is_picture(n)
            and not n.startswith(SUB_INFO + "/")]


_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")


def sniff_ext(path):
    """Настоящий формат по «шапке» файла: .jpg/.png/.gif/.webp/.avif/.heic или None."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return ".avif"
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
            return ".heic"
    return None


def fix_extensions(media_dir, video_id):
    """Приводит расширения картинок поста к настоящему формату (TikTok нередко отдаёт
    WebP/AVIF под именем .jpg — просмотрщики такие файлы не открывают).
    Возвращает {старый путь: новый путь} для переименованных."""
    renamed = {}
    for f in photo_files(media_dir, video_id):
        real = sniff_ext(f)
        base, ext = os.path.splitext(f)
        if real and ext.lower() not in (real, ".jpeg" if real == ".jpg" else real):
            new = base + real
            if not os.path.exists(new):
                try:
                    os.replace(f, new)
                    renamed[f] = new
                except OSError:
                    pass
    return renamed


def is_valid_image(path):
    """Файл похож на настоящую картинку: не пустой и с узнаваемой «шапкой»
    (JPEG/PNG/GIF/WEBP). Заглушка вместо картинки или обрыв дают False."""
    try:
        if os.path.getsize(path) < 64:
            return False
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    real = sniff_ext(path)
    if real is None:
        return False
    # оборванный файл: у JPEG/PNG нет «хвоста» (маркер конца) — просмотрщик пишет «Can't read file header»
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 64))
            tail = f.read()
    except OSError:
        return False
    if real == ".jpg" and b"\xff\xd9" not in tail:
        return False
    if real == ".png" and b"IEND" not in tail:
        return False
    return True


def bad_photo_files(media_dir, video_id):
    """Картинки поста, которые не открываются (их надо скачать заново)."""
    return [f for f in photo_files(media_dir, video_id) if not is_valid_image(f)]


def tiktok_url(nickname, video_id, photo=False):
    """Ссылка на пост: фото-карусели живут по /photo/, видео — по /video/."""
    return "https://www.tiktok.com/@{}/{}/{}".format(
        nickname, "photo" if photo else "video", video_id)
