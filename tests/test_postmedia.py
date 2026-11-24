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
