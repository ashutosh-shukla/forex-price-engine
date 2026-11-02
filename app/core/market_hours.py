"""Weekly forex market hours. Spot forex trades from Sunday 17:00 to Friday 17:00 New York time
(so the open and close move by one hour in UTC when the US changes its clocks)."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.core.timeutil import ist_str, zone

_OPEN_MIN = 17 * 60


def _ny(ms: int | float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=zone("America/New_York"))


def _at(local: datetime, days: int) -> datetime:
    """17:00 New York time, `days` days after `local`'s date (daylight-saving safe)."""
    d = local.replace(tzinfo=None) + timedelta(days=days)
    return datetime(d.year, d.month, d.day, 17, 0, tzinfo=local.tzinfo)


def market_clock(now_ms: int | float) -> dict:
    """Clock-based market state: open/closed plus the next open or close time."""
    ny = _ny(now_ms)
    wd, minutes = ny.weekday(), ny.hour * 60 + ny.minute
    closed = (wd == 4 and minutes >= _OPEN_MIN) or wd == 5 or (wd == 6 and minutes < _OPEN_MIN)
    if closed:
        nxt = _at(ny, (6 - wd) % 7)
    else:
        nxt = _at(ny, (4 - wd) % 7)
    nxt_ms = int(nxt.timestamp() * 1000)
    return {"open": not closed, "next_change_ms": nxt_ms, "next_change_ist": ist_str(nxt_ms, seconds=False),
            "text": (f"WEEKEND - market is off. It reopens {ist_str(nxt_ms, seconds=False)} IST." if closed
                     else f"Market is open. The weekly close is {ist_str(nxt_ms, seconds=False)} IST.")}
