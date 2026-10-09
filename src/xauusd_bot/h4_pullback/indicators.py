"""Daily EMA, H4 ATR, and candle color -- the three raw inputs the
strategy's rules are built from.
"""
from __future__ import annotations

import pandas as pd


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, matching MT5's default (recursive,
    seeded by the first value -- pandas' `adjust=False` ewm).
    """
    return close.ewm(span=period, adjust=False).mean()


def compute_atr(ohlc: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range as a plain SIMPLE moving average of True Range.

    This is MT5's native ATR indicator convention -- not the Wilder
    (RMA-smoothed) ATR used by some other platforms/libraries. The two
    diverge by a few percent in practice; which one a given broker's
    chart shows is worth checking before trusting live signals against
    this backtest.
    """
    prev_close = ohlc["close"].shift(1)
    true_range = pd.concat(
        [
            ohlc["high"] - ohlc["low"],
            (ohlc["high"] - prev_close).abs(),
            (ohlc["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window=period, min_periods=period).mean()


def candle_color(open_: float, close: float) -> str:
    if close > open_:
        return "green"
    if close < open_:
        return "red"
    return "doji"
