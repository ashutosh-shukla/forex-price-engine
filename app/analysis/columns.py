"""Single source of truth for the PE_ANALYSIS table: column names, SQL types and UI groups."""
from __future__ import annotations

ROLL_H = (5, 15, 30, 60)          # rolling windows in seconds
TYPES = {"S20": "VARCHAR2(20)", "S8": "VARCHAR2(8)", "S120": "VARCHAR2(120)", "S1000": "VARCHAR2(1000)",
         "N": "NUMBER(16)", "I": "NUMBER(10)", "B": "NUMBER(1)", "D": "BINARY_DOUBLE"}

# (name, type, group)
COLUMNS: list[tuple[str, str, str]] = [
    ("symbol", "S20", "key"), ("tf", "S8", "key"), ("ts_ms", "N", "key"),
    ("run_id", "N", "meta"), ("sessions", "S120", "meta"), ("partial", "B", "meta"), ("captured_ms", "N", "meta"),
    # --- the candle itself (built from the live stream)
    ("open_px", "D", "core"), ("high_px", "D", "core"), ("low_px", "D", "core"), ("close_px", "D", "core"),
    ("rng", "D", "core"), ("tick_count", "I", "core"), ("direction", "S8", "core"), ("body_ratio", "D", "core"),
    ("net_move", "D", "core"), ("start_mid", "D", "core"), ("momentum", "D", "core"), ("ret_pct", "D", "core"),
    ("logret_pct", "D", "core"), ("momentum_accel", "D", "core"),
    # --- order-flow style metrics
    ("max_jump", "D", "flow"), ("jump_share", "D", "flow"), ("max_run_up", "I", "flow"), ("max_run_down", "I", "flow"),
    ("max_pullback_high", "D", "flow"), ("max_bounce_low", "D", "flow"), ("up_ticks", "I", "flow"), ("down_ticks", "I", "flow"),
    ("imbalance", "D", "flow"), ("move_imbalance", "D", "flow"), ("move_up", "D", "flow"), ("move_down", "D", "flow"),
    ("path_length", "D", "flow"), ("reversals", "I", "flow"), ("rv", "D", "flow"), ("rv_pct", "D", "flow"),
    ("tick_rate", "D", "flow"), ("avg_spread", "D", "flow"), ("min_spread", "D", "flow"), ("max_spread", "D", "flow"),
]
for _h in ROLL_H:   # rolling windows measured at the candle close
    COLUMNS += [(f"ret_{_h}s", "D", "rolling"), (f"mom_{_h}s", "D", "rolling"), (f"rng_{_h}s", "D", "rolling"), (f"acc_{_h}s", "D", "rolling")]
COLUMNS += [
    ("ret_z", "D", "z"), ("range_z", "D", "z"), ("ticks_z", "D", "z"),
    # --- OANDA's official version of the same candle (filled a few seconds after the close)
    ("off_open", "D", "official"), ("off_high", "D", "official"), ("off_low", "D", "official"), ("off_close", "D", "official"),
    ("off_volume", "I", "official"), ("off_spread_close", "D", "official"), ("coverage", "D", "official"),
    # --- what happened afterwards (filled automatically as time passes)
    ("next_dir", "S8", "outcome"), ("next_ret_pct", "D", "outcome"), ("next_range", "D", "outcome"), ("next_body_ratio", "D", "outcome"),
    ("ret_5m", "D", "outcome"), ("ret_15m", "D", "outcome"), ("ret_60m", "D", "outcome"),
    ("mfe_5m", "D", "outcome"), ("mae_5m", "D", "outcome"), ("mfe_15m", "D", "outcome"), ("mae_15m", "D", "outcome"),
    ("mfe_60m", "D", "outcome"), ("mae_60m", "D", "outcome"),
    # --- yours
    ("algo_direction", "S8", "manual"), ("actual_direction", "S8", "manual"), ("label_15m", "S20", "manual"),
    ("note", "S1000", "manual"), ("updated_ms", "N", "manual"),
]

NAMES = [c[0] for c in COLUMNS]
GROUP = {c[0]: c[2] for c in COLUMNS}
KEY = ("symbol", "tf", "ts_ms")
CAPTURE = [n for n in NAMES if GROUP[n] in ("key", "meta", "core", "flow", "rolling", "z")]
UPDATABLE = {n for n in NAMES if GROUP[n] in ("official", "outcome", "manual")}
LABELS = ("continued", "reversed", "consolidated", "unclear")
DIRECTIONS = ("BUY", "SELL", "HOLD")


def ddl(table: str = "pe_analysis") -> str:
    lines = []
    for name, typ, grp in COLUMNS:
        nn = " NOT NULL" if grp == "key" else ""
        lines.append(f"        {name} {TYPES[typ]}{nn}")
    lines.append("        CONSTRAINT pe_analysis_pk PRIMARY KEY (symbol, tf, ts_ms)")
    return ",\n".join(lines)


def block(sql: str, codes=(-955,)) -> str:
    cond = " AND ".join(f"SQLCODE != {c}" for c in codes)
    return f"BEGIN\n    EXECUTE IMMEDIATE '{sql.strip().replace(chr(39), chr(39) * 2)}';\nEXCEPTION\n    WHEN OTHERS THEN\n        IF {cond} THEN RAISE; END IF;\nEND;\n/\n"


def schema_sql() -> str:
    out = ["\n-- =====================================================================",
           "-- Analysis screen tables (manual analysis runs, one row per closed candle, notes)",
           "-- =====================================================================\n",
           "-- 6) one row per closed candle captured while analysis is ON; OANDA-official values, outcomes and your labels fill in later\n"]
    out.append(block(f"CREATE TABLE pe_analysis (\n{ddl()}\n)"))
    out.append(block("CREATE INDEX pe_analysis_run_ix ON pe_analysis (run_id)", (-955, -1408)))
    out.append("-- 7) each press of Start analysis creates one run\n")
    out.append(block("""CREATE TABLE pe_analysis_runs (
        run_id      NUMBER(16)    NOT NULL,
        symbol      VARCHAR2(20)  NOT NULL,
        tfs         VARCHAR2(100),
        started_ms  NUMBER(16)    NOT NULL,
        stopped_ms  NUMBER(16),
        CONSTRAINT pe_analysis_runs_pk PRIMARY KEY (run_id)
)"""))
    out.append("-- 8) free-text notes with a time (IST in the UI)\n")
    out.append(block("""CREATE TABLE pe_notes (
        note_id     NUMBER(20)     NOT NULL,
        symbol      VARCHAR2(20),
        ts_ms       NUMBER(16)     NOT NULL,
        run_id      NUMBER(16),
        note_text   VARCHAR2(2000) NOT NULL,
        CONSTRAINT pe_notes_pk PRIMARY KEY (note_id)
)"""))
    return "\n".join(out)
