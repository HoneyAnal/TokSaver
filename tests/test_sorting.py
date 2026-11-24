import sorting


def P(vid, date, title="", dl=0):
    return {"video_id": vid, "upload_date": date, "title": title, "downloaded": dl}


POSTS = [P("1000000000003", "20240301", "Beta"), P("1000000000001", "20240101", "alpha"),
         P("1000000000004", None, ""), P("1000000000002", "20240201", "Гамма", 1),
         P("1000000000005", None, "zeta", 2)]


def ids(lst):
    return [p["video_id"][-1] for p in lst]


def test_new_old_undated_last():
    assert ids(sorting.sort_posts(POSTS, "new")) == ["3", "2", "1", "5", "4"]
    assert ids(sorting.sort_posts(POSTS, "old")) == ["1", "2", "3", "4", "5"]


def test_title_casefold_empty_last():
    assert ids(sorting.sort_posts(POSTS, "title")) == ["1", "3", "5", "2", "4"]


def test_status_queue_first_then_skipped_then_done():
    out = sorting.sort_posts(POSTS, "status")
    assert [p["downloaded"] for p in out] == [0, 0, 0, 2, 1]


def test_added_keeps_order_and_input_untouched():
    before = list(POSTS)
    assert sorting.sort_posts(POSTS, "added") == before
    sorting.sort_posts(POSTS, "new")
    assert POSTS == before


def test_unknown_key_is_noop():
    assert sorting.sort_posts(POSTS, "???") == POSTS


def test_accounts_sorts():
    accs = [{"id": 1, "nickname": "bob", "last_sync": "2024-01-02"},
            {"id": 2, "nickname": "Alice", "last_sync": None},
            {"id": 3, "nickname": "carol", "last_sync": "2024-03-01"}]
    counts = {1: (5, 5, 0, 0), 2: (9, 2, 7, 0), 3: (1, 0, 1, 0)}
    fn = counts.__getitem__
    assert [a["id"] for a in sorting.sort_accounts(accs, "added")] == [1, 2, 3]
    assert [a["id"] for a in sorting.sort_accounts(accs, "name")] == [2, 1, 3]
    assert [a["id"] for a in sorting.sort_accounts(accs, "sync")] == [3, 1, 2]     # без проверки — в конец
    assert [a["id"] for a in sorting.sort_accounts(accs, "done", fn)] == [1, 2, 3]
    assert [a["id"] for a in sorting.sort_accounts(accs, "queue", fn)] == [2, 3, 1]


def test_labels_roundtrip():
    for key, label in sorting.POST_SORTS + sorting.ACCOUNT_SORTS:
        opts = sorting.POST_SORTS if (key, label) in sorting.POST_SORTS else sorting.ACCOUNT_SORTS
        assert sorting.key_for(opts, label) == key and sorting.label_for(opts, key) == label
    assert sorting.key_for(sorting.POST_SORTS, "нет такого") == "new"
