# XAUUSD H4 Three-Candle Pullback Backtester

A Python backtesting engine for the "H4 Three-Candle Pullback" strategy
(from an uploaded strategy book): a simple, fully-specified trend-pullback
system for trading gold.

> **This is not financial advice.** A backtest, even an honest one, is
> not proof a strategy will make money live. Paper-trade first.

## The rules

1. **Direction** — Daily chart, 50 EMA on close. If yesterday's
   completed daily candle closed above the EMA, today is BUY-only;
   below, SELL-only.
2. **Setup** — on the H4 chart, 3 consecutive closed candles against
   the direction (3 red in BUY mode, 3 green in SELL mode). A doji
   (open == close) breaks the run.
3. **Entry** — market order at the open of the next H4 candle.
4. **Stop** = 2 × ATR(14) on H4 (the ATR value as of the candle that
   just completed the 3-candle pattern). **Take profit** = same
   distance, opposite side (1:1 reward:risk).
5. **Time limit** — close at market if neither level is hit within 200
   hours (~8 days).
6. **One trade at a time** — no new entries while a position is open.

## Why this one, and not something else

This strategy was independently re-implemented from scratch and tested
against real 2020-2026 XAUUSD 5-minute bid+ask data — not the source
book's own claimed results. On the 2022–Sept 2026 window the book
itself claims to have tested, with **real bid/ask spread cost modeled
on every entry and exit**:

| | Book's claim | Independent re-test |
|---|---|---|
| Trades | 368 | 335 |
| Win rate | 55.7% | 57.0% |
| Total result | +43.1R | +47.3R |
| Buys | 249 trades, 58.2% win, +42.0R | 223 trades, 60.1% win, +45.1R |
| Sells | 119 trades, 50.4% win, +1.1R | 112 trades, 50.9% win, +2.2R |

That's a close, independently-reproduced match — not a marketing claim
falling apart under scrutiny. Caveats, confirmed by testing outside the
book's own window:

- Tested on 2020-2021 too (outside the book's claimed window): the
  strategy was roughly flat-to-slightly-negative there. This looks like
  a trend-following system that works well in a sustained uptrend
  (which 2022-2026 gold was), not a universally robust edge.
- Almost all the profit comes from BUYS; sells are barely above
  breakeven. In practice this behaves like a long-biased system.
- The edge per trade is small (~0.08-0.12R average) and sensitive to
  real-world costs beyond what's modeled here (wider broker spreads,
  slippage, overnight swap fees on ~31-hour average hold times).

## Implementation notes / assumptions

- **ATR(14)** is a plain simple moving average of True Range (MT5's
  native ATR indicator convention), not the Wilder-smoothed ATR used by
  some other platforms/libraries — these diverge by a few percent.
- **Fills use real bid/ask spread**: a BUY enters at the ASK and exits
  (SL/TP/time-limit) at the BID; a SELL enters at the BID and exits at
  the ASK. Structure (EMA, ATR, the 3-candle pattern) is read off the
  bid chart, same as a trader would see.
- **5-minute fill granularity**, not true minute-level data — a
  reasonable approximation, but if the stop and target are both touched
  within the same 5-minute bar, this counts it as a loss (the same
  conservative convention the source book describes for its own
  minute-level testing).
- **Daily/H4 candles are derived from one 5-minute feed** via
  `timeframes.build_timeframes` rather than fetched separately — this
  guarantees their candle boundaries are mutually consistent (the same
  way a real trading platform builds higher timeframes off one
  underlying tick/M1 feed), not an approximation.
- **Position sizing**: `risk_percent` of *current* equity (compounds)
  divided by the real dollar risk at the stop distance (from
  `InstrumentSpec`'s actual contract size / tick value — never an
  assumed "N lots = $X" shortcut), rounded down to the broker's lot
  step. A signal is silently skipped if the resulting size would round
  below the minimum lot.
- **Day/H4 boundary**: anchored to 00:00 UTC by default
  (`daily_origin_offset_hours` in `H4PullbackConfig` shifts this if your
  broker's server time starts elsewhere).

## Setup

```bash
pip install -r requirements.txt
pip install -e .          # installs the `xauusd-bot` CLI command
pytest                     # run the test suite, no network needed
```

## Getting historical data

Base data is 5-minute bid **and ask** OHLC (needed for realistic spread
costs); Daily/H4 are derived from it.

```bash
xauusd-bot fetch-data --start 2022-01-01 --end 2026-09-01
```

This pulls from **Dukascopy's free historical data feed** (no API key,
no signup) via the `dukascopy-python` package, fetching both sides and
caching the result as parquet under `.cache/xauusd_bot/`.

**Important:** this was developed inside a sandboxed environment whose
network egress policy blocks arbitrary external hosts (including
Dukascopy's), so the live fetch itself could not be exercised
end-to-end there — only validated to build the correct request. **Run
`fetch-data` on your own machine and sanity-check the output** (row
count, date range, a few known price levels, and that `high >= open/close`
and `low <= open/close` hold throughout) before trusting a backtest
against it.

You can also supply your own 5-minute bid+ask CSV instead (columns:
`timestamp,open,high,low,close,ao,ah,al,ac`):

```bash
xauusd-bot run-backtest --csv path/to/your_bidask_data.csv --equity 10000
```

## Running a backtest

```bash
xauusd-bot run-backtest --start 2022-01-01 --end 2026-09-01 --equity 10000 --risk-percent 1.0 --out results/
```

Prints total trades, win rate, total/average R, max drawdown (in R and
%), final equity, and a by-year and by-direction breakdown. With
`--out`, also writes `trades.csv` and `equity_curve.csv`.

Every rule above is a parameter on `H4PullbackConfig`
(`src/xauusd_bot/config.py`) — construct your own config in a script for
anything beyond `--risk-percent`.

## Testing

```bash
pytest -v
```

Covers: EMA/ATR computed against hand-calculated values, the 3-candle
setup detection and next-bar entry timing, bid/ask fill correctness in
both directions (this is where a backtest most commonly cheats itself
by secretly trading at one frictionless price), that a second signal is
correctly blocked while a trade is already open, and position-sizing
edge cases (never rounds a too-small size up to the minimum lot, never
exceeds the risk budget after rounding).
