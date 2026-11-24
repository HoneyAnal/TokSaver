import hashlib
import http.server
import socket
import socketserver
import struct
import threading
import zipfile

import pytest

import netutil


# ---------- вспомогательные серверы ----------

class _Handler(http.server.BaseHTTPRequestHandler):
    BODY = bytes(range(256)) * 4096          # 1 МБ
    requests = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).requests.append(dict(self.headers))
        body = self.BODY
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            if start >= len(body):
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Length", str(len(body) - start))
            self.end_headers()
            self.wfile.write(body[start:])
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def http_server():
    _Handler.requests = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


class _Socks5(socketserver.BaseRequestHandler):
    seen = []

    def handle(self):
        s = self.request
        ver, n = s.recv(2)
        s.recv(n)
        s.sendall(b"\x05\x00")
        hdr = s.recv(4)
        assert hdr[:2] == b"\x05\x01" and hdr[3] == 3        # CONNECT по ДОМЕННОМУ имени
        ln = s.recv(1)[0]
        host = s.recv(ln).decode()
        port = struct.unpack(">H", s.recv(2))[0]
        type(self).seen.append((host, port))
        target = socket.create_connection(("127.0.0.1", port))   # «настоящий» хост
        s.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        def pump(a, b):
            try:
                while True:
                    d = a.recv(65536)
                    if not d:
                        break
                    b.sendall(d)
            except OSError:
                pass
            finally:
                try:
                    b.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        t = threading.Thread(target=pump, args=(target, s), daemon=True)
        t.start()
        pump(s, target)
        t.join(2)


@pytest.fixture
def socks_server():
    _Socks5.seen = []
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Socks5)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


# ---------- SHA-256 ----------

def test_parse_sums_and_sha(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    assert netutil.sha256_file(str(f)) == digest
    sums = netutil.parse_sums(f"{digest}  a.bin\n{'0'*64} *b.bin\nмусор\nzz  c.bin\n")
    assert sums == {"a.bin": digest, "b.bin": "0" * 64}


# ---------- загрузка ----------

def test_download_direct_and_sha_ok(tmp_path, http_server):
    dest = tmp_path / "f.bin"
    want = hashlib.sha256(_Handler.BODY).hexdigest()
    netutil.download_file(f"http://127.0.0.1:{http_server}/x", str(dest), expected_sha256=want)
    assert dest.read_bytes() == _Handler.BODY
    assert not (tmp_path / "f.bin.part").exists()


def test_download_sha_mismatch_leaves_nothing(tmp_path, http_server):
    dest = tmp_path / "f.bin"
    with pytest.raises(netutil.IntegrityError):
        netutil.download_file(f"http://127.0.0.1:{http_server}/x", str(dest),
                              expected_sha256="0" * 64)
    assert not dest.exists()
    assert not (tmp_path / "f.bin.part").exists()


def test_download_resumes_part(tmp_path, http_server):
    dest = tmp_path / "f.bin"
    part = tmp_path / "f.bin.part"
    part.write_bytes(_Handler.BODY[:300000])
    netutil.download_file(f"http://127.0.0.1:{http_server}/x", str(dest))
    assert dest.read_bytes() == _Handler.BODY
    assert _Handler.requests[-1].get("Range") == "bytes=300000-"


def test_download_part_larger_than_file_restarts(tmp_path, http_server):
    dest = tmp_path / "f.bin"
    (tmp_path / "f.bin.part").write_bytes(b"x" * (len(_Handler.BODY) + 5))
    netutil.download_file(f"http://127.0.0.1:{http_server}/x", str(dest))
    assert dest.read_bytes() == _Handler.BODY


# ---------- SOCKS5 ----------

def test_http_get_through_socks_uses_domain_name(http_server, socks_server):
    data = netutil.http_get_prefix(f"http://tiktok.example:{http_server}/x", 100,
                                   proxy=("127.0.0.1", socks_server))
    assert data == _Handler.BODY[:101]
    # прокси получил ИМЯ хоста, а не локально разрезолвленный IP (иначе DNS-блокировка не обходится)
    assert _Socks5.seen == [("tiktok.example", http_server)]


def test_socks_does_not_patch_global_socket(http_server, socks_server):
    original = socket.socket
    netutil.http_get_prefix(f"http://tiktok.example:{http_server}/x", 10,
                            proxy=("127.0.0.1", socks_server))
    assert socket.socket is original


def test_socks_refused():
    with socket.socket() as s:                    # свободный порт, на котором никто не слушает
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(OSError):
        netutil.socks5_connect(("127.0.0.1", port), "example.com", 80, timeout=2)


# ---------- zip ----------

def _zip(path, names):
    with zipfile.ZipFile(path, "w") as z:
        for n, data in names.items():
            z.writestr(n, data)


def test_safe_extract_only_target(tmp_path):
    z = tmp_path / "a.zip"
    _zip(z, {"pkg/ciadpi.exe": b"MZ-data", "pkg/readme.txt": b"hi"})
    dest = tmp_path / "out" / "ciadpi.exe"
    dest.parent.mkdir()
    assert netutil.safe_extract_member(str(z), "ciadpi.exe", str(dest)) is True
    assert dest.read_bytes() == b"MZ-data"
    assert [p.name for p in dest.parent.iterdir()] == ["ciadpi.exe"]    # лишнего не распаковано


@pytest.mark.parametrize("bad", ["../evil.exe", "/abs/evil.exe", "a/../../evil.exe", "C:/evil.exe"])
def test_safe_extract_rejects_traversal(tmp_path, bad):
    z = tmp_path / "a.zip"
    _zip(z, {"ciadpi.exe": b"ok", bad: b"evil"})
    with pytest.raises(netutil.UnsafeArchive):
        netutil.safe_extract_member(str(z), "ciadpi.exe", str(tmp_path / "ciadpi.exe"))
    assert not (tmp_path / "evil.exe").exists()


def test_safe_extract_missing_member(tmp_path):
    z = tmp_path / "a.zip"
    _zip(z, {"other.txt": b"x"})
    assert netutil.safe_extract_member(str(z), "ciadpi.exe", str(tmp_path / "c.exe")) is False


def test_free_port_skips_busy():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        got = netutil.free_port(busy)
        assert got is not None and got != busy


# ---------- HTTPS через SOCKS (основной боевой сценарий: tiktok.com) ----------

def test_https_through_socks(tmp_path, socks_server, monkeypatch):
    import shutil
    import ssl
    import subprocess
    if not shutil.which("openssl"):
        pytest.skip("нет openssl для генерации тестового сертификата")
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(crt), "-subj", "/CN=tiktok.example",
                    "-addext", "subjectAltName=DNS:tiktok.example"],
                   check=True, capture_output=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(str(crt), str(key))
    srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        real = ssl.create_default_context

        def trusting(*a, **k):
            ctx = real(*a, **k)
            ctx.load_verify_locations(str(crt))
            return ctx
        monkeypatch.setattr(netutil.ssl, "create_default_context", trusting)
        port = srv.server_address[1]
        data = netutil.http_get_prefix(f"https://tiktok.example:{port}/x", 50,
                                       proxy=("127.0.0.1", socks_server))
        assert data == _Handler.BODY[:51]
        assert _Socks5.seen == [("tiktok.example", port)]
    finally:
        srv.shutdown()
