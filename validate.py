"""Проверка пользовательского ввода (ники, ссылки, ID профиля).

Всё, что пользователь вставил в программу, попадает в командную строку
yt-dlp / gallery-dl и в URL. Поэтому ввод проверяется ЖЁСТКО и приводится к
каноническому виду: строка вроде «--exec calc tiktok.com» не пройдёт (раньше её
пропускала проверка «"tiktok.com" in target», и yt-dlp принял бы её за опции).
"""
from i18n import tr
import re
from collections import namedtuple
from urllib.parse import urlsplit

SEC_UID_RE = re.compile(r"^MS4wLjABAAAA[A-Za-z0-9_-]{56,80}$")
NICK_RE = re.compile(r"^[A-Za-z0-9_.]{1,24}$")
VIDEO_ID_RE = re.compile(r"^\d{10,25}$")

# точные имена хостов (НЕ «содержит tiktok.com»: evil.com/tiktok.com сюда не проходит)
PAGE_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
MAX_LEN = 400

Target = namedtuple("Target", "kind nickname sec_uid url")
# kind: "nick" | "profile" | "post" | "secuid"


def safe_arg(value):
    """Значение для командной строки внешней программы: не пустое, без
    управляющих символов и не начинается с «-» (иначе станет опцией)."""
    v = str(value)
    if not v or v.startswith("-") or any(ord(c) < 32 for c in v):
        raise ValueError(tr('Invalid value for starting the downloader.'))
    return v


def is_tiktok_host(host):
    """Домен TikTok (в том числе поддомены) — для привязки cookies."""
    host = (host or "").lower().rstrip(".")
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def parse_nick(raw):
    nick = (raw or "").strip().lstrip("@")
    if not NICK_RE.match(nick):
        raise ValueError(tr('A TikTok nickname is up to 24 characters: Latin letters, digits, “_” and “.”. Or paste a profile or post link.'))
    return nick


def parse_target(raw):
    """Разбирает ввод пользователя в Target или бросает ValueError (по-русски)."""
    text = (raw or "").strip()
    if not text:
        raise ValueError(tr('Empty: enter a nickname, a profile/post link or tiktokuser:ID.'))
    if len(text) > MAX_LEN or any(ord(c) < 32 for c in text):
        raise ValueError(tr('Invalid characters or input too long.'))

    if text.lower().startswith("tiktokuser:"):
        sec = text.split(":", 1)[1].strip()
        if not SEC_UID_RE.match(sec):
            raise ValueError(tr('Invalid tiktokuser:ID format (expected MS4wLjABAAAA…).'))
        return Target("secuid", None, sec, None)

    if "/" in text or "." in text and not NICK_RE.match(text.lstrip("@")) or "://" in text:
        return _parse_url(text)
    return Target("nick", parse_nick(text), None, None)


def _parse_url(text):
    if "://" not in text:
        text = "https://" + text
    try:
        u = urlsplit(text)
    except ValueError:
        raise ValueError(tr('Could not parse the link.'))
    if u.scheme not in ("http", "https") or u.username or u.password:
        raise ValueError(tr('Expected a regular link https://www.tiktok.com/…'))
    host = (u.hostname or "").lower()
    if host in SHORT_HOSTS:
        code = u.path.strip("/")
        if not re.fullmatch(r"[A-Za-z0-9]{5,20}", code):
            raise ValueError(tr('Invalid short TikTok link.'))
        return Target("post", None, None, f"https://{host}/{code}/")
    if host not in PAGE_HOSTS:
        raise ValueError(tr('The link must point to tiktok.com.'))
    parts = [p for p in u.path.split("/") if p]
    if parts and parts[0].startswith("@"):
        nick = parse_nick(parts[0])
        if len(parts) == 1:
            return Target("profile", nick, None, f"https://www.tiktok.com/@{nick}")
        if len(parts) >= 3 and parts[1] in ("video", "photo") and VIDEO_ID_RE.match(parts[2]):
            return Target("post", nick, None, f"https://www.tiktok.com/@{nick}/{parts[1]}/{parts[2]}")
    raise ValueError(tr('Could not understand the link. Expected a profile link (tiktok.com/@nick) or a post link (tiktok.com/@nick/video/123…).'))
