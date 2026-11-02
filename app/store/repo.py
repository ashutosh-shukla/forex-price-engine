"""All SQL the engine runs against Oracle. Written for Oracle 19c / 23ai (Autonomous Database)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import oracledb

from app.core.metrics import DB_COLUMNS, INT_FIELDS, RAW_FIELDS, db_col
from app.core.timeframes import TF_SECONDS

# ---------------------------------------------------------------------------- candles
# input sizes: an integer means "string of up to N characters"
_UPSERT_TYPES = [20, oracledb.DB_TYPE_NUMBER]
_UPSERT_TYPES += [oracledb.DB_TYPE_NUMBER if f in INT_FIELDS else oracledb.DB_TYPE_BINARY_DOUBLE for f in RAW_FIELDS]
_UPSERT_TYPES += [20]

_SET_COLS = DB_COLUMNS[2:]  # everything except symbol, ts_ms
_USING = ", ".join(f":{i + 1} AS {c}" for i, c in enumerate(DB_COLUMNS))
_UPSERT_SQL = (
    f"MERGE INTO pe_candles_1m t USING (SELECT {_USING} FROM dual) s "
    f"ON (t.symbol = s.symbol AND t.ts_ms = s.ts_ms) "
    f"WHEN MATCHED THEN UPDATE SET {', '.join(f't.{c} = s.{c}' for c in _SET_COLS)} "
    f"WHERE t.source <> 'live' OR s.tick_count >= NVL(t.tick_count, 0) "
    f"WHEN NOT MATCHED THEN INSERT ({', '.join(DB_COLUMNS)}) VALUES ({', '.join('s.' + c for c in DB_COLUMNS)})"
)

_BACKFILL_SQL = (
    "MERGE INTO pe_candles_1m t USING (SELECT :1 AS symbol, :2 AS ts_ms, :3 AS open_px, :4 AS high_px, :5 AS low_px, "
    ":6 AS close_px, :7 AS tick_count FROM dual) s ON (t.symbol = s.symbol AND t.ts_ms = s.ts_ms) "
    "WHEN NOT MATCHED THEN INSERT (symbol, ts_ms, open_px, high_px, low_px, close_px, tick_count, source) "
    "VALUES (s.symbol, s.ts_ms, s.open_px, s.high_px, s.low_px, s.close_px, s.tick_count, 'backfill')"
)
_BACKFILL_TYPES = [20, oracledb.DB_TYPE_NUMBER] + [oracledb.DB_TYPE_BINARY_DOUBLE] * 4 + [oracledb.DB_TYPE_NUMBER]


async def upsert_candles(db, rows: list[tuple]) -> int:
    return await db.executemany(_UPSERT_SQL, rows, _UPSERT_TYPES)


async def insert_missing_candles(db, rows: list[tuple]) -> int:
    """rows: (symbol, ts_ms, open, high, low, close, tick_count). Existing minutes are never touched."""
    return await db.executemany(_BACKFILL_SQL, rows, _BACKFILL_TYPES)


async def latest_candle_ts(db, symbol: str) -> int | None:
    v = await db.fetchval("SELECT MAX(ts_ms) FROM pe_candles_1m WHERE symbol = :s", {"s": symbol})
    return int(v) if v is not None else None


_RAW_SELECT = ", ".join(db_col(f) for f in RAW_FIELDS)
_AGG_SELECT = """
  MIN(open_px)  KEEP (DENSE_RANK FIRST ORDER BY ts_ms) AS open_px,
  MAX(high_px)  AS high_px,
  MIN(low_px)   AS low_px,
  MIN(close_px) KEEP (DENSE_RANK LAST ORDER BY ts_ms)  AS close_px,
  SUM(tick_count) AS tick_count, SUM(up_ticks) AS up_ticks, SUM(down_ticks) AS down_ticks,
  SUM(move_up) AS move_up, SUM(move_down) AS move_down, SUM(path_length) AS path_length,
  SUM(reversals) AS reversals, MAX(max_run_up) AS max_run_up, MAX(max_run_down) AS max_run_down,
  MAX(max_jump_up) AS max_jump_up, MAX(max_jump_down) AS max_jump_down,
  SUM(spread_sum) AS spread_sum, MIN(min_spread) AS min_spread, MAX(max_spread) AS max_spread,
  SUM(mid_sum) AS mid_sum, SUM(sum_sq_move) AS sum_sq_move,
  MIN(start_mid) KEEP (DENSE_RANK FIRST ORDER BY ts_ms) AS start_mid,
  MIN(bid_close) KEEP (DENSE_RANK LAST ORDER BY ts_ms) AS bid_close,
  MIN(ask_close) KEEP (DENSE_RANK LAST ORDER BY ts_ms) AS ask_close,
  COUNT(*) AS minutes_present, COUNT(up_ticks) AS metric_minutes"""

_DAY_MS = 86_400_000


@dataclass
class CandlePage:
    rows: list[dict]
    next_from: int | None = None


async def fetch_candles(db, symbol: str, tf: str, from_ms: int | None, to_ms: int | None, limit: int) -> CandlePage:
    """Oldest -> newest. Without `from`: the latest `limit` candles. With `from`: pages forward (use next_from)."""
    tf_ms = TF_SECONDS[tf] * 1000
    now_ms = int(time.time() * 1000)
    asc = from_ms is not None
    hi_req = to_ms if to_ms is not None else now_ms + tf_ms
    next_from = None

    if tf == "1m":
        lo = from_ms if asc else 0
        order = "ASC" if asc else "DESC"
        rows = await db.fetchall(
            f"SELECT ts_ms AS t, {_RAW_SELECT}, 1 AS minutes_present, CASE WHEN up_ticks IS NULL THEN 0 ELSE 1 END AS metric_minutes "
            f"FROM pe_candles_1m WHERE symbol = :sym AND ts_ms >= :lo AND ts_ms < :hi ORDER BY ts_ms {order} FETCH FIRST :n ROWS ONLY",
            {"sym": symbol, "lo": lo, "hi": hi_req, "n": limit})
        if not asc:
            rows.reverse()
        elif len(rows) == limit:
            next_from = int(rows[-1]["t"]) + tf_ms
        return CandlePage(rows, next_from)

    # bound the scan: enough 1m rows for `limit` buckets plus weekend gaps
    window = int(limit * tf_ms * 1.5) + 3 * _DAY_MS
    if asc:
        lo = (from_ms // tf_ms) * tf_ms
        hi = min(hi_req, ((lo + window) // tf_ms) * tf_ms)
        truncated = hi < hi_req
    else:
        hi = hi_req
        lo = max(0, ((hi - window) // tf_ms) * tf_ms)
        truncated = False
    rows = await db.fetchall(
        f"SELECT bucket AS t, {_AGG_SELECT} FROM ("
        f"SELECT FLOOR(ts_ms / :ms) * :ms AS bucket, c.* FROM pe_candles_1m c WHERE symbol = :sym AND ts_ms >= :lo AND ts_ms < :hi) "
        f"GROUP BY bucket ORDER BY bucket {'ASC' if asc else 'DESC'} FETCH FIRST :n ROWS ONLY",
        {"ms": tf_ms, "sym": symbol, "lo": lo, "hi": hi, "n": limit})
    if not asc:
        rows.reverse()
    else:
        if len(rows) == limit:
            next_from = int(rows[-1]["t"]) + tf_ms
        elif truncated:
            next_from = hi
    return CandlePage(rows, next_from)


async def prune_candles(db, cutoff_ms: int) -> int:
    return await db.execute("DELETE FROM pe_candles_1m WHERE ts_ms < :c AND ROWNUM <= 50000", {"c": cutoff_ms})


# ---------------------------------------------------------------------------- ticks
_TICK_SQL = "INSERT INTO pe_ticks (symbol, ts_ms, bid, ask) VALUES (:1, :2, :3, :4)"
_TICK_TYPES = [20, oracledb.DB_TYPE_NUMBER, oracledb.DB_TYPE_BINARY_DOUBLE, oracledb.DB_TYPE_BINARY_DOUBLE]


async def insert_ticks(db, rows: list[tuple]) -> int:
    return await db.executemany(_TICK_SQL, rows, _TICK_TYPES)


async def fetch_ticks(db, symbol: str, from_ms: int | None, to_ms: int | None, limit: int) -> list[list]:
    now_ms = int(time.time() * 1000)
    lo = from_ms if from_ms is not None else now_ms - 10 * 60_000
    hi = to_ms if to_ms is not None else now_ms + 60_000
    rows = await db.fetchall(
        "SELECT ts_ms AS t, bid, ask FROM pe_ticks WHERE symbol = :sym AND ts_ms >= :lo AND ts_ms < :hi ORDER BY ts_ms FETCH FIRST :n ROWS ONLY",
        {"sym": symbol, "lo": lo, "hi": hi, "n": limit})
    return [[int(r["t"]), r["bid"], r["ask"]] for r in rows]


async def prune_ticks(db, cutoff_ms: int) -> int:
    return await db.execute("DELETE FROM pe_ticks WHERE ts_ms < :c AND ROWNUM <= 50000", {"c": cutoff_ms})


# ---------------------------------------------------------------------------- pairs
async def list_pairs(db) -> list[dict]:
    rows = await db.fetchall("SELECT symbol, display_name, price_decimals, active FROM pe_pairs ORDER BY symbol")
    for r in rows:
        r["active"] = bool(r["active"])
        r["price_decimals"] = int(r["price_decimals"])
    return rows


async def active_symbols(db) -> list[str]:
    rows = await db.fetchall("SELECT symbol FROM pe_pairs WHERE active = 1 ORDER BY symbol")
    return [r["symbol"] for r in rows]


async def upsert_pair(db, symbol: str, display_name: str | None, decimals: int, active: bool, overwrite: bool) -> None:
    upd = "WHEN MATCHED THEN UPDATE SET t.active = s.active, t.display_name = NVL(s.display_name, t.display_name), t.price_decimals = s.decimals " if overwrite else ""
    await db.execute(
        f"MERGE INTO pe_pairs t USING (SELECT :1 AS symbol, :2 AS display_name, :3 AS decimals, :4 AS active FROM dual) s "
        f"ON (t.symbol = s.symbol) {upd}"
        f"WHEN NOT MATCHED THEN INSERT (symbol, display_name, price_decimals, active) VALUES (s.symbol, s.display_name, s.decimals, s.active)",
        [symbol, display_name, decimals, 1 if active else 0])


async def update_pair(db, symbol: str, changes: dict) -> bool:
    cols = {"active": "active", "display_name": "display_name", "price_decimals": "price_decimals"}
    sets, params = [], {"sym": symbol}
    for k, v in changes.items():
        if k in cols:
            sets.append(f"{cols[k]} = :{k}")
            params[k] = (1 if v else 0) if k == "active" else v
    if not sets:
        return False
    return (await db.execute(f"UPDATE pe_pairs SET {', '.join(sets)} WHERE symbol = :sym", params)) > 0


# ---------------------------------------------------------------------------- clients
def _split(v: str | None) -> list[str] | None:
    items = [x.strip() for x in (v or "").split(",") if x.strip()]
    return items or None


def _join(v) -> str | None:
    return ",".join(v) if v else None


@dataclass
class ClientRecord:
    client_id: str
    name: str
    key_hash: str
    allowed_symbols: set | None
    allowed_timeframes: set | None
    allow_metrics: bool
    allow_ws: bool
    allow_ticks: bool
    rate_limit_per_min: int
    max_ws_connections: int
    allowed_ips: list = field(default_factory=list)
    ws_token_ttl_seconds: int = 60
    active: bool = True

    @classmethod
    def from_row(cls, r: dict) -> "ClientRecord":
        syms, tfs = _split(r["allowed_symbols"]), _split(r["allowed_timeframes"])
        return cls(
            client_id=r["client_id"], name=r["name"], key_hash=r["key_hash"],
            allowed_symbols=set(syms) if syms else None, allowed_timeframes=set(tfs) if tfs else None,
            allow_metrics=bool(r["allow_metrics"]), allow_ws=bool(r["allow_ws"]), allow_ticks=bool(r["allow_ticks"]),
            rate_limit_per_min=int(r["rate_limit_per_min"]), max_ws_connections=int(r["max_ws_connections"]),
            allowed_ips=_split(r["allowed_ips"]) or [], ws_token_ttl_seconds=int(r["ws_token_ttl_seconds"]), active=bool(r["active"]))

    def public(self) -> dict:
        return {
            "client_id": self.client_id, "name": self.name,
            "allowed_symbols": sorted(self.allowed_symbols) if self.allowed_symbols else None,
            "allowed_timeframes": sorted(self.allowed_timeframes) if self.allowed_timeframes else None,
            "allow_metrics": self.allow_metrics, "allow_ws": self.allow_ws, "allow_ticks": self.allow_ticks,
            "rate_limit_per_min": self.rate_limit_per_min, "max_ws_connections": self.max_ws_connections,
            "allowed_ips": self.allowed_ips, "ws_token_ttl_seconds": self.ws_token_ttl_seconds, "active": self.active,
        }


CLIENT_FIELDS = ("name", "allowed_symbols", "allowed_timeframes", "allow_metrics", "allow_ws", "allow_ticks",
                 "rate_limit_per_min", "max_ws_connections", "allowed_ips", "ws_token_ttl_seconds", "active")
_LIST_FIELDS = {"allowed_symbols", "allowed_timeframes", "allowed_ips"}
_BOOL_FIELDS = {"allow_metrics", "allow_ws", "allow_ticks", "active"}


def _encode(k: str, v):
    if k in _LIST_FIELDS:
        return _join(v)
    if k in _BOOL_FIELDS:
        return 1 if v else 0
    return v


async def get_client(db, client_id: str) -> ClientRecord | None:
    rows = await db.fetchall("SELECT * FROM pe_clients WHERE client_id = :c", {"c": client_id})
    return ClientRecord.from_row(rows[0]) if rows else None


async def create_client(db, client_id: str, key_hash: str, name: str, **opts) -> ClientRecord:
    cols = {"client_id": client_id, "name": name, "key_hash": key_hash}
    for k, v in opts.items():
        if k in CLIENT_FIELDS and k != "name" and v is not None:
            cols[k] = _encode(k, v)
    names = list(cols)
    await db.execute(f"INSERT INTO pe_clients ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})", cols)
    return await get_client(db, client_id)


async def update_client(db, client_id: str, changes: dict) -> ClientRecord | None:
    params = {"cid": client_id}
    sets = []
    for k, v in changes.items():
        if k in CLIENT_FIELDS:
            sets.append(f"{k} = :{k}")
            params[k] = _encode(k, v)
    if not sets:
        return None
    n = await db.execute(f"UPDATE pe_clients SET {', '.join(sets)} WHERE client_id = :cid", params)
    return await get_client(db, client_id) if n else None


async def set_key_hash(db, client_id: str, key_hash: str) -> bool:
    return (await db.execute("UPDATE pe_clients SET key_hash = :h WHERE client_id = :c", {"h": key_hash, "c": client_id})) > 0


async def list_clients(db) -> list[dict]:
    rows = await db.fetchall("SELECT * FROM pe_clients ORDER BY created_at")
    out = []
    for r in rows:
        d = ClientRecord.from_row(r).public()
        d["last_used_at"] = r["last_used_at"].isoformat() if r.get("last_used_at") else None
        out.append(d)
    return out


async def touch_client(db, client_id: str) -> None:
    await db.execute("UPDATE pe_clients SET last_used_at = SYSTIMESTAMP WHERE client_id = :c", {"c": client_id})
