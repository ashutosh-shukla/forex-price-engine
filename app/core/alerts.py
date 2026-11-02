"""Optional webhook alerts (Slack / Discord / any JSON webhook) with a per-key cooldown."""
from __future__ import annotations

import time

import httpx

from app.core import logs


class Alerter:
    def __init__(self, url: str, cooldown_s: int):
        self.url = url
        self.cooldown = cooldown_s
        self._last: dict[str, float] = {}

    async def send(self, key: str, message: str) -> None:
        if not self.url:
            return
        now = time.monotonic()
        if now - self._last.get(key, -1e9) < self.cooldown:
            return
        self._last[key] = now
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                # "text" is read by Slack, "content" by Discord
                await client.post(self.url, json={"text": message, "content": message})
            logs.activity(f"An alert was sent to the webhook: {message}")
        except Exception as exc:
            logs.error_throttled("alert-send", "Could not deliver an alert to the webhook.", exc)
