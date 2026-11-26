import os
import postmedia as pm


def test_scan_and_counts(tmp_path):
    for n in ["20250101_7111111111111111111_1.jpg", "20250101_7111111111111111111_2.jpg",
              "20250101_7111111111111111111_3.jpg", "20250101_7111111111111111111.m4a",
              "7222222222222222222.mp4", "7222222222222222222.cover.jpg"]:
        (tmp_path / n).write_bytes(b"x")
    info = pm.scan(str(tmp_path), {"7111111111111111111", "7222222222222222222"})
    photo = {"video_id": "7111111111111111111", "kind": "photo", "downloaded": 1}
    video = {"video_id": "7222222222222222222", "kind": "video", "downloaded": 1}
    assert pm.picture_count(photo, info) == 3 and pm.is_photo(photo, info)
    assert not pm.is_photo(video, info)
    assert len(pm.photo_files(str(tmp_path), "7111111111111111111")) == 3


def test_unknown_kind_with_many_images_is_photo(tmp_path):
    for i in range(2):
        (tmp_path / f"7333333333333333333_{i}.webp").write_bytes(b"x")
    info = pm.scan(str(tmp_path), {"7333333333333333333"})
    assert pm.is_photo({"video_id": "7333333333333333333", "kind": None}, info)


def test_url():
    assert pm.tiktok_url("bob", "1", True) == "https://www.tiktok.com/@bob/photo/1"
    assert pm.tiktok_url("bob", "1") == "https://www.tiktok.com/@bob/video/1"


def test_is_valid_image(tmp_path):
    import postmedia
    ok = tmp_path / "7_01.jpg"
    ok.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 200 + b"\xff\xd9")
    empty = tmp_path / "7_02.jpg"
    empty.write_bytes(b"")
    html = tmp_path / "7_03.jpg"
    html.write_bytes(b"<html>403 forbidden</html>" + b" " * 100)
    assert postmedia.is_valid_image(str(ok))
    assert not postmedia.is_valid_image(str(empty)) and not postmedia.is_valid_image(str(html))
    assert [os.path.basename(f) for f in postmedia.bad_photo_files(str(tmp_path), "7")] == ["7_02.jpg", "7_03.jpg"]


def test_fix_extensions(tmp_path):
    import postmedia
    webp = tmp_path / "9_01 title.jpg"
    webp.write_bytes(b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"x" * 100)
    jpg = tmp_path / "9_02 title.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 100)
    ren = postmedia.fix_extensions(str(tmp_path), "9")
    assert list(ren.values()) == [str(tmp_path / "9_01 title.webp")]
    assert (tmp_path / "9_02 title.jpg").exists() and postmedia.is_valid_image(str(tmp_path / "9_01 title.webp"))


def test_truncated_jpeg_is_invalid(tmp_path):
    from postmedia import is_valid_image
    good = tmp_path / "a.jpg"
    good.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 200 + b"\xff\xd9")
    bad = tmp_path / "b.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 200)
    assert is_valid_image(str(good))
    assert not is_valid_image(str(bad))


def test_relocate_and_list_names(tmp_path):
    import postmedia
    m = tmp_path
    (m / "20230101_7777777777777777777.mp4").write_bytes(b"x")
    (m / "Photos").mkdir()
    (m / "Photos" / "7777777777777777777_01.jpg").write_bytes(b"y")
    names = postmedia.list_names(str(m))
    assert "Photos/7777777777777777777_01.jpg" in names
    moved = postmedia.relocate(str(m), "7777777777777777777", "Video")
    assert len(moved) == 2
    assert (m / "Video" / "20230101_7777777777777777777.mp4").exists()
    assert postmedia.scan(str(m), ["7777777777777777777"])["7777777777777777777"] == {"images": 1, "videos": 1}


def test_move_meta_and_info_ignored(tmp_path):
    import postmedia
    m = tmp_path
    (m / "Photos").mkdir()
    (m / "Photos" / "8888888888888888888_01.jpg").write_bytes(b"y")
    (m / "Photos" / "8888888888888888888_01.jpg.json").write_text("{}")
    postmedia.move_meta(str(m / "Photos"), "8888888888888888888", str(m / "Info"))
    assert (m / "Info" / "8888888888888888888_01.jpg.json").exists()
    (m / "Info" / "20230101_8888888888888888888.webp").write_bytes(b"z")
    assert postmedia.scan(str(m), ["8888888888888888888"])["8888888888888888888"]["images"] == 1
    assert len(postmedia.photo_files(str(m), "8888888888888888888")) == 1


def test_expected_count(tmp_path):
    import postmedia
    (tmp_path / "9999999999999999999_01.jpg.json").write_text('{"count": 5, "num": 1}')
    assert postmedia.expected_count("9999999999999999999", str(tmp_path)) == 5
    assert postmedia.expected_count("1111111111111111111", str(tmp_path)) == 0
