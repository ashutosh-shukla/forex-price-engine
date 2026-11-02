"""JSON helpers. Uses orjson when installed (much faster), falls back to stdlib json."""
from __future__ import annotations

try:
    import orjson

    def dumps(obj) -> str:
        return orjson.dumps(obj).decode()

    def dumps_bytes(obj) -> bytes:
        return orjson.dumps(obj)

    def loads(data):
        return orjson.loads(data)

except ImportError:  # pragma: no cover - only used when orjson is missing
    import json

    def dumps(obj) -> str:
        return json.dumps(obj, separators=(",", ":"))

    def dumps_bytes(obj) -> bytes:
        return json.dumps(obj, separators=(",", ":")).encode()

    def loads(data):
        return json.loads(data)
