#!/usr/bin/env python3
"""
FEED PROBE - checks which data your OANDA practice account really gives us, on the LIVE feed.

Run it while the forex market is OPEN (it exits early with a clear message if the market is closed):

    python scripts/feed_probe.py                      (default: XAU_USD, 5 minutes)
    python scripts/feed_probe.py --seconds 600        (longer = more complete minutes to compare)
    python scripts/feed_probe.py --instrument EUR_USD

    Needs only Python 3.9+ (no extra packages). Reads OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_API_URL and
    OANDA_STREAM_URL from your .env file. Your key is never printed or saved.

    It writes three files into the "data" folder (safe to share, they contain prices only):
        feed_probe_<time>.json   the verdict report  (paste this back to me)
        feed_probe_ticks_<time>.csv      every tick seen
        feed_probe_candles_<time>.csv    1-minute candles built from those ticks with ALL planned columns

"""

import argparse
import bisect
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
EPS = 1e-9
VERDICTS = []  # (feature, status, evidence)
REPORT = {}


# ------------------------------------------------------------------ helpers
def ist(ts):
    return datetime.fromtimestamp(ts, tz=IST).strftime("%Y-%m-%d %H:%M:%S")


def say(msg=""):
    print(msg, flush=True)


def head(title):
    say("\n" + "=" * 78)
    say(title)
    say("=" * 78)


def verdict(feature, status, evidence):
    VERDICTS.append((feature, status, evidence))
    say(f"  -> [{status}] {feature}: {evidence}")


def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def med(vals):
    return pct(vals, 50)


def fnum(x, d=4):
    return "-" if x is None else f"{x:.{d}f}"


def parse_time(s):
    """unix seconds ("1700000000.0") or RFC3339 ("2026-10-26T02:40:00Z") -> unix seconds"""
    try:
        return float(s)
    except (TypeError, ValueError):
        return (
            datetime.fromisoformat(
                str(s).replace("Z", "+00:00").split(".")[0]
                + ("+00:00" if "+" not in str(s) and not str(s).endswith("Z") else "")
            ).timestamp()
            if "T" in str(s)
            else None
        )


def load_cfg():
    cfg = {}
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (
        os.path.join(os.getcwd(), ".env"),
        os.path.join(here, ".env"),
        os.path.join(here, "..", ".env"),
    ):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        cfg[k.strip()] = v.strip().strip('"').strip("'")
                        break
                    for k in (
                        "OANDA_API_KEY",
                        "OANDA_ACCOUNT_ID",
                        "OANDA_API_URL",
                        "OANDA_STREAM_URL",
                    ):
                        if os.environ.get(k):
                            cfg[k] = os.environ[k]
                            cfg.setdefault(
                                "OANDA_API_URL", "https://api-fxpractice.oanda.com"
                            )
                            cfg.setdefault(
                                "OANDA_STREAM_URL",
                                "https://stream-fxpractice.oanda.com",
                            )
                            return cfg


def get_json(url, headers, params=None, timeout=30):
    if params:
        url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")[:300]
        except Exception as e:  # network problems
            return None, f"{type(e).__name__}: {e}"

        SESSIONS = [  # my reading of your IST windows - please confirm (start hour/min, end hour/min)
            ("Tokyo pre", (4, 30), (5, 30)),
            ("Tokyo", (5, 30), (11, 30)),
            ("Tokyo post", (11, 30), (13, 30)),
            ("London", (11, 30), (22, 0)),
            ("New York pre", (17, 0), (19, 0)),
            ("New York", (19, 0), (1, 30)),
        ]


def active_sessions(ts):
    d = datetime.fromtimestamp(ts, tz=IST)
    m = d.hour * 60 + d.minute
    out = []
    for name, (sh, sm), (eh, em) in SESSIONS:
        a, b = sh * 60 + sm, eh * 60 + em
        if (a <= m < b) if a < b else (m >= a or m < b):
            out.append(name)
            return out

        # ------------------------------------------------------------------ 1. account + instrument


def check_account(cfg, H):
    head("1. ACCOUNT AND INSTRUMENT")
    acct = cfg["OANDA_ACCOUNT_ID"]
    st, d = get_json(f"{cfg['OANDA_API_URL']}/v3/accounts/{acct}/summary", H)
    if st == 200:
        a = d.get("account", {})
        verdict(
            "Key and account work",
            "YES",
            f"account {acct[:7]}..., currency {a.get('currency')}",
        )
    else:
        verdict("Key and account work", "NO", f"HTTP {st}: {d}")
        return st == 200


