"""Сетевые и файловые утилиты TokSaver (без tkinter, без сторонних библиотек).

  • SOCKS5-клиент на сокетах — запросы через ByeDPI БЕЗ подмены socket.socket
    (прежняя подмена затрагивала весь процесс, включая соседние потоки);
  • http_get / download_file — с прокси или напрямую, докачка .part (Range);
  • проверка SHA-256 и разбор файла SHA2-256SUMS;
  • безопасная распаковка zip (защита от путей вида ../ и абсолютных путей).
"""
from i18n import tr
import hashlib
import http.client
import os
import os.path
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

CHUNK = 256 * 1024


class IntegrityError(Exception):
    """Скачанный файл не совпал с контрольной суммой."""


class UnsafeArchive(Exception):
    """В архиве путь, выходящий за пределы папки назначения."""


class SocksError(OSError):
    """Ошибка рукопожатия SOCKS5."""


# ------------------------------ SOCKS5 ------------------------------

_SOCKS_REPLIES = {
    1: tr('general SOCKS server failure'), 2: tr('connection not allowed by ruleset'),
    3: tr('network unreachable'), 4: tr('host unreachable'), 5: tr('connection refused'),
    6: tr('TTL expired'), 7: tr('command not supported'), 8: tr('address type not supported'),
}


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        part = sock.recv(n - len(buf))
        if not part:
            raise SocksError(tr('SOCKS server closed the connection'))
        buf += part
    return buf


def socks5_connect(proxy, host, port, timeout=30):
    """Открывает TCP-соединение с host:port через SOCKS5-прокси (host, port).
    Имя хоста передаётся прокси как есть (remote DNS), без локального резолва.
    Возвращает подключённый сокет."""
    sock = socket.create_connection(proxy, timeout=timeout)
    try:
        sock.sendall(b"\x05\x01\x00")                      # версия 5, 1 метод: без авторизации
        ver, method = _recv_exact(sock, 2)
        if ver != 5 or method != 0:
            raise SocksError(tr('SOCKS5: the proxy did not accept the no-authentication method'))
        name = host.encode("idna")
        if len(name) > 255:
            raise SocksError(tr('host name too long'))
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port))
        ver, rep, _rsv, atyp = _recv_exact(sock, 4)
        if rep != 0:
            raise SocksError("SOCKS5: " + _SOCKS_REPLIES.get(rep, tr('code {0}', rep)))
        if atyp == 1:
            _recv_exact(sock, 4 + 2)
        elif atyp == 4:
            _recv_exact(sock, 16 + 2)
        elif atyp == 3:
            ln = _recv_exact(sock, 1)[0]
            _recv_exact(sock, ln + 2)
        else:
            raise SocksError(tr('SOCKS5: unknown address type in the reply'))
        return sock
    except BaseException:
        sock.close()
        raise


class _SocksHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, proxy=None, **kw):
        super().__init__(*args, **kw)
        self._proxy = proxy

    def connect(self):
        self.sock = socks5_connect(self._proxy, self.host, self.port, self.timeout)


class _SocksHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, proxy=None, **kw):
        super().__init__(*args, **kw)
        self._proxy = proxy

    def connect(self):
        raw = socks5_connect(self._proxy, self.host, self.port, self.timeout)
        ctx = self._context if hasattr(self, "_context") else ssl.create_default_context()
        self.sock = ctx.wrap_socket(raw, server_hostname=self.host)


class _SocksHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, proxy):
        super().__init__()
        self._proxy = proxy

    def http_open(self, req):
        def factory(host, **kw):
            return _SocksHTTPConnection(host, proxy=self._proxy, **kw)
        return self.do_open(factory, req)


