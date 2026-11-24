import os
import time

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

import thumbs  # noqa: E402


def _img(path, size=(1080, 1920), color=(200, 30, 30), fmt=None):
    Image.new("RGB", size, color).save(path, fmt)


def test_find_source_prefers_image_over_video(tmp_path):
    (tmp_path / "20240101_111.mp4").write_bytes(b"v")
    (tmp_path / "20240101_111.webp").write_bytes(b"i")
    (tmp_path / "20240101_222.mp4").write_bytes(b"v")
    (tmp_path / "20240101_333.cover.jpg").write_bytes(b"c")          # кэш обложки аккаунта — не источник
    assert thumbs.find_source(str(tmp_path), "111")[0] == "image"
    assert thumbs.find_source(str(tmp_path), "222")[0] == "video"
    assert thumbs.find_source(str(tmp_path), "333") is None
    assert thumbs.find_source(str(tmp_path), "999") is None


@pytest.mark.parametrize("ext,fmt", [(".jpg", "JPEG"), (".png", "PNG"), (".webp", "WEBP")])
def test_build_thumb_formats_and_size(tmp_path, ext, fmt):
    src = tmp_path / f"s{ext}"
    _img(str(src), fmt=fmt)
    dest = tmp_path / "cache" / "1.jpg"
    assert thumbs.build_thumb(str(src), str(dest))
    with Image.open(dest) as im:
        assert im.format == "JPEG"
        assert im.width <= 240 and im.height <= 360
        assert abs(im.width / im.height - 1080 / 1920) < 0.02        # пропорции сохранены
    assert dest.stat().st_size < 30_000                                  # маленький файл


def test_build_thumb_alpha_png_and_garbage(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGBA", (300, 300), (255, 0, 0, 0)).save(src)
    assert thumbs.build_thumb(str(src), str(tmp_path / "o.jpg"))
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    assert thumbs.build_thumb(str(bad), str(tmp_path / "b.jpg")) is False
    assert not (tmp_path / "b.jpg").exists() and not (tmp_path / "b.jpg.part").exists()


def test_is_fresh(tmp_path):
    src, cache = tmp_path / "s.jpg", tmp_path / "c.jpg"
    src.write_bytes(b"s")
    cache.write_bytes(b"c")
    os.utime(src, (1000, 1000))
    os.utime(cache, (2000, 2000))
    assert thumbs.is_fresh(str(cache), str(src))
    os.utime(src, (3000, 3000))
    assert not thumbs.is_fresh(str(cache), str(src))                     # источник новее → пересоздать


def test_worker_priority_cache_and_results(tmp_path):
    media, cache = tmp_path / "media", tmp_path / "thumbs"
    media.mkdir()
    for i in range(6):
        _img(str(media / f"20240101_10000000{i:02d}.jpg"), size=(400, 700))
    w = thumbs.ThumbWorker(str(media), str(cache), pause=0)
    for i in range(6):
        w.request(f"10000000{i:02d}")                                    # прогрев
    w.request("1000000005", priority=thumbs.PRIORITY_NOW)                # выбранный — вне очереди
    w.start()
    got = {}
    deadline = time.time() + 10
    while len(got) < 6 and time.time() < deadline:
        try:
            vid, path = w.results.get(timeout=0.5)
            got[vid] = path
        except Exception:
            pass
    w.stop()
    assert len(got) == 6 and all(p and os.path.isfile(p) for p in got.values())
    # второй запрос — из кэша, без перекодирования
    mtime = os.path.getmtime(got["1000000001"])
    assert thumbs.ThumbWorker(str(media), str(cache), pause=0).make("1000000001") == got["1000000001"]
    assert os.path.getmtime(got["1000000001"]) == mtime


def test_worker_no_source_gives_none_and_dedup(tmp_path):
    w = thumbs.ThumbWorker(str(tmp_path), str(tmp_path / "c"), pause=0)
    w.request("777")
    w.request("777")                                                      # дубль прогрева игнорируется
    assert w._q.qsize() == 1
    assert w.make("777") is None


def test_prune(tmp_path):
    for n in ("1.jpg", "2.jpg", "3.jpg", "x.txt"):
        (tmp_path / n).write_bytes(b"x")
    assert thumbs.prune(str(tmp_path), {"1", "3"}) == 1
    assert sorted(os.listdir(tmp_path)) == ["1.jpg", "3.jpg", "x.txt"]


def test_extract_frame_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbs.shutil, "which", lambda n: None)
    assert thumbs.extract_frame(str(tmp_path / "v.mp4"), str(tmp_path / "o.jpg")) is False
