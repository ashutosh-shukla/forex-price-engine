"""API keys (hashed at rest), short-lived websocket tokens, IP allow-lists."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import time

API_KEY_PREFIX = "pek_"


def hash_api_key(pepper: str, full_key: str) -> str:
    return hmac.new(pepper.encode(), full_key.encode(), hashlib.sha256).hexdigest()


def generate_api_key(pepper: str) -> tuple[str, str, str]:
    """Return (client_id, full_key, key_hash). The full key is shown to the admin once."""
    client_id = secrets.token_hex(6)
    full_key = f"{API_KEY_PREFIX}{client_id}.{secrets.token_urlsafe(32)}"
    return client_id, full_key, hash_api_key(pepper, full_key)


def split_api_key(full_key: str | None) -> str | None:
    """Extract the client id from a key like pek_<id>.<secret>. None if malformed."""
    if not full_key or not full_key.startswith(API_KEY_PREFIX) or "." not in full_key:
        return None
    cid = full_key[len(API_KEY_PREFIX):].split(".", 1)[0]
    return cid if 6 <= len(cid) <= 32 and cid.isalnum() else None


def verify_api_key(pepper: str, full_key: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(pepper, full_key), expected_hash)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_ws_token(secret: str, claims: dict) -> str:
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_ws_token(secret: str, token: str, now: float | None = None) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(_unb64(body))
        if claims.get("exp", 0) < (now if now is not None else time.time()):
            return None
        return claims
    except Exception:
        return None


def ip_allowed(ip: str, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in ipaddress.ip_network(a, strict=False) for a in allowed)
    except ValueError:
        return False


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
