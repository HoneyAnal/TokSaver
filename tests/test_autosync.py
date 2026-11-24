import datetime as dt

import autosync as sc

NOW = dt.datetime(2026, 11, 23, 14, 0, 0)


def ts(d):
    return d.timestamp()


def test_parse_time():
    assert sc.parse_time("03:30") == (3, 30)
    for bad in ("24:00", "3", "ab", "", None, "12:60"):
        assert sc.parse_time(bad) is None


def test_hours_mode():
    last = ts(NOW - dt.timedelta(hours=2))
    assert not sc.is_due("hours", 3, "", last, NOW)
    assert sc.is_due("hours", 1, "", last, NOW)
    assert sc.next_run("hours", 3, "", last, NOW) == NOW + dt.timedelta(hours=1)


def test_daily_mode_due_and_catch_up():
    # сегодня 03:00 уже прошло, последний запуск вчера → пора
    assert sc.is_due("daily", 24, "03:00", ts(NOW - dt.timedelta(days=1)), NOW)
    # запускались сегодня в 03:05 → не пора, следующий завтра в 03:00
    last = ts(NOW.replace(hour=3, minute=5))
    assert not sc.is_due("daily", 24, "03:00", last, NOW)
    assert sc.next_run("daily", 24, "03:00", last, NOW) == dt.datetime(2026, 11, 24, 3, 0)
    # время ещё не наступило сегодня (21:00), вчерашний слот отработан
    last = ts(dt.datetime(2026, 11, 22, 21, 5))
    assert not sc.is_due("daily", 24, "21:00", last, NOW)
    assert sc.next_run("daily", 24, "21:00", last, NOW) == dt.datetime(2026, 11, 23, 21, 0)


def test_off():
    assert not sc.is_due("off", 1, "", 0, NOW) and sc.next_run("off", 1, "", 0, NOW) is None
