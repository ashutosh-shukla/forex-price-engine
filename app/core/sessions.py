"""Trading-session windows, shown in IST.

SESSION_MODE=local  (default) windows follow the real local clocks (Tokyo, London, New York), so in IST they move by
                    an hour when London or the US change their clocks.
SESSION_MODE=fixed  windows are fixed IST clock times, exactly as you wrote them.

The local-time windows were chosen so that they equal your IST windows today (26 Oct 2026):
  Tokyo pre 04:30-05:30 IST, Tokyo 05:30-11:30, Tokyo post 11:30-13:30, London 11:30-22:00,
  New York pre 17:00-19:00, New York 19:00-01:30.
"""
from __future__ import annotations

from datetime import datetime

from app.core.timeutil import IST, zone

# (name, zone, (start h, m), (end h, m)) in local time
LOCAL = [
    ("Tokyo pre", "Asia/Tokyo", (8, 0), (9, 0)),
    ("Tokyo", "Asia/Tokyo", (9, 0), (15, 0)),
    ("Tokyo post", "Asia/Tokyo", (15, 0), (17, 0)),
    ("London", "Europe/London", (6, 0), (16, 30)),
    ("New York pre", "America/New_York", (7, 30), (9, 30)),
    ("New York", "America/New_York", (9, 30), (16, 0)),
]
FIXED_IST = [
    ("Tokyo pre", (4, 30), (5, 30)), ("Tokyo", (5, 30), (11, 30)), ("Tokyo post", (11, 30), (13, 30)),
    ("London", (11, 30), (22, 0)), ("New York pre", (17, 0), (19, 0)), ("New York", (19, 0), (1, 30)),
]


def _inside(minute: int, start: tuple, end: tuple) -> bool:
    a, b = start[0] * 60 + start[1], end[0] * 60 + end[1]
    return (a <= minute < b) if a < b else (minute >= a or minute < b)


def sessions_at(ts_ms: int | float, mode: str = "local") -> list[str]:
    out = []
    if mode == "fixed":
        d = datetime.fromtimestamp(ts_ms / 1000.0, tz=IST)
        m = d.hour * 60 + d.minute
        return [n for n, a, b in FIXED_IST if _inside(m, a, b)]
    for name, zn, a, b in LOCAL:
        d = datetime.fromtimestamp(ts_ms / 1000.0, tz=zone(zn))
        if _inside(d.hour * 60 + d.minute, a, b):
            out.append(name)
    return out


def session_text(ts_ms, mode: str = "local") -> str:
    return ", ".join(sessions_at(ts_ms, mode))
