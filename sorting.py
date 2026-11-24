"""Сортировки списков (без tkinter — проверяются тестами).

Посты и аккаунты сортируются здесь, а интерфейс только показывает порядок.
"""
from i18n import tr

POST_SORTS = [
    ("new", tr('Newest first')),
    ("old", tr('Oldest first')),
    ("title", tr('By title A→Z')),
    ("status", tr('Queued first')),
    ("added", tr('In order added')),
]
ACCOUNT_SORTS = [
    ("added", tr('By date added')),
    ("name", tr('By name A→Z')),
    ("sync", tr('By last check')),
    ("done", tr('Most downloaded')),
    ("queue", tr('Largest queue')),
]


def label_for(options, key):
    for k, text in options:
        if k == key:
            return text
    return options[0][1]


def key_for(options, label):
    for k, text in options:
        if text == label:
            return k
    return options[0][0]


def _id_num(video_id):
    """ID поста TikTok — число, растущее со временем: годится как запасной ключ даты."""
    try:
        return int(video_id)
    except (TypeError, ValueError):
        return 0


def sort_posts(posts, key):
    """Новый список постов в нужном порядке. Не меняет исходный."""
    posts = list(posts)
    if key == "added":
        return posts                                   # порядок базы (rowid)
    if key in ("new", "old"):
        dated = [p for p in posts if p.get("upload_date")]
        undated = [p for p in posts if not p.get("upload_date")]
        rev = key == "new"
        dated.sort(key=lambda p: (p["upload_date"], _id_num(p["video_id"])), reverse=rev)
        undated.sort(key=lambda p: _id_num(p["video_id"]), reverse=rev)   # без даты — по ID
        return dated + undated                         # пост без даты всегда в конце
    if key == "title":
        def tkey(p):
            t = (p.get("title") or "").strip().casefold()
            return (t == "", t, _id_num(p["video_id"]))      # без названия — в конец
        return sorted(posts, key=tkey)
    if key == "status":
        rank = {0: 0, 2: 1, 1: 2}                      # очередь, пропуск, скачано
        return sorted(posts, key=lambda p: (rank.get(p.get("downloaded"), 3),
                                            _neg(p.get("upload_date")), -_id_num(p["video_id"])))
    return posts


def _neg(date):
    """Ключ «дата по убыванию» для строк YYYYMMDD; пустая дата — в конец."""
    if not date:
        return (1, 0)
    return (0, -int(date)) if str(date).isdigit() else (1, 0)


def sort_accounts(accounts, key, counts_fn=None):
    """Новый список аккаунтов. counts_fn(acc_id) → (всего, скачано, в очереди, пропущено)."""
    accounts = list(accounts)
    if key == "name":
        return sorted(accounts, key=lambda a: ((a.get("nickname") or "").casefold(), a["id"]))
    if key == "sync":
        have = [a for a in accounts if a.get("last_sync")]
        never = [a for a in accounts if not a.get("last_sync")]
        have.sort(key=lambda a: (a["last_sync"], a["id"]), reverse=True)
        return have + never
    if key in ("done", "queue") and counts_fn:
        idx = 1 if key == "done" else 2
        cache = {a["id"]: counts_fn(a["id"])[idx] for a in accounts}
        return sorted(accounts, key=lambda a: (-cache[a["id"]], a["id"]))
    return sorted(accounts, key=lambda a: a["id"])
