# Price Engine (local edition)

One program that streams forex prices from OANDA, builds candles for every timeframe with order-flow metrics,
stores them in **Oracle Autonomous Database**, and serves them through a protected REST API, a low-latency
websocket and a simple trading UI. **No Docker, no Redis.** It runs as a single Python process on your PC.

```
OANDA stream -> candle engine (all timeframes + metrics, in memory) -> websocket clients (live, no network hop)
                                  |
                                  +-> Oracle ADB (1m candles with metrics)  -> REST API (any timeframe, built by SQL)
```

## 1. One-time setup (Windows)

You need **Python 3.10 or newer** (check with `python --version`).

1. **Wallet.** Unzip your Autonomous Database wallet into the `wallet\` folder (same files as in your ETL project:
   `tnsnames.ora`, `ewallet.pem`, `cwallet.sso`, `sqlnet.ora` ...).
2. **Install.** Double-click `setup.bat` (creates `venv`, runs `python -m pip install -r requirements.txt`,
   creates `.env` and generates the three secrets for you). Without the batch file:
   `python -m venv venv`, `venv\Scripts\activate`, `python -m pip install -r requirements.txt`, `python engine_ctl.py init`.
3. **Fill in `.env`** (open it in Notepad):
   - `ORACLE_ADB_USER`, `ORACLE_ADB_PASSWORD`, `ORACLE_ADB_WALLET_PASSWORD`: as in your ETL project.
   - `ORACLE_ADB_DSN`: a TNS alias from `wallet\tnsnames.ora`. For this engine use the **`_tp`** (or `_low`) alias,
     for example `etldb_tp`. Do not use `_high`: it allows very few simultaneous sessions on a free database.
   - `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`: your practice credentials (leave empty to run only the UI/API without live prices).
4. **Create the tables** in Oracle, either way:
   - **Oracle console:** your Autonomous Database -> *Database actions* -> *SQL*, signed in as the same user as in `.env`,
     paste the whole file `sql\oracle_schema.sql`, click **Run Script** (F5). Safe to run twice.
   - **Or from your PC:** double-click `check_db.bat` (tests the connection), then `initdb.bat`.
   The tables are `PE_PAIRS`, `PE_CLIENTS`, `PE_CANDLES_1M`, `PE_TICKS`. They never clash with your other tables.
   (`sql\oracle_drop.sql` deletes all of them if you ever want a clean start.)

## 1b. Updating an existing install (analysis screen)
1. `venv\Scripts\python -m pip install -r requirements.txt` (adds `tzdata`, needed for daylight-saving time zones on Windows).
2. Double-click `initdb.bat` once: it adds three tables (`PE_ANALYSIS`, `PE_ANALYSIS_RUNS`, `PE_NOTES`) and leaves your data alone.
3. Restart with `stop.bat` then `start.bat`. All times everywhere (UI, logs, CSV) are now **IST**.

## 2. Start and stop

| Action | Double-click | Command |
|---|---|---|
| Start in the background | `start.bat` | `python engine_ctl.py start` |
| Graceful stop (saves the in-progress candle) | `stop.bat` | `python engine_ctl.py stop` |
| Is it running? Is the feed healthy? | `status.bat` | `python engine_ctl.py status` |
| Restart | `restart.bat` | `python engine_ctl.py restart` |
| Run with live console output (Ctrl+C stops) | `run.bat` | `python engine_ctl.py run` |

After `start.bat` open **http://127.0.0.1:8000/ui/** (UI) or **http://127.0.0.1:8000/docs** (Swagger).
Logs (plain sentences, no GET/POST lines) are in `logs\`:
`main.log` (milestones), `activity.log` (everything), `error.log` (failures with reasons).
First run: the engine downloads `BACKFILL_DAYS_NEW_PAIR` days of 1-minute history per pair, then streams live.
Forex is closed from Friday ~21:00 UTC to Sunday ~22:00 UTC: no live prices then.

## 2b. The Analysis screen (http://127.0.0.1:8000/ui/analysis.html)
Press **Start analysis** and, from then on, every candle that closes for the chosen pair (1m, 5m, 15m, 30m, 1h) is saved as one row
with every metric. Nothing else in the engine changes. **Stop analysis** ends the run. After a restart or a weekend it stays off until you press Start.
* **Weekend:** spot forex trades Sunday 17:00 to Friday 17:00 New York time. Outside it the stream is paused (no reconnect attempts),
  the screen shows "WEEKEND - market is off" with the reopening time in IST, and Start is disabled. Everything else (tables, labels, notes, CSV) still works.
* **Sessions** (shown on every row, overlaps allowed): Tokyo pre / Tokyo / Tokyo post, London, New York pre / New York. With `SESSION_MODE=local` (default)
  the windows follow the real Tokyo, London and New York clocks, so in IST they move by an hour when London or the US change their clocks
  (they equal your IST windows until 1 November). `SESSION_MODE=fixed` keeps the exact IST times you gave.
* **Columns** (hover a title for its formula; use the group checkboxes to show or hide): candle values, order-flow metrics, rolling windows (5/15/30/60 s),
  z-scores, OANDA's official candle, what happened next, and your own columns (algo direction left blank, label, note).
* **Filled automatically:** OANDA's official candle a few seconds after the close; the next candle's result (and "actual direction"); the price change,
  best rise (MFE) and worst fall (MAE) 5, 15 and 60 minutes after the close (measured from the stream's ticks, for a long entered at the close).
* **Export:** pick a run (or From/To in IST) and download the candles CSV or the notes CSV.
* **1h candles** start at :30 past the hour in IST (India is UTC+5:30); they match OANDA's own hourly candles.

### What the live-feed probe found (26 Oct 2026, XAU_USD)
* The stream delivers about **1/3 of the price updates** OANDA counts in its own candles (OANDA's candle volume is about 3x our tick count), and our
  highs and lows differ from OANDA's by up to ~0.3. So the tick-path metrics (jumps, runs, pullbacks, imbalance) describe the stream's thinned path.
  The analysis rows therefore store OANDA's official OHLC and volume next to ours, plus `coverage` (our ticks / OANDA volume) per candle.
* Bid and ask sizes in the price ladder never differ (100/400/500/1000 on both sides) and the level offsets are fixed, so there is **no order-book imbalance or microprice**
  to compute. There are no trades, so there is **no executed buy/sell volume**.
* Available: 2.8 ticks/s on gold, 5-second windows always hold at least 3 ticks, spread history by minute, delivery latency ~0.15 s.
* Because `tick_count` of live candles (stream) and of downloaded history (OANDA volume) are on different scales, do not mix them when comparing activity.
Run `python scripts\feed_probe.py --seconds 600` any time to repeat these checks.

## 3. The UI (http://127.0.0.1:8000/ui/)
Plain HTML + CSS, with small vanilla JavaScript files (JavaScript is the only way a page can call an API; there are no
libraries and no build step).

| Page | What it shows |
|---|---|
| **Live** | A card per pair: live bid/ask/mid over the websocket, delivery latency, and the forming-candle metrics (imbalance gauge, pressure, efficiency, ticks/s, reversals, biggest jump). |
| **Charts** | Candlestick chart for any of 15 timeframes with tick-volume and imbalance panels, a table with every calculated column (hover a header for its formula), range / paging controls, live updates, the summary endpoint and the forming candle. |
| **Order-flow** | Forming candles of 1m/5m/15m/1h/4h side by side with all metrics, last closed candles with full metrics, and the written formulas. |
| **API console** | System health and per-pair feed age; an explorer that calls every endpoint; a websocket tester; admin tools (create clients/keys, add pairs, download history, stop the engine). |

In development mode the UI gets its keys automatically from the engine (this computer only). Never ship keys to a browser in production.

## 4. Using the API from your other projects
See `docs\API.md` (endpoints, websocket protocol) and `docs\METRICS.md` (every calculation). Create a key per project:
`venv\Scripts\python -m scripts.create_client --name my-website-backend`

## 5. Notes about Oracle Autonomous Database
* **Free tier limits.** The Always Free database has about 20 GB of storage and a small session limit (`DB_POOL_MAX=5`).
  1-minute candles are tiny (7 pairs is a few MB per month), so storage is only a concern if you turn on `STORE_TICKS`
  (raw ticks: millions of rows per day). Ticks are off by default; when on, they are deleted after `TICK_RETENTION_DAYS`.
* **Idle stop.** An Always Free database is stopped by Oracle after a long period of inactivity (check the console if the
  connection suddenly fails after days of not using it).
* **How timeframes work.** Only 1-minute candles are stored. 5m, 15m, 1h, 4h, 1d and the rest are built by a SQL query
  (`MIN ... KEEP (DENSE_RANK FIRST ...)` for open/close, `SUM`/`MAX` for metrics). Responses are cached for 1 s (latest) or 60 s (history).
* **Network.** Every database write goes over the internet to your cloud region, so writes are batched (once per second).

## 6. Tests and load tests
`python -m tests.run_tests` (17 unit tests, no database needed).
With the engine running: `python -m scripts.loadtest_ws --key <api key> --clients 200` and `python -m scripts.loadtest_rest --key <api key>`.

## 7. Troubleshooting
| Symptom | Fix |
|---|---|
| `start.bat` says it stopped right away | It prints the last lines of `logs\console.log` and `logs\error.log`. |
| "Could not connect to the Oracle database" | `check_db.bat`: wallet files in `wallet\`, alias in `ORACLE_ADB_DSN`, passwords in `.env`. |
| "Database tables are missing" | Run `sql\oracle_schema.sql` in Database Actions, or `initdb.bat`. |
| UI says "No API key yet" | Start the engine in development mode (`ENGINE_ENV=development`) and reload. |
| No live prices | Check `OANDA_*` in `.env`, and that the market is open. `logs\main.log` shows the stream state. |
| Charts empty | History downloads on the first connection; wait a minute, or use the API console -> Admin -> Download history. |

## 8. Security
* Keep `.env` and `wallet\` private (the `.gitignore` already excludes them).
* API keys are stored only as hashes; per-client permissions, IP allow-list and rate limit are in the Admin tab.
* **Your earlier reference zip contained a real wallet and passwords. Treat them as exposed and rotate the ADB user password
  and any tokens when convenient.** This project's zip contains no credentials.

## 9. What changes when you deploy to the cloud later
This edition keeps everything in one process, which is ideal for a single machine. To serve many users from several
processes you would add a shared message bus (Redis) behind the same interfaces (`app/core/hub.py`).
