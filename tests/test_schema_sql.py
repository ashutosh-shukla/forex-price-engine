import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _split(sql):
    out = []
    for b in re.split(r"^\s*/\s*$", sql, flags=re.M):
        code = "\n".join(l for l in b.splitlines() if l.strip() and not l.strip().startswith("--")).strip()
        if code:
            out.append(code)
    return out


def test_schema_blocks_are_plsql_and_complete():
    blocks = _split((ROOT / "sql" / "oracle_schema.sql").read_text())
    assert len(blocks) == 10
    assert all(b.startswith("BEGIN") and b.rstrip().endswith("END;") for b in blocks)
    text = "\n".join(blocks)
    for t in ("pe_pairs", "pe_clients", "pe_candles_1m", "pe_ticks", "pe_analysis", "pe_analysis_runs", "pe_notes"):
        assert f"CREATE TABLE {t}" in text


def test_every_candle_column_exists_in_schema():
    from app.core.metrics import DB_COLUMNS
    text = (ROOT / "sql" / "oracle_schema.sql").read_text()
    for c in DB_COLUMNS:
        assert re.search(rf"\b{c}\b", text), c


def test_every_analysis_column_exists_in_schema():
    from app.analysis.columns import NAMES
    text = (ROOT / "sql" / "oracle_schema.sql").read_text()
    seg = text[text.index("CREATE TABLE pe_analysis ("):text.index("CREATE INDEX pe_analysis_run_ix")]
    for c in NAMES:
        assert re.search(rf"\b{c}\b", seg), c