def check_instrument(cfg, H, ins):
    acct = cfg["OANDA_ACCOUNT_ID"]
    st, d = get_json(
        f"{cfg['OANDA_API_URL']}/v3/accounts/{acct}/instruments",
        H,
        {"instruments": ins},
    )
    if st != 200:
        st, d = get_json(f"{cfg['OANDA_API_URL']}/v3/accounts/{acct}/instruments", H)
        if st == 200 and isinstance(d, dict):
            d = {
                "instruments": [
                    i for i in d.get("instruments", []) if i.get("name") == ins
                ]
            }
            if st == 200 and d.get("instruments"):
                i = d["instruments"][0]
                keys = (
                    "name",
                    "displayPrecision",
                    "pipLocation",
                    "tradeUnitsPrecision",
                    "minimumTradeSize",
                    "marginRate",
                    "maximumOrderUnits",
                )
                info = {k: i.get(k) for k in keys if k in i}
                REPORT["instrument_spec"] = info
                say("  " + json.dumps(info))
                verdict(
                    "Instrument specification (precision, pip, min size)",
                    "YES",
                    f"{len(info)} fields",
                )
            else:
                verdict(
                    "Instrument specification (precision, pip, min size)",
                    "NO",
                    f"HTTP {st}",
                )

                # ------------------------------------------------------------------ 2. REST snapshot + history


def check_snapshot(cfg, H, ins):
    head("2. PRICE LADDER SNAPSHOT (REST)")
    acct = cfg["OANDA_ACCOUNT_ID"]
    st, d = get_json(
        f"{cfg['OANDA_API_URL']}/v3/accounts/{acct}/pricing", H, {"instruments": ins}
    )
    if st != 200 or not d.get("prices"):
        verdict("Price ladder snapshot", "NO", f"HTTP {st}")
        return
    p = d["prices"][0]
    b, a = p.get("bids", []), p.get("asks", [])
    say(
        f"  tradeable={p.get('tradeable')} status={p.get('status')} levels bid/ask={len(b)}/{len(a)}"
    )
    for i, (x, y) in enumerate(zip(b, a), 1):
        say(
            f"  level {i}: bid {x['price']} x {x.get('liquidity')}   ask {y['price']} x {y.get('liquidity')}"
        )
        REPORT["snapshot"] = {
            "tradeable": p.get("tradeable"),
            "status": p.get("status"),
            "levels": [len(b), len(a)],
            "bid_liq": [x.get("liquidity") for x in b],
            "ask_liq": [x.get("liquidity") for x in a],
        }
        verdict(
            "Price ladder (several levels per side)",
            "YES" if len(b) > 1 else "PARTIAL",
            f"{len(b)} bid / {len(a)} ask levels",
        )


