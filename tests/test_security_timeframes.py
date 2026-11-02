import time

from app.core import security
from app.core.timeframes import bucket_start_ms, tf_seconds


def test_api_key_roundtrip():
    cid, key, h = security.generate_api_key("pepper-" * 6)
    assert security.split_api_key(key) == cid
    assert security.verify_api_key("pepper-" * 6, key, h)
    assert not security.verify_api_key("pepper-" * 6, key + "x", h)
    assert security.split_api_key("garbage") is None


def test_ws_token():
    tok = security.make_ws_token("s" * 40, {"cid": "abc", "exp": time.time() + 30})
    assert security.verify_ws_token("s" * 40, tok)["cid"] == "abc"
    assert security.verify_ws_token("t" * 40, tok) is None
    expired = security.make_ws_token("s" * 40, {"cid": "abc", "exp": time.time() - 1})
    assert security.verify_ws_token("s" * 40, expired) is None
    assert security.verify_ws_token("s" * 40, tok[:-2] + "zz") is None


def test_ip_allowlist():
    assert security.ip_allowed("10.0.0.5", None)
    assert security.ip_allowed("10.0.0.5", ["10.0.0.0/24"])
    assert not security.ip_allowed("11.0.0.5", ["10.0.0.0/24"])


def test_buckets():
    assert tf_seconds("4h") == 14400
    assert bucket_start_ms(14400_000 * 3 + 5, 14400_000) == 14400_000 * 3
