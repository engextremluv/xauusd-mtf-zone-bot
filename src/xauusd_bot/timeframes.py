"""Build the Daily / 4H / 30M timeframes from a single base 5-minute feed.

Deriving every higher timeframe from one 5M series (rather than fetching
each timeframe independently from the data provider) guarantees their
candle boundaries are mutually consistent -- exactly what a real EA gets
by building higher timeframes off the same underlying tick/M1 feed.

Assumption made explicit here (spec does not state a session boundary):
day and 4H boundaries are anchored to 00:00 UTC. If your broker's server
time / "trading day" starts elsewhere (many use 17:00 or 22:00 UTC), set
`daily_origin_offset_hours` accordingly before trusting Daily-timeframe
structure calls.
"""
from __future__ import annotations

import pandas as pd

OHLC_AGG = {"open": "first", "high": "max", "low": "min", "close": "last"}


def _resample(df: pd.DataFrame, rule: str, origin_offset_hours: float = 0.0) -> pd.DataFrame:
    origin = df.index.min().floor("D") - pd.Timedelta(hours=origin_offset_hours)
    out = df.resample(rule, origin=origin, label="left", closed="left").agg(OHLC_AGG)
    return out.dropna(subset=["open", "high", "low", "close"])


def build_timeframes(
    base_5m: pd.DataFrame, daily_origin_offset_hours: float = 0.0
) -> dict[str, pd.DataFrame]:
    """base_5m: DataFrame indexed by UTC candle-open timestamp with
    columns open/high/low/close, at 5-minute resolution (gaps for
    weekends/holidays are fine).

    Returns {"5min": ..., "30min": ..., "4h": ..., "1D": ...}, each a
    DataFrame with the same OHLC columns, aligned so that every 30min/4h/1D
    candle's close time is also a 5min candle boundary in the input.
    """
    required = {"open", "high", "low", "close"}
    missing = required - set(base_5m.columns)
    if missing:
        raise ValueError(f"base_5m is missing required columns: {missing}")
    if not isinstance(base_5m.index, pd.DatetimeIndex):
        raise ValueError("base_5m must be indexed by a DatetimeIndex")

    base = base_5m.sort_index()
    return {
        "5min": base,
        "30min": _resample(base, "30min"),
        "4h": _resample(base, "4h", origin_offset_hours=daily_origin_offset_hours),
        # "24h" (Tick-like) rather than "1D" (calendar DateOffset): pandas
        # only honors a custom `origin` for Tick-like rules, and we need
        # that to shift the day boundary via daily_origin_offset_hours.
        "1D": _resample(base, "24h", origin_offset_hours=daily_origin_offset_hours),
    }