def check_history(cfg, H, ins):
    head("3. HISTORY: 1-MINUTE BID/ASK/MID CANDLES, SECONDS CANDLES")
    url = f"{cfg['OANDA_API_URL']}/v3/instruments/{ins}/candles"
    st, d = get_json(url, H, {"price": "BAM", "granularity": "M1", "count": "5000"})
    if st != 200:
        verdict("Historical bid/ask candles (spread history)", "NO", f"HTTP {st}: {d}")
        return
    rows = []
    for c in d.get("candles", []):
        if not c.get("complete") or "bid" not in c or "ask" not in c or "mid" not in c:
            continue
        t = int(float(c["time"]))
        rows.append(
            (
                t,
                float(c["mid"]["h"]) - float(c["mid"]["l"]),
                float(c["ask"]["c"]) - float(c["bid"]["c"]),
                int(c.get("volume", 0)),
            )
        )
        if not rows:
            verdict(
                "Historical bid/ask candles (spread history)",
                "PARTIAL",
                "response had no bid/ask",
            )
            return
        say(
            f"  {len(rows)} complete 1-minute candles, from {ist(rows[0][0])} to {ist(rows[-1][0])} IST"
        )
        rng, spr, vol = [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows]
        say(
            f"  typical 1-minute range (median) {fnum(med(rng), 3)}   typical spread at close (median) {fnum(med(spr), 3)}   typical tick count {fnum(med(vol), 0)}"
        )
        if med(rng):
            say(
                f"  spread / typical 1-minute range = {med(spr) / med(rng):.2f}   (cost hurdle: the closer to 1, the harder a 1-minute edge is)"
            )
            hours = {}
            for t, r, s, v in rows:
                hours.setdefault(((t + 19800) // 3600) % 24, []).append((r, s, v))
                say(
                    "\n  IST hour | candles | median range | median spread | median ticks"
                )
                for h in sorted(hours):
                    x = hours[h]
                    say(
                        f"   {h:02d}:00   | {len(x):7d} | {med([i[0] for i in x]):12.3f} | {med([i[1] for i in x]):13.3f} | {med([i[2] for i in x]):10.0f}"
                    )
                    REPORT["history"] = {
                        "candles": len(rows),
                        "from_ist": ist(rows[0][0]),
                        "to_ist": ist(rows[-1][0]),
                        "median_range": med(rng),
                        "median_spread": med(spr),
                        "median_ticks": med(vol),
                        "by_ist_hour": {
                            str(h): {
                                "n": len(x),
                                "range": med([i[0] for i in x]),
                                "spread": med([i[1] for i in x]),
                                "ticks": med([i[2] for i in x]),
                            }
                            for h, x in hours.items()
                        },
                    }
                    verdict(
                        "Historical bid/ask candles (spread history by minute)",
                        "YES",
                        f"{len(rows)} minutes, so spread by time of day is available without storing ticks",
                    )
                    st, d = get_json(
                        url, H, {"price": "M", "granularity": "S5", "count": "500"}
                    )
                    if st == 200 and d.get("candles"):
                        cs = [c for c in d["candles"] if c.get("complete")]
                        verdict(
                            "5-second candles (finer history)",
                            "YES",
                            f"{len(cs)} returned, covering {ist(int(float(cs[0]['time'])))} to {ist(int(float(cs[-1]['time'])))} IST",
                        )
                    else:
                        verdict("5-second candles (finer history)", "NO", f"HTTP {st}")


def check_books(cfg, H, ins):
    head("4. OANDA ORDER BOOK / POSITION BOOK")
    for name in ("orderBook", "positionBook"):
        st, d = get_json(f"{cfg['OANDA_API_URL']}/v3/instruments/{ins}/{name}", H)
        if st == 200 and isinstance(d, dict) and name in d:
            b = d[name]
            buckets = b.get("buckets", [])
            t = int(parse_time(b["time"])) if b.get("time") else None
            say(
                f"  {name}: {len(buckets)} price buckets, bucket width {b.get('bucketWidth')}, snapshot {ist(t) if t else '?'} IST"
            )
            age = f"{(time.time() - t) / 60:.0f} min old" if t else "age unknown"
            verdict(
                f"OANDA {name} (its clients' orders/positions)",
                "YES",
                f"{len(buckets)} buckets, {age}; slow snapshot, not tick-level",
            )
        else:
            verdict(
                f"OANDA {name} (its clients' orders/positions)",
                "NO",
                f"HTTP {st}: {str(d)[:120]}",
            )

            # ------------------------------------------------------------------ 5. live stream


def read_stream(cfg, H, ins, seconds):
    head(f"5. LIVE STREAM ({seconds}s) - this is the important part")
    url = (
        f"{cfg['OANDA_STREAM_URL']}/v3/accounts/{cfg['OANDA_ACCOUNT_ID']}/pricing/stream?"
        + urllib.parse.urlencode({"instruments": ins})
    )
    ticks, beats, keys, closed_msgs = [], [], Counter(), 0
    req = urllib.request.Request(url, headers=H)
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        verdict(
            "Live price stream",
            "NO",
            f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:150]}",
        )
        return ticks, beats, keys, False
    except Exception as e:
        verdict("Live price stream", "NO", f"{type(e).__name__}: {e}")
        return ticks, beats, keys, False
    say(f"  connected at {ist(t0)} IST (now streaming; leave it running)")
    last_print = t0
    try:
        for raw in resp:
            now = time.time()
            if now - t0 > seconds:
                break
            line = raw.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            keys.update(m.keys())
            if m.get("type") == "HEARTBEAT":
                beats.append(now)
            elif m.get("type") == "PRICE":
                b, a = m.get("bids", []), m.get("asks", [])
                if not b or not a:
                    continue
                tradeable = bool(m.get("tradeable", True))
                if not tradeable:
                    closed_msgs += 1
                    ticks.append(
                        {
                            "t": float(m["time"]),
                            "r": now,
                            "bid": float(b[0]["price"]),
                            "ask": float(a[0]["price"]),
                            "bl": [(float(x["price"]), x.get("liquidity")) for x in b],
                            "al": [(float(x["price"]), x.get("liquidity")) for x in a],
                            "ok": tradeable,
                            "status": m.get("status"),
                        }
                    )
                    if now - last_print >= 30:
                        last_print = now
                        say(
                            f"  ... {int(now - t0)}s: {len(ticks)} prices, {len(beats)} heartbeats"
                        )
                        if now - t0 > 25 and not any(t["ok"] for t in ticks):
                            say(
                                "  No tradeable prices after 25s: the market looks CLOSED. Stopping early."
                            )
                            break
    except Exception as e:
        say(f"  stream ended: {type(e).__name__}: {e}")
    finally:
        try:
            resp.close()
        except Exception:
            pass
        return ticks, beats, keys, True


