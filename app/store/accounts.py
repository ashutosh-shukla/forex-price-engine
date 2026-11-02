"""API client management (create, rotate keys, the local UI client)."""
from __future__ import annotations

import secrets
from pathlib import Path

from app.core.security import API_KEY_PREFIX, generate_api_key, hash_api_key
from app.store import repo

UI_CLIENT_ID = "localui00001"


async def create_client(db, pepper: str, name: str, **opts) -> tuple[dict, str]:
    """Returns (client_info, full_api_key). The key is shown once and stored only as a hash."""
    client_id, key, key_hash = generate_api_key(pepper)
    rec = await repo.create_client(db, client_id, key_hash, name, **opts)
    return rec.public(), key


async def rotate_key(db, pepper: str, client_id: str) -> str | None:
    key = f"{API_KEY_PREFIX}{client_id}.{secrets.token_urlsafe(32)}"
    ok = await repo.set_key_hash(db, client_id, hash_api_key(pepper, key))
    return key if ok else None


async def ensure_local_ui_client(db, pepper: str, key_file: Path) -> str:
    """Development only: a full-access client whose key lives in data/local_ui.key (read by the local UI)."""
    existing = await repo.get_client(db, UI_CLIENT_ID)
    if existing is not None and key_file.exists():
        key = key_file.read_text().strip()
        if existing.key_hash == hash_api_key(pepper, key):
            return key
    key = f"{API_KEY_PREFIX}{UI_CLIENT_ID}.{secrets.token_urlsafe(32)}"
    key_hash = hash_api_key(pepper, key)
    if existing is None:
        await repo.create_client(db, UI_CLIENT_ID, key_hash, "local-ui", allow_metrics=True, allow_ws=True, allow_ticks=True,
                                 rate_limit_per_min=0, max_ws_connections=1000, ws_token_ttl_seconds=120, active=True)
    else:
        await repo.set_key_hash(db, UI_CLIENT_ID, key_hash)
        await repo.update_client(db, UI_CLIENT_ID, {"active": True, "rate_limit_per_min": 0, "allow_ticks": True})
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key)
    return key
