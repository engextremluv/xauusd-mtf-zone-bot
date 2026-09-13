# XAUUSD Multi-Timeframe Candle-Zone Backtester

A Python backtesting engine for the multi-timeframe candle-zone strategy
described in the project's `Master Trading Strategy Specification` (Daily
structure → 4H zone → 30M confirmation → 5M confirmation → entry, with
progressive stop-loss, a 30M "roadblock" partial close, and a 4H final
target). This is a **backtester**, not a live MT5 Expert Advisor -- it
exists to test whether the rules in the spec have a positive expectancy
before anyone considers building the real EA or trading real money.

> **This is not financial advice and this strategy has not been shown to
> be profitable.** A backtest, even a correct one, is not proof a
> strategy will make money live. Read the whole "Assumptions and gaps"
> section below before trusting any number this tool prints.

## What's implemented

| Spec section | Module |
|---|---|
| 2 — Daily market structure (HH/HL, LH/LL, range) | `market_structure.py` |
| 3-14, 27 — 4H/30M/5M candle zones, retest, reversal, history | `zones.py` |
| 10, 13, 15/16, 20, 28/29 — cascading confirmation + entries | `signals.py` |
| 15-24 — initial SL, progressive SL/break-even, roadblock, final TP | `trade_manager.py`, `position.py` |
| 18, 19, 25 — position sizing, aggregate risk cap, daily drawdown halt | `risk.py` |
| 32 — exact-OHLC confirmation rules | `candles.py` |
| whole engine | `engine.py` (bar-by-bar), `timeframes.py` (multi-TF resampling) |

## Setup

```bash
pip install -r requirements.txt
pip install -e .          # installs the `xauusd-bot` CLI command
pytest                     # run the test suite (30+ tests, no network needed)
```

## Getting historical data

Base data is 5-minute OHLC; 30M/4H/Daily are derived from it (see
"Why derive timeframes" below), so you only ever need to fetch 5-minute
bars.

```bash
xauusd-bot fetch-data --start 2022-01-01 --end 2024-01-01
```

This pulls from **Dukascopy's free historical data feed** (no API key,
no signup) via the `dukascopy-python` package, and caches the result as
parquet under `.cache/xauusd_bot/`.

**Important:** this was developed and tested inside a sandboxed
environment whose network egress policy blocks arbitrary external hosts
(`freeserv.dukascopy.com` included) — so the live fetch itself could not
be exercised end-to-end there. The request logic was verified to be
correctly formed (package installs, calls build the right URL/params),
and the rest of the pipeline was validated against synthetic + local CSV
data instead. **Run `fetch-data` on your own machine first and sanity
check the output** (row count, date range, a few known price levels)
before trusting a backtest against it.

If Dukascopy ever doesn't work for you, you can supply your own 5-minute
OHLC as a CSV instead (columns: `timestamp,open,high,low,close`, any
pandas-parseable timestamp format, UTC recommended):

```bash
xauusd-bot run-backtest --csv path/to/your_data.csv --equity 10000
```

## Running a backtest

```bash
xauusd-bot run-backtest --start 2022-01-01 --end 2024-01-01 --equity 10000 --out results/
```

Prints summary stats (trade count, win rate, profit factor, max
drawdown, final equity) and, with `--out`, writes `trades.csv` and
`equity_curve.csv`.

All EA-style inputs (risk %, SL ladder, roadblock split, etc.) live in
`StrategyConfig` (`src/xauusd_bot/config.py`) — edit defaults there or
construct your own `StrategyConfig(...)` in a script instead of the CLI
for anything beyond the basics.

## Assumptions and gaps (read this before trusting results)

The spec is unusually precise for a trading document, but a few
mechanics are genuinely underspecified. Each is implemented with a
documented, configurable assumption — **verify these against your own
manual chart reading before trusting backtest output**, per the spec's
own closing recommendation:

- **Swing high/low detection** (`market_structure.py`): the spec defines
  what HH/HL vs LH/LL *means* but not how to detect a swing point. This
  uses a standard N-bar fractal (`swing_lookback` / `swing_lookahead_confirm`
  in config, default 2/2).