class _SocksHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, proxy):
        super().__init__(context=ssl.create_default_context())
        self._proxy = proxy

    def https_open(self, req):
        ctx = self._context

        def factory(host, **kw):
            conn = _SocksHTTPSConnection(host, proxy=self._proxy, context=ctx, **kw)
            return conn
        return self.do_open(factory, req)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Редиректы без утечек: при переходе на ДРУГОЙ хост заголовки Cookie и
    Authorization отбрасываются (по умолчанию urllib пересылает их как есть, и
    cookies TikTok уехали бы на чужой сервер). Переход https→http запрещён."""
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if old.scheme == "https" and new.scheme != "https":
            raise urllib.error.HTTPError(req.full_url, code,
                                         tr('redirect from https to an insecure protocol is forbidden'),
                                         headers, fp)
        nreq = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nreq is not None and (old.hostname or "").lower() != (new.hostname or "").lower():
            for hdr in ("Cookie", "Authorization", "Proxy-Authorization"):
                nreq.headers.pop(hdr, None)
                nreq.headers.pop(hdr.lower(), None)
                nreq.unredirected_hdrs.pop(hdr, None)
                nreq.unredirected_hdrs.pop(hdr.lower(), None)
        return nreq


def build_opener(proxy=None):
    """urllib-opener. proxy = (host, port) SOCKS5 или None (напрямую).
    Системные HTTP-прокси игнорируются: ходим либо через ByeDPI, либо напрямую."""
    handlers = [urllib.request.ProxyHandler({}), _SafeRedirectHandler()]
    if proxy:
        handlers += [_SocksHTTPHandler(proxy), _SocksHTTPSHandler(proxy)]
    return urllib.request.build_opener(*handlers)


# ------------------------------ HTTP ------------------------------

DEFAULT_LIMIT = 5 * 1024 * 1024        # страницы и JSON TikTok — не больше 5 МБ
DEFAULT_DEADLINE = 120                   # общий потолок времени одного запроса, секунд


class ResponseTooLarge(OSError):
    """Ответ длиннее допустимого."""


def http_get(url, headers=None, timeout=30, proxy=None, limit=DEFAULT_LIMIT,
             deadline=DEFAULT_DEADLINE):
    """GET → bytes. limit — потолок размера (по умолчанию 5 МБ): при превышении
    бросается ResponseTooLarge, а не съедается вся память. deadline — общий
    потолок времени (timeout действует лишь на каждую операцию чтения, и
    медленный сервер мог бы тянуть ответ бесконечно). limit=None — без потолка.
    Исключения сети пробрасываются."""
    req = urllib.request.Request(url, headers=headers or {})
    started = time.monotonic()
    buf = bytearray()
    with build_opener(proxy).open(req, timeout=timeout) as r:
        while True:
            chunk = r.read(CHUNK)
            if not chunk:
                break
            buf += chunk
            if limit is not None and len(buf) > limit:
                raise ResponseTooLarge(tr('response is larger than {0} bytes', limit))
            if deadline and time.monotonic() - started > deadline:
                raise TimeoutError(tr('request takes longer than {0} s', deadline))
    return bytes(buf)


def http_get_prefix(url, nbytes, headers=None, timeout=30, proxy=None):
    """Первые nbytes байт ответа (для картинок с потолком размера): читает
    nbytes+1, чтобы вызывающий мог понять, что ответ обрезан. Остальное не качается."""
    req = urllib.request.Request(url, headers=headers or {})
    with build_opener(proxy).open(req, timeout=timeout) as r:
        return r.read(nbytes + 1)


def download_file(url, dest, headers=None, timeout=30, proxy=None,
                  progress=None, expected_sha256=None, max_size=None):
    """Скачивает url в dest через dest+'.part' с ДОКАЧКОЙ: если .part остался
    от прерванной загрузки, просит у сервера только недостающий хвост (Range).
    Если задан expected_sha256 — сверяет и при несовпадении удаляет файл и
    бросает IntegrityError (ничего не попадает в dest). progress(done, total)."""
    part = dest + ".part"
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    start = os.path.getsize(part) if os.path.exists(part) else 0
    hdrs = dict(headers or {})
    if start:
        hdrs["Range"] = f"bytes={start}-"
    req = urllib.request.Request(url, headers=hdrs)
    opener = build_opener(proxy)
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 416 and start:             # .part уже полный или чужой — начинаем заново
            os.remove(part)
            return download_file(url, dest, headers, timeout, proxy, progress,
                                 expected_sha256, max_size)
        raise
    with resp:
        resumed = start > 0 and resp.status == 206
        mode = "ab" if resumed else "wb"
        done = start if resumed else 0
        total = done + int(resp.headers.get("Content-Length") or 0)
        if max_size and total > max_size:
            raise ResponseTooLarge(tr('file is larger than the allowed {0} bytes', max_size))
        with open(part, mode) as f:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if max_size and done > max_size:
                    raise ResponseTooLarge(tr('file is larger than the allowed {0} bytes', max_size))
                if progress:
                    progress(done, total)
    if total and done != total:
        raise OSError(tr('download interrupted: received {0} of {1} bytes (the .part file is kept for resuming)', done, total))
    if expected_sha256:
        actual = sha256_file(part)
        if actual.lower() != expected_sha256.lower():
            os.remove(part)
            raise IntegrityError(
                tr('checksum mismatch: expected {0}, got {1}', expected_sha256, actual))
    os.replace(part, dest)


# ------------------------------ контрольные суммы ------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(text):
    """Разбор SHA2-256SUMS: строки «<64 hex>␠␠<имя файла>» → {имя: hex}."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            try:
                int(parts[0], 16)
            except ValueError:
                continue
            out[parts[1].lstrip("*").strip()] = parts[0].lower()
    return out


# ------------------------------ zip ------------------------------

def safe_extract_member(zip_path, member_suffix, dest_file):
    """Извлекает из zip ОДИН файл, чьё имя оканчивается на member_suffix
    (без учёта регистра), прямо в dest_file. Остальное содержимое архива не
    трогается, поэтому пути внутри архива не могут никуда «убежать».
    Возвращает True, если файл найден."""
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():               # сначала проверяем ВЕСЬ архив
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or ":" in name.split("/")[0]:
                raise UnsafeArchive(tr('suspicious path in archive: {0}', info.filename))
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if name.lower().endswith(member_suffix.lower()):
                tmp = dest_file + ".part"
                with z.open(info) as src, open(tmp, "wb") as out:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                os.replace(tmp, dest_file)
                return True
    return False


def is_within(base, path):
    """Лежит ли path внутри папки base (после разрешения .. и ссылок)."""
    try:
        b = os.path.normcase(os.path.realpath(base))
        p = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([b, p]) == b
    except ValueError:                       # разные диски Windows
        return False


def free_port(preferred, host="127.0.0.1", tries=20):
    """Свободный порт: preferred, а если занят — ближайший следующий."""
    for port in range(preferred, min(preferred + tries, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return None
