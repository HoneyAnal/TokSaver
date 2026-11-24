"""Расписание автосинхронизации: чистые функции без UI (легко тестируются).

Режимы:
  off    — выключено
  hours  — раз в N часов от последнего запуска (N = 1, 3, 6, 12, 24)
  daily  — каждый день в HH:MM; если окно программы в это время было закрыто,
           пропущенный запуск догоняется при первом же открытии
last — момент (epoch) последнего запуска; при включении режима ставится «сейчас».
"""
import datetime as dt

MODES = ("off", "hours", "daily")
HOURS = (1, 3, 6, 12, 24)


def parse_time(text):
    """'03:30' → (3, 30); неверный формат → None."""
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def _last_slot(hhmm, now):
    """Самый поздний запланированный момент ≤ now для ежедневного режима."""
    h, m = parse_time(hhmm) or (3, 0)
    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return slot if slot <= now else slot - dt.timedelta(days=1)


def is_due(mode, hours, hhmm, last, now=None):
    now = now or dt.datetime.now()
    if mode == "hours":
        return now.timestamp() >= float(last) + int(hours) * 3600
    if mode == "daily":
        return float(last) < _last_slot(hhmm, now).timestamp()
    return False


def next_run(mode, hours, hhmm, last, now=None):
    """Когда сработает в следующий раз (datetime) или None, если выключено.
    Если запуск уже «просрочен» — возвращает now."""
    now = now or dt.datetime.now()
    if mode == "off":
        return None
    if is_due(mode, hours, hhmm, last, now):
        return now
    if mode == "hours":
        return dt.datetime.fromtimestamp(float(last) + int(hours) * 3600)
    return _last_slot(hhmm, now) + dt.timedelta(days=1)
