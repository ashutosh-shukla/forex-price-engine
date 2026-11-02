# Metrics dictionary

All metrics are computed **per tick** inside the ingestor and stored with every 1-minute candle.
Every other timeframe (2m ... 1d) is rebuilt from the stored 1m rows by Oracle SQL, so
they always agree with each other. Prices are **mid** = (bid + ask) / 2.

A "tick" here is one price update from OANDA. Moves smaller than 1e-9 count as "no change".

## Raw (additive) fields

| Field | Meaning |
|---|---|
| `tick_count` (`volume`) | Price updates received. Volume proxy, not traded volume. |
| `up_ticks` / `down_ticks` | Updates where mid went up / down versus the previous update. |
| `move_up` / `move_down` | Sum of all upward / downward mid moves (both positive numbers). |
| `path_length` | `move_up + move_down`: total distance the price travelled. |
| `reversals` | Number of direction changes (up→down or down→up). |
| `max_run_up` / `max_run_down` | Longest streak of consecutive up / down ticks. |
| `max_jump_up` / `max_jump_down` | Largest single-tick move in each direction (positive numbers). |
| `spread_sum`, `min_spread`, `max_spread` | Spread statistics (`avg_spread = spread_sum / tick_count`). |
| `bid_close`, `ask_close` | Last bid / ask of the candle. |

## Derived fields (`metrics=full`; a subset with `metrics=basic`)

| Field | Formula | Reading it |
|---|---|---|
| `imbalance` *(basic)* | `(up_ticks − down_ticks) / (up_ticks + down_ticks)` | −1 all selling ticks, +1 all buying ticks |
| `move_imbalance` *(basic)* | `(move_up − move_down) / (move_up + move_down)` | Same, weighted by size: one big drop outweighs many small ups |
| `efficiency` *(basic)* | `\|close − start_mid\| / path_length` | ~1 clean move, ~0 heavy oscillation |
| `close_location` *(basic)* | `(close − low) / (high − low)` | 0 closed at the low, 1 at the high |
| `avg_spread` *(basic)* | `spread_sum / tick_count` | Liquidity cost |
| `pressure` *(basic)* | label from `pressure_score` | `buying` > 0.2, `selling` < −0.2, else `balanced` (a heuristic, not a fact) |
| `pressure_score` | `0.5·imbalance + 0.5·move_imbalance` | −1 … +1 |
| `max_jump`, `jump_dir` | Larger of the two jumps, signed | Your "102 → 97" event |
| `jump_share` | `max single move / (high − low)` | How much of the range came from one tick |
| `choppiness` | `reversals / (up_ticks + down_ticks)` | Oscillation rate |
| `up_down_ratio` | `up_ticks / down_ticks` | |
| `tick_velocity` | `tick_count / seconds in candle` | Ticks per second (activity) |
| `realized_vol` | `sqrt(sum of squared tick moves)` | Price-unit volatility |
| `range`, `net_move`, `direction` | `high − low`, `close − open`, up/down/flat | |
| `body_ratio`, `wick_upper`, `wick_lower` | candle shape | |
| `avg_mid` | `mid_sum / tick_count` | |

## Accuracy notes (please read)

* **Carry-over rule.** The move from the last tick of the previous candle to the first tick of a new
  candle is counted in the *new* candle. That is what makes higher timeframes exact sums of 1m
  candles (a unit test proves it). `efficiency` therefore uses `start_mid` (the last tick before
  the candle) instead of `open`.
* **Runs and reversals** are exact inside the engine's live candles. For higher timeframes built from 1m
  rows, `max_run_*` is the maximum of the 1m maxima, and a run that spans a 1m boundary is counted in the
  later minute. Treat them as very close approximations.
* **Backfilled history** (downloaded from OANDA) has OHLC and OANDA's tick count only. Metric fields are
  `null` and `minutes_present` / `metric_minutes` tell you how many of the minutes in a candle have full metrics.
* **Volume is a proxy.** Spot FX has no central tape, so direction is inferred from quote changes. A jump can
  be liquidity repricing rather than aggressive sellers; `avg_spread` / `max_spread` help tell them apart.
* Buckets are aligned to **UTC** (4h at 00/04/08/12/16/20, 1d at UTC midnight).
