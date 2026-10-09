"""H4 Three-Candle Pullback strategy: a separate, much simpler strategy
from the Daily/4H/30M/5M candle-zone system in the rest of this package.

Rules (from the source strategy book):
  1. Direction: Daily 50 EMA on close. Yesterday's completed daily candle
     above the EMA -> BUY mode for the day; below -> SELL mode.
  2. Setup: on the H4 chart, 3 consecutive closed candles against the
     mode (red in BUY mode, green in SELL mode). A doji breaks the run.
  3. Entry: market order at the open of the next H4 candle.
  4. Stop = 2 x ATR(14) on H4 (as of the candle that just closed).
     Take profit = same distance, opposite side (1:1).
  5. Close after 200 hours (~8 days) if neither level is hit.
  6. Only one trade open at a time.

See engine.py for the backtest implementation and config.py for every
rule above exposed as a configurable parameter.
"""
