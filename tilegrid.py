"""Раскладка плиток для режима «Плитки» (чистая математика, без tkinter).

Плитки рисуются на холсте ВИРТУАЛЬНО: на экране создаются только видимые строки
(плюс запас в одну строку сверху и снизу), остальное существует лишь как число
в расчёте высоты. Поэтому тысяча постов не тормозит интерфейс так, как это было
при «живых» виджетах-плитках.
"""
TILE_W = 148
TILE_H = 262
GAP = 10
IMG_W = 136
IMG_H = 200


class Grid:
    def __init__(self, width, count, tile_w=TILE_W, tile_h=TILE_H, gap=GAP):
        self.tile_w, self.tile_h, self.gap = tile_w, tile_h, gap
        self.count = max(0, count)
        self.width = max(1, width)
        self.cols = max(1, (self.width - gap) // (tile_w + gap))
        used = self.cols * (tile_w + gap) - gap
        self.left = max(gap, (self.width - used) // 2)          # центрируем ряд
        self.rows = -(-self.count // self.cols) if self.count else 0
        self.height = self.rows * (tile_h + gap) + gap if self.rows else 0

    def rect(self, i):
        """(x0, y0, x1, y1) плитки с номером i."""
        row, col = divmod(i, self.cols)
        x0 = self.left + col * (self.tile_w + self.gap)
        y0 = self.gap + row * (self.tile_h + self.gap)
        return x0, y0, x0 + self.tile_w, y0 + self.tile_h

    def visible(self, top, view_h, buffer_rows=1):
        """Номера плиток, которые нужно держать на экране при прокрутке top."""
        if not self.count:
            return range(0)
        step = self.tile_h + self.gap
        first = max(0, int((top - self.gap) // step) - buffer_rows)
        last = min(self.rows - 1, int((top + view_h) // step) + buffer_rows)
        if last < first:
            return range(0)
        return range(first * self.cols, min(self.count, (last + 1) * self.cols))

    def index_at(self, x, y):
        """Номер плитки под точкой (x, y) холста или None (зазор / за краем)."""
        if x < self.left or y < self.gap:
            return None
        step_x, step_y = self.tile_w + self.gap, self.tile_h + self.gap
        col, rx = divmod(int(x - self.left), step_x)
        row, ry = divmod(int(y - self.gap), step_y)
        if col >= self.cols or rx >= self.tile_w or ry >= self.tile_h:
            return None
        i = row * self.cols + col
        return i if 0 <= i < self.count else None

    def move(self, i, dcol, drow):
        """Соседняя плитка при навигации стрелками (границы не пересекаются)."""
        if not self.count:
            return None
        if i is None:
            return 0
        row, col = divmod(i, self.cols)
        col = min(max(0, col + dcol), self.cols - 1)
        row = min(max(0, row + drow), self.rows - 1)
        return min(row * self.cols + col, self.count - 1)

    def scroll_to_show(self, i, top, view_h):
        """Новая прокрутка top, при которой плитка i целиком видна (или прежняя)."""
        _x0, y0, _x1, y1 = self.rect(i)
        if y0 - self.gap < top:
            return max(0, y0 - self.gap)
        if y1 + self.gap > top + view_h:
            return max(0, y1 + self.gap - view_h)
        return top
