"""Time helpers. The whole application shows Indian Standard Time (IST, UTC+5:30)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

IST = timezone(timedelta(hours=5, minutes=30), "IST")
UTC = timezone.utc

# used only if the 'tzdata' package is missing on Windows (daylight saving would then be wrong)
_FALLBACK = {"Asia/Tokyo": 9.0, "Europe/London": 0.0, "America/New_York": -4.0}
_warned = False


def zone(name: str) -> tzinfo:
    global _warned
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        if not _warned:
            _warned = True
            try:
                from app.core import logs
                logs.error("The 'tzdata' package is missing, so daylight-saving changes are not handled. Run: python -m pip install -r requirements.txt")
            except Exception:
                pass
        return timezone(timedelta(hours=_FALLBACK.get(name, 0.0)), name)


def ist_dt(ms: int | float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=IST)


def ist_str(ms: int | float | None, seconds: bool = True) -> str:
    if ms is None:
        return ""
    return ist_dt(ms).strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")
