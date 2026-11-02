# API reference

Base URL (local): `http://127.0.0.1:8000`. All times are epoch **milliseconds** (UTC).
Every call below needs the header `X-API-Key: pek_<id>.<secret>`.
Responses are JSON. Errors: `401` bad key, `403` not permitted, `404` unknown pair, `429` rate limit.

## Public (no key)
| Endpoint | Purpose |
|---|---|
| `GET /health/live` | Process is up |
| `GET /health/ready` | Database, engine and stream status (503 if the database is down) |

## Market data
| Endpoint | Purpose |
|---|---|
| `GET /v1/pairs` | Active pairs, supported timeframes, live timeframes |
| `GET /v1/price/latest?symbols=EUR_USD,GBP_USD` | Latest bid/ask/mid |
| `GET /v1/candles?symbol=EUR_USD&tf=5m&limit=500&metrics=none\|basic\|full` | Latest N candles, oldest first |
| `GET /v1/candles?symbol=…&tf=…&from=<ms>&to=<ms>&limit=…` | Range. With `from`, pages forward: use `next_from` for the next page |
| `GET /v1/candles/forming?symbol=EUR_USD&tf=5m&metrics=full` | The unfinished candle (live timeframes only) |
| `GET /v1/metrics/summary?symbol=EUR_USD&tf=5m&window=20` | Order-flow summary over the last N candles |
| `GET /v1/ticks?symbol=EUR_USD&from=&to=&limit=` | Raw ticks `[t, bid, ask]` (needs `allow_ticks` and `STORE_TICKS=true`; kept 2 days by default) |
| `GET /v1/status` | Feed health per pair (age of last tick, stream connected) |

Timeframes: `1m 2m 3m 5m 10m 15m 30m 1h 2h 3h 4h 6h 8h 12h 1d`. Every timeframe above 1m is computed by Oracle SQL from stored 1m candles
(short responses are cached for 1 second, history for 60 seconds).
Each candle: `t, open, high, low, close, volume, complete` (+ metrics, see METRICS.md).

## Websocket tokens and live stream
1. **Your backend** calls `POST /v1/ws-token` (optional body `{"symbols":["EUR_USD"],"timeframes":["1m","5m"],"metrics":true,"ttl_seconds":60}`)
   and returns `token` to the browser. The API key never reaches the browser.
2. The browser connects to `wss://<engine-host>/v1/stream` and sends within 5 seconds:
   `{"op":"auth","token":"<token>"}`
3. Then subscribe (as many times as needed):
```json
{"op":"subscribe","channel":"ticks","symbols":["EUR_USD","GBP_USD"]}
{"op":"subscribe","channel":"candles","symbols":["EUR_USD"],"tfs":["1m","5m"]}
{"op":"subscribe","channel":"candle_metrics","symbols":["EUR_USD"],"tfs":["5m"]}
{"op":"unsubscribe","channel":"ticks","symbols":["GBP_USD"]}
{"op":"ping"}
```
Each subscription is answered by the latest snapshot immediately, then live updates.

Server messages:
```json
{"e":"tick","symbol":"EUR_USD","t":1760000000123,"bid":1.0843,"ask":1.08432,"mid":1.08431,"p":1760000000125}
{"e":"candle","symbol":"EUR_USD","tf":"5m","t":1760000000000,"open":..,"high":..,"low":..,"close":..,"volume":123,"final":false}
{"e":"candle_metrics", "...same fields plus every metric...", "final":true}
{"e":"error","code":"forbidden","message":"..."}
```
`final:true` marks the closed candle. `t` on ticks is OANDA's timestamp; `p` is when the engine published it
(subtract it from your receive time to measure delivery latency).
Forming candles are pushed at most every `CANDLE_PUBLISH_INTERVAL_MS` (250 ms) and always on close.

Close codes: `4401` auth failed, `4403` forbidden/origin, `4408` session time limit (reconnect with a new token), `1013` capacity/limit.

## Admin (header `X-Admin-Key`, keep private)
| Endpoint | Purpose |
|---|---|
| `POST /v1/admin/clients` | Create a client; returns the API key **once** |
| `GET /v1/admin/clients` | List clients |
| `PATCH /v1/admin/clients/{id}` | Change permissions, limits, IP list, disable |
| `POST /v1/admin/clients/{id}/rotate-key` | New key; the old one stops working |
| `GET/POST /v1/admin/pairs`, `PATCH /v1/admin/pairs/{symbol}` | List / add / (de)activate pairs |
| `POST /v1/admin/backfill` `{"symbol":"EUR_USD","days":7}` | Download history now |
| `POST /v1/admin/shutdown` | Graceful stop (used by stop.bat) |

Example, from your website's backend:
```python
r = httpx.get("http://127.0.0.1:8000/v1/candles", params={"symbol": "EUR_USD", "tf": "5m", "limit": 300, "metrics": "basic"},
              headers={"X-API-Key": ENGINE_KEY})
```
