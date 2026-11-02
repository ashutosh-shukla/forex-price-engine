"""Websocket fan-out bookkeeping (no web-framework imports, so it is easy to unit test)."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque


class WsClient:
    __slots__ = ("ws", "client_id", "conn_id", "ip", "queue", "event", "claims", "keys", "dropped", "connected_at")

    def __init__(self, ws, ip: str, queue_max: int):
        self.ws = ws
        self.ip = ip
        self.client_id = ""
        self.conn_id = uuid.uuid4().hex[:12]
        self.queue: deque[str] = deque(maxlen=queue_max)
        self.event = asyncio.Event()
        self.claims: dict = {}
        self.keys: set[str] = set()
        self.dropped = 0
        self.connected_at = time.time()

    def push(self, payload: str) -> None:
        q = self.queue
        if len(q) == q.maxlen:
            self.dropped += 1  # slow client: its oldest message is discarded, nobody else is affected
        q.append(payload)
        self.event.set()


class ConnectionManager:
    def __init__(self):
        self.routes: dict[str, set[WsClient]] = {}
        self.total = 0
        self.by_ip: dict[str, int] = defaultdict(int)
        self.by_client: dict[str, int] = defaultdict(int)
        self.messages_forwarded = 0

    def add_key(self, c: WsClient, key: str) -> None:
        self.routes.setdefault(key, set()).add(c)
        c.keys.add(key)

    def remove_key(self, c: WsClient, key: str) -> None:
        s = self.routes.get(key)
        if s:
            s.discard(c)
            if not s:
                del self.routes[key]
        c.keys.discard(key)

    def remove_client(self, c: WsClient) -> None:
        for key in list(c.keys):
            self.remove_key(c, key)

    def dispatch(self, channel: str, payload: str) -> None:
        """Hub listener: forward one pre-built message to everyone subscribed to this channel."""
        subs = self.routes.get(channel)
        if subs:
            for c in subs:  # no awaits in here, so the set cannot change while iterating
                c.push(payload)
            self.messages_forwarded += len(subs)