def analyse_ticks(ticks, beats, keys):
    live = [t for t in ticks if t["ok"]]
    verdict(
        "Market state flag per price (tradeable/status)",
        "YES" if "type" in keys else "PARTIAL",
        f"{len(live)} tradeable, {len(ticks) - len(live)} non-tradeable messages; statuses {sorted(set(str(t['status']) for t in ticks))}",
    )
    if len(live) < 20:
        verdict(
            "Live bid/ask ticks",
            "NO",
            f"only {len(live)} tradeable prices received (market closed? run again when it is open)",
        )
        return live
    dur = live[-1]["t"] - live[0]["t"]
    gaps = [live[i]["t"] - live[i - 1]["t"] for i in range(1, len(live))]
    per_sec = Counter(int(t["t"]) for t in live)
    say(
        f"  {len(live)} ticks over {dur:.0f}s = {len(live) / max(dur, 1):.2f} ticks/s; busiest second {max(per_sec.values())} ticks"
    )
    say(
        f"  gap between ticks: median {pct(gaps, 50):.2f}s  p90 {pct(gaps, 90):.2f}s  p99 {pct(gaps, 99):.2f}s  max {max(gaps):.1f}s"
    )
    if len(beats) > 1:
        hb = [beats[i] - beats[i - 1] for i in range(1, len(beats))]
        say(f"  heartbeats every {med(hb):.1f}s (median)")
        verdict(
            "Live bid/ask ticks",
            "YES",
            f"{len(live) / max(dur, 1):.2f} ticks/s, median gap {pct(gaps, 50):.2f}s, p99 gap {pct(gaps, 99):.1f}s",
        )
        REPORT["ticks"] = {
            "n": len(live),
            "seconds": dur,
            "per_second": len(live) / max(dur, 1),
            "gap_p50": pct(gaps, 50),
            "gap_p90": pct(gaps, 90),
            "gap_p99": pct(gaps, 99),
            "gap_max": max(gaps),
        }

        lat = [(t["r"] - t["t"]) * 1000 for t in live]
        say(
            f"  receive time minus OANDA tick time: median {med(lat):.0f} ms, min {min(lat):.0f}, max {max(lat):.0f} (includes your clock error)"
        )
        verdict(
            "Tick timestamps / delivery latency measurable",
            "YES" if -2000 < med(lat) < 5000 else "PARTIAL",
            f"median {med(lat):.0f} ms; if negative or huge, your PC clock is off (sync it in Windows settings)",
        )

        sp = [t["ask"] - t["bid"] for t in live]
        say(
            f"  spread: min {min(sp):.3f}  median {med(sp):.3f}  p95 {pct(sp, 95):.3f}  max {max(sp):.3f}; {len(set(round(s, 4) for s in sp))} distinct values"
        )
        verdict(
            "Spread on every tick",
            "YES",
            f"median {med(sp):.3f}, p95 {pct(sp, 95):.3f}, max {max(sp):.3f}",
        )
        REPORT["spread"] = {
            "min": min(sp),
            "median": med(sp),
            "p95": pct(sp, 95),
            "max": max(sp),
        }

        cls = Counter()
        for i in range(1, len(live)):
            db, da = (
                live[i]["bid"] - live[i - 1]["bid"],
                live[i]["ask"] - live[i - 1]["ask"],
            )
            sb = 0 if abs(db) < EPS else (1 if db > 0 else -1)
            sa = 0 if abs(da) < EPS else (1 if da > 0 else -1)
            cls[
                {
                    (0, 0): "nothing moved (sizes only)",
                    (1, 0): "bid up only",
                    (-1, 0): "bid down only",
                    (0, 1): "ask up only",
                    (0, -1): "ask down only",
                    (1, 1): "both up",
                    (-1, -1): "both down",
                    (1, -1): "bid up, ask down (spread tightens)",
                    (-1, 1): "bid down, ask up (spread widens)",
                }[(sb, sa)]
            ] += 1
            say("\n  what changes from one tick to the next:")
            for k, v in cls.most_common():
                say(f"    {k:<38} {v:>6}  ({100 * v / (len(live) - 1):.1f}%)")
                REPORT["move_types"] = dict(cls)
                verdict(
                    "Bid-only vs ask-only moves",
                    "YES",
                    "; ".join(
                        f"{k}: {100 * v / (len(live) - 1):.0f}%"
                        for k, v in cls.most_common(4)
                    ),
                )

                shapes = Counter((len(t["bl"]), len(t["al"])) for t in live)
                nlev = max(len(t["bl"]) for t in live)
                bl, al = [set() for _ in range(nlev)], [set() for _ in range(nlev)]
                same, imb = 0, set()
                for t in live:
                    for i, (p, l) in enumerate(t["bl"]):
                        bl[i].add(l)
                        for i, (p, l) in enumerate(t["al"]):
                            al[i].add(l)
                            if (
                                t["bl"][0][1] is not None
                                and t["bl"][0][1] == t["al"][0][1]
                            ):
                                same += 1
                                if (
                                    t["bl"][0][1] is not None
                                    and t["al"][0][1] is not None
                                ):
                                    tot = t["bl"][0][1] + t["al"][0][1]
                                    imb.add(
                                        round((t["bl"][0][1] - t["al"][0][1]) / tot, 3)
                                        if tot
                                        else 0
                                    )
                                    say(f"\n  price levels per side: {dict(shapes)}")
                                    for i in range(nlev):
                                        say(
                                            f"    level {i + 1}: distinct bid sizes {sorted(bl[i])[:6]}   distinct ask sizes {sorted(al[i])[:6]}"
                                        )
                                        offs = {
                                            tuple(
                                                round(t["bl"][0][0] - p, 3)
                                                for p, _ in t["bl"]
                                            )
                                            for t in live
                                        } | {
                                            tuple(
                                                round(p - t["al"][0][0], 3)
                                                for p, _ in t["al"]
                                            )
                                            for t in live
                                        }
                                        ratios = [
                                            (t["al"][-1][0] - t["bl"][-1][0])
                                            / (t["al"][0][0] - t["bl"][0][0])
                                            for t in live
                                            if t["al"][0][0] > t["bl"][0][0]
                                            and len(t["al"]) > 1
                                        ]
                                        say(
                                            f"  best-level bid size equals ask size on {100 * same / len(live):.0f}% of ticks; distinct (bid-ask)/(sum) values: {sorted(imb)[:8]}"
                                        )
                                        say(
                                            f"  price offsets between levels (distinct patterns): {len(offs)}   deep/best spread ratio: min {fnum(min(ratios), 2) if ratios else '-'} max {fnum(max(ratios), 2) if ratios else '-'}"
                                        )
                                        informative = len(imb) > 3
                                        verdict(
                                            "Quoted-size imbalance (bid size vs ask size)",
                                            "YES" if informative else "NO",
                                            f"{len(imb)} distinct values over {len(live)} ticks -> "
                                            + (
                                                "varies, worth testing"
                                                if informative
                                                else "constant/near-constant: carries no information"
                                            ),
                                        )
                                        verdict(
                                            "Ladder depth/skew (cost of size)",
                                            "NO" if len(offs) <= 2 else "PARTIAL",
                                            f"{len(offs)} offset pattern(s); "
                                            + (
                                                "the level offsets never change, so the deep/best spread ratio only reflects the best spread (no extra information)"
                                                if len(offs) <= 2
                                                else "offsets vary, worth testing"
                                            ),
                                        )
                                        REPORT["ladder"] = {
                                            "shapes": {
                                                str(k): v for k, v in shapes.items()
                                            },
                                            "distinct_imbalance_values": len(imb),
                                            "offset_patterns": len(offs),
                                        }

                                        verdict(
                                            "Executed trades / buy-sell volume / aggressor side",
                                            "NO",
                                            "message fields seen: "
                                            + ", ".join(sorted(keys))
                                            + " (no trade or volume field)",
                                        )

                                        small = sum(1 for g in gaps if g < 0.25)
                                        say(
                                            f"\n  stream rate check: smallest gap {min(gaps):.3f}s, {100 * small / len(gaps):.1f}% of gaps under 0.25s, busiest second {max(per_sec.values())} ticks"
                                        )
                                        REPORT["stream_rate"] = {
                                            "min_gap": min(gaps),
                                            "share_gaps_under_250ms": small / len(gaps),
                                            "busiest_second": max(per_sec.values()),
                                        }
                                        times = [t["t"] for t in live]
                                        say(
                                            "\n  rolling-window coverage (share of windows containing at least N ticks):"
                                        )
                                        cover = {}
                                        for w in (1, 5, 15, 30, 60):
                                            ends = [
                                                times[0] + w + i
                                                for i in range(int(max(0, dur - w)))
                                            ]
                                            if not ends:
                                                continue
                                            counts = [
                                                bisect.bisect_right(times, e)
                                                - bisect.bisect_left(times, e - w)
                                                for e in ends
                                            ]
                                            c = {
                                                n: 100
                                                * sum(1 for x in counts if x >= n)
                                                / len(counts)
                                                for n in (1, 3, 10)
                                            }
                                            cover[w] = c
                                            say(
                                                f"    {w:>3}s window: >=1 tick {c[1]:5.1f}%   >=3 ticks {c[3]:5.1f}%   >=10 ticks {c[10]:5.1f}%"
                                            )
                                            REPORT["window_coverage"] = {
                                                str(k): v for k, v in cover.items()
                                            }
                                            ok5 = cover.get(5, {}).get(3, 0)
                                            verdict(
                                                "Rolling windows (5s/15s/30s/60s) are statistically usable",
                                                "YES" if ok5 > 70 else "PARTIAL",
                                                f"5-second windows hold >=3 ticks {ok5:.0f}% of the time",
                                            )
                                            return live

                                        # ------------------------------------------------------------------ 6. candles from ticks
                                        COLS = [
                                            "minute_ist",
                                            "open",
                                            "high",
                                            "low",
                                            "close",
                                            "range",
                                            "tick_count",
                                            "direction",
                                            "body_pct",
                                            "net_move",
                                            "momentum",
                                            "pct_return",
                                            "max_jump",
                                            "jump_share",
                                            "max_run_up",
                                            "max_run_down",
                                            "max_pullback_from_high",
                                            "max_bounce_from_low",
                                            "imbalance",
                                            "move_imbalance",
                                            "reversals",
                                            "avg_spread",
                                            "max_spread",
                                            "spread_close",
                                        ]


