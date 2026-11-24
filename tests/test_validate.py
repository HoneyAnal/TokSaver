import pytest

import validate


@pytest.mark.parametrize("raw,kind,nick", [
    ("alice", "nick", "alice"),
    ("@alice.b_1", "nick", "alice.b_1"),
    ("https://www.tiktok.com/@alice", "profile", "alice"),
    ("tiktok.com/@alice/video/7123456789012345678?lang=en", "post", "alice"),
    ("https://m.tiktok.com/@a/photo/7123456789012345678", "post", "a"),
    ("https://vm.tiktok.com/ZMabc123/", "post", None),
])
def test_valid_targets(raw, kind, nick):
    t = validate.parse_target(raw)
    assert t.kind == kind and t.nickname == nick
    if t.url:
        assert t.url.startswith("https://") and "?" not in t.url     # канонический вид


def test_secuid_target():
    sec = "MS4wLjABAAAA" + "a" * 60
    t = validate.parse_target("tiktokuser:" + sec)
    assert t.kind == "secuid" and t.sec_uid == sec


@pytest.mark.parametrize("raw", [
    "", "   ", "--exec calc tiktok.com", "-evil", "a b", "x" * 30,
    "https://evil.com/tiktok.com/@a", "https://tiktok.com.evil.com/@a/video/7123456789012345678",
    "http://user:pw@tiktok.com/@a", "ftp://tiktok.com/@a", "https://www.tiktok.com/",
    "https://www.tiktok.com/@a/video/abc", "tiktokuser:short", "alice\nbob", "a" * 500,
    "https://vm.tiktok.com/--exec/",
])
def test_rejected_targets(raw):
    with pytest.raises(ValueError):
        validate.parse_target(raw)


def test_safe_arg():
    assert validate.safe_arg("https://www.tiktok.com/@a") == "https://www.tiktok.com/@a"
    for bad in ("", "-o", "--exec", "a\x00b", "a\nb"):
        with pytest.raises(ValueError):
            validate.safe_arg(bad)


def test_is_tiktok_host():
    assert validate.is_tiktok_host("www.tiktok.com") and validate.is_tiktok_host("tiktok.com")
    for bad in ("tiktok.com.evil.com", "eviltiktok.com", "", None, "example.com"):
        assert not validate.is_tiktok_host(bad)
