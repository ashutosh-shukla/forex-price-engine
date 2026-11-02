"""Timeframe definitions. All buckets are aligned to UTC (epoch) boundaries.
Every timeframe above 1m is computed by SQL from stored 1m candles."""
from __future__ import annotations

TF_SECONDS: dict[str, int] = {
    "1m": 60, "2m": 120, "3m": 180, "5m": 300, "10m": 600, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "3h": 10800, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400,
}

def tf_seconds(tf: str) -> int:
    try:
        return TF_SECONDS[tf]
    except KeyError:
        raise ValueError(f"Unsupported timeframe '{tf}'") from None


def bucket_start_ms(ts_ms: int, tf_ms: int) -> int:
    return ts_ms - ts_ms % tf_ms


def is_valid_tf(tf: str) -> bool:
    return tf in TF_SECONDS