def build_minutes(live):
    mins, prev, direction, run = {}, None, 0, 0
    for t in live:
        mid = round((t["bid"] + t["ask"]) / 2, 8)
        k = int(t["t"] // 60)
        m = mins.get(k)
        if m is None:
            m = mins[k] = {
                "o": mid,
                "h": mid,
                "l": mid,
                "c": mid,
                "n": 0,
                "up": 0,
                "dn": 0,
                "mu": 0.0,
                "md": 0.0,
                "ju": 0.0,
                "jd": 0.0,
                "ru": 0,
                "rd": 0,
                "rev": 0,
                "pb": 0.0,
                "bn": 0.0,
                "sp": [],
                "start": prev if prev is not None else mid,
                "bo": t["bid"],
                "bh": t["bid"],
                "bl": t["bid"],
                "ao": t["ask"],
                "ah": t["ask"],
                "al": t["ask"],
            }
            m["h"], m["l"], m["c"] = max(m["h"], mid), min(m["l"], mid), mid
            m["bh"], m["bl"], m["bc"] = (
                max(m["bh"], t["bid"]),
                min(m["bl"], t["bid"]),
                t["bid"],
            )
            m["ah"], m["al"], m["ac"] = (
                max(m["ah"], t["ask"]),
                min(m["al"], t["ask"]),
                t["ask"],
            )
            m["n"] += 1
            m["sp"].append(t["ask"] - t["bid"])
            m["pb"] = max(m["pb"], m["h"] - mid)  # pullback from the running high
            m["bn"] = max(m["bn"], mid - m["l"])  # bounce from the running low
            if prev is not None:
                d = mid - prev
                if d > EPS:
                    m["up"] += 1
                    m["mu"] += d
                    m["ju"] = max(m["ju"], d)
                    run = run + 1 if direction == 1 else 1
                    if direction == -1:
                        m["rev"] += 1
                    direction = 1
                    m["ru"] = max(m["ru"], run)
                elif d < -EPS:
                    m["dn"] += 1
                    m["md"] += -d
                    m["jd"] = max(m["jd"], -d)
                    run = run + 1 if direction == -1 else 1
                    if direction == 1:
                        m["rev"] += 1
                    direction = -1
                    m["rd"] = max(m["rd"], run)
                    prev = mid
                    return mins


def candle_rows(mins, first_t, last_t):
    rows = []
    for k in sorted(mins):
        if k * 60 < first_t or (k + 1) * 60 > last_t:
            continue  # partial minute at the start or end of the sample
        m = mins[k]
        rng, net = m["h"] - m["l"], m["c"] - m["o"]
        ud, mv = m["up"] + m["dn"], m["mu"] + m["md"]
        jump = m["ju"] if m["ju"] >= m["jd"] else -m["jd"]
        body = abs(net) / rng * 100 if rng > EPS else 0.0
        rows.append(
            {
                "minute_ist": ist(k * 60),
                "open": m["o"],
                "high": m["h"],
                "low": m["l"],
                "close": m["c"],
                "range": rng,
                "tick_count": m["n"],
                "direction": (
                    "BULLISH" if net > EPS else "BEARISH" if net < -EPS else "FLAT"
                ),
                "body_pct": body,
                "net_move": net,
                "momentum": m["c"] - m["start"],
                "pct_return": (m["c"] / m["start"] - 1) * 100 if m["start"] else 0.0,
                "max_jump": jump,
                "jump_share": abs(jump) / rng if rng > EPS else 0.0,
                "max_run_up": m["ru"],
                "max_run_down": m["rd"],
                "max_pullback_from_high": m["pb"],
                "max_bounce_from_low": m["bn"],
                "imbalance": (m["up"] - m["dn"]) / ud if ud else 0.0,
                "move_imbalance": (m["mu"] - m["md"]) / mv if mv > EPS else 0.0,
                "reversals": m["rev"],
                "avg_spread": sum(m["sp"]) / len(m["sp"]),
                "max_spread": max(m["sp"]),
                "spread_close": m["ac"] - m["bc"],
                "_k": k,
                "_m": m,
            }
        )
        return rows


def compare_with_oanda(cfg, H, ins, rows):
    head("6. DO OUR TICK-BUILT CANDLES MATCH OANDA'S OWN CANDLES?")
    if len(rows) < 2:
        verdict(
            "Tick-built 1-minute candles match OANDA candles",
            "PARTIAL",
            "fewer than 2 complete minutes in the sample; run with --seconds 300 or more",
        )
        return
    st, d = get_json(
        f"{cfg['OANDA_API_URL']}/v3/instruments/{ins}/candles",
        H,
        {
            "price": "BAM",
            "granularity": "M1",
            "from": str(rows[0]["_k"] * 60),
            "to": str((rows[-1]["_k"] + 1) * 60),
        },
    )
    if st != 200:
        verdict(
            "Tick-built 1-minute candles match OANDA candles",
            "PARTIAL",
            f"could not fetch OANDA candles: HTTP {st}",
        )
        return
    theirs = {
        int(float(c["time"])) // 60: c
        for c in d.get("candles", [])
        if c.get("complete") and "mid" in c
    }
    diffs = {"o": [], "h": [], "l": [], "c": []}
    signed = {"h": [], "l": []}
    vol, bad = [], 0
    for r in rows:
        c = theirs.get(r["_k"])
        if not c:
            bad += 1
            continue
        for key, ours in (
            ("o", r["open"]),
            ("h", r["high"]),
            ("l", r["low"]),
            ("c", r["close"]),
        ):
            diffs[key].append(abs(ours - float(c["mid"][key])))
            signed["h"].append(
                r["high"] - float(c["mid"]["h"])
            )  # negative = our high is lower than OANDA's
            signed["l"].append(
                r["low"] - float(c["mid"]["l"])
            )  # positive = our low is higher than OANDA's
            if int(c.get("volume", 0)):
                vol.append(r["tick_count"] / int(c["volume"]))
                n = len(diffs["o"])
                if not n:
                    verdict(
                        "Tick-built 1-minute candles match OANDA candles",
                        "PARTIAL",
                        "no matching minutes returned by OANDA",
                    )
                    return
                for key, name in (
                    ("o", "open"),
                    ("h", "high"),
                    ("l", "low"),
                    ("c", "close"),
                ):
                    say(
                        f"  {name:<6} max difference {max(diffs[key]):.4f}   exact matches {100 * sum(1 for x in diffs[key] if x < 1e-6) / n:.0f}%"
                    )
                    say(
                        f"  our tick count / OANDA volume: median {fnum(med(vol), 2)} (1.00 means volume is exactly the number of price updates)"
                    )
                    say(
                        f"  our high minus OANDA's high: median {fnum(med(signed['h']), 3)} (negative = we miss peaks); our low minus OANDA's low: median {fnum(med(signed['l']), 3)} (positive = we miss troughs)"
                    )
                    say(
                        f"  stream coverage per minute (our ticks / OANDA volume): min {fnum(min(vol), 2)}  median {fnum(med(vol), 2)}  max {fnum(max(vol), 2)}"
                        if vol
                        else ""
                    )
                    if vol and med(vol) < 0.8:
                        say(
                            "  => the stream delivers only part of OANDA's price updates, so tick-based metrics see a thinned price path."
                        )
                        worst = max(max(v) for v in diffs.values())
                        REPORT["candle_match"] = {
                            "minutes": n,
                            "max_diff": worst,
                            "volume_ratio_median": med(vol),
                        }
                        verdict(
                            "Tick-built 1-minute candles match OANDA candles",
                            "YES" if worst < 0.05 else "PARTIAL",
                            f"{n} minutes compared, largest difference {worst:.4f}",
                        )
                        verdict(
                            "OANDA candle volume = count of price updates",
                            "YES" if vol and 0.9 < med(vol) < 1.1 else "PARTIAL",
                            f"ratio {fnum(med(vol), 2)}",
                        )


def show_candles(rows):
    head(
        "7. THE TABLE THE NEW SCREEN WILL SHOW (1-minute candles built from this live sample)"
    )
    if not rows:
        say("  (no complete minute yet: run longer, e.g. --seconds 300)")
        return
    say(
        f"  {'minute IST':<21}{'dir':<9}{'body%':>6}{'range':>8}{'ticks':>6}{'jump':>8}{'jshare':>7}{'runU':>5}{'runD':>5}{'pullHi':>8}{'bounceLo':>9}{'imb':>6}{'mimb':>6}{'spr':>6}"
    )
    for r in rows[-12:]:
        say(
            f"  {r['minute_ist']:<21}{r['direction']:<9}{r['body_pct']:>6.0f}{r['range']:>8.3f}{r['tick_count']:>6}{r['max_jump']:>8.3f}{r['jump_share']:>7.2f}"
            f"{r['max_run_up']:>5}{r['max_run_down']:>5}{r['max_pullback_from_high']:>8.3f}{r['max_bounce_from_low']:>9.3f}{r['imbalance']:>6.2f}{r['move_imbalance']:>6.2f}{r['avg_spread']:>6.2f}"
        )
        rg = [r["range"] for r in rows]
        verdict(
            "All planned 1-minute columns can be computed on the live feed",
            "YES",
            f"{len(rows)} minutes built; median range {med(rg):.3f}, median spread {med([r['avg_spread'] for r in rows]):.3f}",
        )
        if med(rg):
            say(
                f"  spread / median 1-minute range in this sample: {med([r['avg_spread'] for r in rows]) / med(rg):.2f}"
            )

            # ------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description="Probe the live OANDA feed")
    ap.add_argument("--instrument", default="XAU_USD")
    ap.add_argument(
        "--seconds",
        type=int,
        default=300,
        help="how long to watch the live stream (default 300)",
    )
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()
    cfg = load_cfg()
    if not cfg.get("OANDA_API_KEY") or not cfg.get("OANDA_ACCOUNT_ID"):
        sys.exit("OANDA_API_KEY / OANDA_ACCOUNT_ID not found in .env")
        H = {
            "Authorization": f"Bearer {cfg['OANDA_API_KEY']}",
            "Accept-Datetime-Format": "UNIX",
        }
        ins = args.instrument.upper()
        now = time.time()
        head("FEED PROBE")
        say(
            f"  instrument {ins}   now {ist(now)} IST   ({datetime.fromtimestamp(now, tz=timezone.utc):%Y-%m-%d %H:%M:%S} UTC)"
        )
        say(
            f"  sessions active now (my reading of your IST windows): {', '.join(active_sessions(now)) or 'none'}"
        )

        ok = check_account(cfg, H)
        steps = (
            [
                ("instrument", lambda: check_instrument(cfg, H, ins)),
                ("snapshot", lambda: check_snapshot(cfg, H, ins)),
                ("history", lambda: check_history(cfg, H, ins)),
                ("books", lambda: check_books(cfg, H, ins)),
            ]
            if ok
            else []
        )
        for name, fn in steps:
            try:
                fn()
            except Exception as e:
                verdict(f"step {name}", "NO", f"crashed: {type(e).__name__}: {e}")

                live, rows = [], []
                if ok:
                    try:
                        ticks, beats, keys, connected = read_stream(
                            cfg, H, ins, args.seconds
                        )
                        if connected:
                            live = analyse_ticks(ticks, beats, keys)
                            if len(live) >= 20:
                                mins = build_minutes(live)
                                rows = candle_rows(mins, live[0]["t"], live[-1]["t"])
                                compare_with_oanda(cfg, H, ins, rows)
                                show_candles(rows)
                    except Exception as e:
                        verdict(
                            "live stream analysis",
                            "NO",
                            f"crashed: {type(e).__name__}: {e}",
                        )

                        head("VERDICT SUMMARY")
                        for f, s, e in VERDICTS:
                            say(f"  [{s:<7}] {f}")
                            outdir = args.outdir or (
                                os.path.join(
                                    os.path.dirname(os.path.abspath(__file__)),
                                    "..",
                                    "data",
                                )
                                if os.path.isdir(
                                    os.path.join(
                                        os.path.dirname(os.path.abspath(__file__)),
                                        "..",
                                        "data",
                                    )
                                )
                                else "."
                            )
                            os.makedirs(outdir, exist_ok=True)
                            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                            REPORT.update(
                                {
                                    "run_utc": stamp,
                                    "instrument": ins,
                                    "verdicts": [
                                        {"feature": f, "status": s, "evidence": e}
                                        for f, s, e in VERDICTS
                                    ],
                                }
                            )
                            jp = os.path.join(outdir, f"feed_probe_{stamp}.json")
                            with open(jp, "w", encoding="utf-8") as f:
                                json.dump(REPORT, f, indent=1, default=str)
                                files = [jp]
                                if live:
                                    tp = os.path.join(
                                        outdir, f"feed_probe_ticks_{stamp}.csv"
                                    )
                                    with open(
                                        tp, "w", newline="", encoding="utf-8"
                                    ) as f:
                                        w = csv.writer(f)
                                        w.writerow(
                                            [
                                                "tick_time_ist",
                                                "bid",
                                                "ask",
                                                "mid",
                                                "spread",
                                                "bid_size_1",
                                                "ask_size_1",
                                            ]
                                        )
                                        for t in live:
                                            w.writerow(
                                                [
                                                    ist(t["t"])
                                                    + f"{t['t'] % 1:.3f}"[1:],
                                                    t["bid"],
                                                    t["ask"],
                                                    round((t["bid"] + t["ask"]) / 2, 6),
                                                    round(t["ask"] - t["bid"], 6),
                                                    t["bl"][0][1],
                                                    t["al"][0][1],
                                                ]
                                            )
                                            files.append(tp)
                                            if rows:
                                                cp = os.path.join(
                                                    outdir,
                                                    f"feed_probe_candles_{stamp}.csv",
                                                )
                                                with open(
                                                    cp,
                                                    "w",
                                                    newline="",
                                                    encoding="utf-8",
                                                ) as f:
                                                    w = csv.writer(f)
                                                    w.writerow(COLS)
                                                    for r in rows:
                                                        w.writerow(
                                                            [
                                                                (
                                                                    round(r[c], 6)
                                                                    if isinstance(
                                                                        r[c], float
                                                                    )
                                                                    else r[c]
                                                                )
                                                                for c in COLS
                                                            ]
                                                        )
                                                        files.append(cp)
                                                        say(
                                                            "\nSaved:\n  "
                                                            + "\n  ".join(
                                                                os.path.abspath(p)
                                                                for p in files
                                                            )
                                                        )
                                                        say(
                                                            "\nSend me the .json file (or paste the VERDICT SUMMARY above). It contains no keys."
                                                        )


if __name__ == "__main__":
    main()