- **Pending-zone invalidation** (`zones.py`): the spec says an unconfirmed
  setup candle is tracked "until confirmed or invalidated" but doesn't
  define invalidation for the *unconfirmed* case. Implemented as: a
  candidate dies the moment a later candle closes through its far side
  before ever confirming the other way.
- **Zone direction reversal** (`zones.py`, spec section 8): implemented
  as the zone's price range staying fixed while only its direction label
  flips (a "flip zone"), rather than being replaced by a new zone.
- **Roadblock / final-TP price level** (`trade_manager.py`): the "next
  confirmed opposite zone" is used, and the *near edge* of that zone
  (the side price reaches first) is the trigger price.
- **"5 positions → close 4" (spec 23)**: implemented as one position per
  entry signal, itself partially closed 80/20 by volume — mathematically
  equivalent per the spec's own "based on volume, not ticket count" rule,
  without needing to fabricate multiple discrete tickets per signal.
- **Margin sanity cap (not in the spec):** pure $-risk sizing (section 18)
  is mathematically unbounded when a stop-loss happens to sit very close
  to entry — a real occurrence with candle-based stops — which can imply
  enormous, unfundable lot sizes while still nominally "only" risking
  10%. `max_margin_usage_percent` in `StrategyConfig` caps a *new*
  position's own required margin as a fraction of equity. This is a
  backstop, not a full margin simulation — it does not track cumulative
  margin usage across multiple simultaneously open positions.
- **Day/session boundary**: Daily and 4H candles are built from the 5M
  feed anchored to 00:00 UTC (`daily_origin_offset_hours` in the engine
  lets you shift this to match your broker's actual server-time day
  boundary, e.g. many use 17:00 or 22:00 UTC).
- **Fill assumptions**: entries execute at the next bar's close plus a
  configurable spread (`SpreadCommissionModel`); SL/TP fill on intrabar
  touch of the 5-minute bar's high/low, not requiring a close beyond the
  level (a live position's resting stop/limit orders fill on touch, not
  only on candle close — that "close only" rule in the spec is specific
  to *zone confirmation logic*, not order execution).

**On risk sizing specifically** (the piece the spec asks to be tested
"extremely carefully"): position size is always derived from the actual
SL distance and `InstrumentSpec`'s real contract size / tick value / pip
size — never from an assumed "N lots = $X" shortcut. Replace the default
`InstrumentSpec` values with your actual broker's XAUUSD symbol
specification before trusting any position-sizing or P&L number.

**10% simultaneous risk is very aggressive.** It's what the spec asks
for, and it's implemented faithfully, but combined with compounding
(each new position is sized against *current*, not starting, equity) it
can produce dramatic equity swings in a backtest — small early samples
in testing here went from \$10k to well over \$100k in a matter of days
against favorable synthetic data. That is a mathematical property of
aggressive fixed-fractional sizing compounding across many trades, not a
bug — but it's exactly why the spec's own suggested next steps (realistic
spread/commission/slippage, then a demo account, before ever risking real
money) matter more than usual here. Consider testing with a materially
lower `risk_percent` for anything beyond curiosity.

## Why derive 30M/4H/Daily from the 5M feed instead of fetching each
separately

Fetching each timeframe independently risks subtly inconsistent candle
boundaries between feeds (different weekend/holiday handling, different
close-time conventions). Resampling everything from one 5-minute series
guarantees every 30M/4H/Daily candle's close time is also a 5M boundary
in the underlying data -- the same way a real EA builds higher
timeframes off one underlying tick/M1 feed.

## Testing

```bash
pytest -v
```

The suite focuses hardest on the three things most likely to silently
produce wrong results: exact-OHLC confirmation logic (including that an
intrabar wick spike through a level does *not* count, only a candle
*close* does), the zone lifecycle (creation, delayed confirmation,
invalidation, retest, direction reversal, multiple concurrent historical
zones), and risk sizing (exact dollar risk from the real contract spec,
aggregate cap enforcement, daily drawdown halt/reset). `test_engine.py`
is an end-to-end plumbing smoke test on synthetic data.
