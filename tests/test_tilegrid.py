import pytest

from tilegrid import GAP, TILE_H, TILE_W, Grid


def test_columns_and_height():
    g = Grid(width=700, count=25)
    assert g.cols == (700 - GAP) // (TILE_W + GAP) == 4
    assert g.rows == 7 and g.height == 7 * (TILE_H + GAP) + GAP
    assert Grid(width=50, count=3).cols == 1                    # узкое окно — одна колонка
    e = Grid(width=700, count=0)
    assert e.rows == 0 and e.height == 0 and list(e.visible(0, 500)) == []


def test_rects_do_not_overlap_and_fit():
    g = Grid(width=700, count=9)
    rects = [g.rect(i) for i in range(9)]
    for i, a in enumerate(rects):
        assert a[0] >= 0 and a[2] <= 700 and a[3] - a[1] == TILE_H
        for b in rects[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def test_visible_is_small_even_for_huge_lists():
    g = Grid(width=700, count=100_000)
    vis = g.visible(top=50 * (TILE_H + GAP), view_h=600)
    assert 0 < len(vis) <= g.cols * 6                            # ~2-3 экрана строк, а не 100 000
    assert vis.start % g.cols == 0
    assert g.rect(vis[0])[3] >= 50 * (TILE_H + GAP) - 2 * (TILE_H + GAP)
    assert len(g.visible(0, 600)) <= g.cols * 5


def test_visible_covers_viewport():
    g = Grid(width=700, count=200)
    for top in (0, 333, 1234, 5000):
        vis = set(g.visible(top, 700, buffer_rows=0))
        for i in range(200):
            _x0, y0, _x1, y1 = g.rect(i)
            if y1 > top and y0 < top + 700:
                assert i in vis                                  # всё видимое действительно нарисуется


def test_index_at():
    g = Grid(width=700, count=10)
    for i in range(10):
        x0, y0, x1, y1 = g.rect(i)
        assert g.index_at(x0 + 1, y0 + 1) == i and g.index_at(x1 - 1, y1 - 1) == i
    x0, y0, x1, y1 = g.rect(0)
    assert g.index_at(x1 + 2, y0 + 5) is None                    # зазор между плитками
    assert g.index_at(0, 0) is None
    assert g.index_at(g.rect(9)[0] + g.tile_w + g.gap + 5, g.rect(9)[1] + 5) is None
    last_row_y = g.rect(9)[1] + 5
    assert g.index_at(g.rect(0)[0], last_row_y) is not None or g.cols >= 10
    g2 = Grid(width=700, count=6)                                # пустые клетки последней строки
    assert g2.index_at(g2.rect(5)[0] + g2.tile_w + g2.gap + 3, g2.rect(5)[1] + 3) is None


def test_move_clamps():
    g = Grid(width=700, count=10)                                # 4 колонки: 4+4+2
    assert g.move(None, 1, 0) == 0
    assert g.move(0, -1, 0) == 0 and g.move(0, 0, -1) == 0
    assert g.move(3, 1, 0) == 3
    assert g.move(1, 0, 1) == 5 and g.move(9, 0, 1) == 9
    assert g.move(7, 0, 1) == 9                                   # внизу короткая строка → последняя плитка
    assert Grid(width=700, count=0).move(None, 1, 0) is None


@pytest.mark.parametrize("top,expect_changed", [(0, True), (10_000, True)])
def test_scroll_to_show(top, expect_changed):
    g = Grid(width=700, count=100)
    new = g.scroll_to_show(50, top, 600)
    _x0, y0, _x1, y1 = g.rect(50)
    assert new <= y0 and new + 600 >= y1
    assert g.scroll_to_show(0, 0, 600) == 0                       # уже видна — не двигаем
