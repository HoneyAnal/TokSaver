"""Сведения о медиа поста: фото-карусель или видео, число картинок, ссылка на TikTok."""
import os
import re

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")
_ID_RE = re.compile(r"\d{15,20}")


def _is_picture(name):
    low = name.lower()
    return low.endswith(IMG_EXTS) and not low.endswith(".cover.jpg")


def scan(media_dir, ids):
    """Одним проходом по папке: {video_id: {"images": n, "videos": n}} для известных ID."""
    out = {}
    try:
        names = os.listdir(media_dir)
    except OSError:
        return out
    for name in names:
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
    return post.get("kind") == "photo" or picture_count(post, info) >= 2


def photo_files(media_dir, video_id):
    """Картинки поста по порядку имён."""
    try:
        names = sorted(os.listdir(media_dir))
    except OSError:
        return []
    return [os.path.join(media_dir, n) for n in names if video_id in n and _is_picture(n)]


def tiktok_url(nickname, video_id, photo=False):
    """Ссылка на пост: фото-карусели живут по /photo/, видео — по /video/."""
    return "https://www.tiktok.com/@{}/{}/{}".format(
        nickname, "photo" if photo else "video", video_id)
