import asyncio

from app.api.wsmanager import ConnectionManager, WsClient
from app.core.hub import Hub


def test_hub_fanout_and_rate_window():
    hub, got = Hub(), []
    hub.subscribe(lambda ch, p: got.append((ch, p)))
    hub.subscribe(lambda ch, p: 1 / 0)  # a broken listener must not stop the others
    hub.publish("px:EUR_USD", "x")
    assert got == [("px:EUR_USD", "x")]
    assert [hub.incr_window("k", 60) for _ in range(3)] == [1, 2, 3]


def test_ws_manager_routes_and_drops_for_slow_clients():
    async def go():
        mgr = ConnectionManager()
        a, b = WsClient(None, "ip", 3), WsClient(None, "ip", 3)
        mgr.add_key(a, "px:EUR_USD"); mgr.add_key(b, "px:EUR_USD"); mgr.add_key(b, "cd:EUR_USD:1m")
        mgr.dispatch("px:EUR_USD", "t1")
        mgr.dispatch("cd:EUR_USD:1m", "c1")
        mgr.dispatch("px:GBP_USD", "nobody")
        assert list(a.queue) == ["t1"] and list(b.queue) == ["t1", "c1"]
        for i in range(5):
            mgr.dispatch("px:EUR_USD", f"x{i}")
        assert len(a.queue) == 3 and a.dropped == 3 and list(a.queue)[-1] == "x4"  # oldest dropped, newest kept
        mgr.remove_client(b)
        assert "cd:EUR_USD:1m" not in mgr.routes
    asyncio.run(go())
