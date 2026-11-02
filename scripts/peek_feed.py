"""Show what OANDA really sends for one pair: price levels and the `liquidity` sizes.

python -m scripts.peek_feed XAU_USD

1) takes one snapshot from the normal (REST) pricing endpoint, which also works when the market is closed,
2) streams for up to 60 seconds if the stream endpoint is reachable,
3) tries the orderBook and positionBook endpoints.
Your API key is never printed.
"""

import json
import sys
import time

import httpx

from app.core.config import get_settings


def summarize(prices: list) -> None:
    shapes, bid_liq, ask_liq = set(), set(), set()
    for m in prices:
        b, a = m.get("bids", []), m.get("asks", [])
        shapes.add((len(b), len(a)))
        bid_liq.update(x.get("liquidity") for x in b)
        ask_liq.update(x.get("liquidity") for x in a)
        print("levels per message (bid levels, ask levels):", sorted(shapes))
        print(
            "distinct bid liquidity values:",
            len(bid_liq),
            sorted(v for v in bid_liq if v is not None)[:12],
        )
        print(
            "distinct ask liquidity values:",
            len(ask_liq),
            sorted(v for v in ask_liq if v is not None)[:12],
        )


def main() -> None:
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "XAU_USD"
    s = get_settings()
    headers = {
        "Authorization": f"Bearer {s.OANDA_API_KEY}",
        "Accept-Datetime-Format": "UNIX",
    }

    print("== 1. REST price snapshot ==")
    r = httpx.get(
        f"{s.OANDA_API_URL}/v3/accounts/{s.OANDA_ACCOUNT_ID}/pricing",
        params={"instruments": sym},
        headers=headers,
        timeout=30,
    )
    print("status:", r.status_code)
    if r.status_code == 200:
        prices = r.json().get("prices", [])
        print(json.dumps(prices[:1], indent=1))
        summarize(prices)
    else:
        print(r.text[:300])

        print("\n== 2. Price stream (up to 60 s) ==")
        seen, collected, t0 = 0, [], time.time()
        try:
            with httpx.stream(
                "GET",
                f"{s.OANDA_STREAM_URL}/v3/accounts/{s.OANDA_ACCOUNT_ID}/pricing/stream",
                params={"instruments": sym},
                headers=headers,
                timeout=30,
            ) as resp:
                print("status:", resp.status_code)
                if resp.status_code != 200:
                    print(resp.read().decode(errors="replace")[:300])
                else:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        m = json.loads(line)
                        if m.get("type") != "PRICE":
                            continue
                        seen += 1
                        collected.append(m)
                        if seen <= 2:
                            print(json.dumps(m))
                            if seen >= 300 or time.time() - t0 > 60:
                                break
                            print(f"{seen} price messages in {time.time() - t0:.0f}s")
                            if collected:
                                summarize(collected)
        except Exception as exc:
            print("stream failed:", exc)

            print("\n== 3. Books ==")
            for book in ("orderBook", "positionBook"):
                r = httpx.get(
                    f"{s.OANDA_API_URL}/v3/instruments/{sym}/{book}",
                    headers=headers,
                    timeout=30,
                )
                print(f"{book}: HTTP {r.status_code}  {r.text[:300]!r}")


if __name__ == "__main__":
    main()
