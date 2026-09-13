"""Daily market structure classification: bullish / bearish / range.

Spec section 2 defines the *outcome* precisely (HH+HL => bullish, LH+LL
=> bearish, otherwise range) but does not specify how to detect swing
highs/lows in the first place -- that is a genuine gap the spec leaves
to implementation. This module fills it with a standard N-bar fractal:

    bar[i].high is a swing high if it is strictly greater than the highs
    of `lookback` bars before AND `lookahead` bars after it (mirror for
    swing lows on the low).

This is a real, widely-used definition (a "fractal"), but it is an
assumption, not something derivable from the spec text -- treat
`swing_lookback`/`swing_lookahead_confirm` in StrategyConfig as a knob to
validate against your own manual chart reading, per the spec's closing
recommendation to compare the EA's behaviour to your manual
interpretation.

Look-ahead safety: a swing point at bar i is only *confirmed* once
`lookahead` further bars exist after it. Callers must only pass in
candles up to "now" (no future data) so that structure as of a given
backtest timestamp never sees swings that hadn't confirmed yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class MarketStructure(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGE = "range"


@dataclass(frozen=True)
class SwingPoint:
    timestamp: pd.Timestamp
    price: float
    index: int


def detect_swing_highs(df: pd.DataFrame, lookback: int, lookahead: int) -> list[SwingPoint]:
    if lookback < 1 or lookahead < 1:
        return []
    highs = df["high"].to_numpy(dtype=float)
    n = len(highs)
    swings: list[SwingPoint] = []
    for i in range(lookback, n - lookahead):
        window_left = highs[i - lookback : i]
        window_right = highs[i + 1 : i + 1 + lookahead]
        if highs[i] > window_left.max() and highs[i] > window_right.max():
            swings.append(SwingPoint(timestamp=df.index[i], price=highs[i], index=i))
    return swings


def detect_swing_lows(df: pd.DataFrame, lookback: int, lookahead: int) -> list[SwingPoint]:
    if lookback < 1 or lookahead < 1:
        return []
    lows = df["low"].to_numpy(dtype=float)
    n = len(lows)
    swings: list[SwingPoint] = []
    for i in range(lookback, n - lookahead):
        window_left = lows[i - lookback : i]
        window_right = lows[i + 1 : i + 1 + lookahead]
        if lows[i] < window_left.min() and lows[i] < window_right.min():
            swings.append(SwingPoint(timestamp=df.index[i], price=lows[i], index=i))
    return swings


def classify_structure(
    daily_df: pd.DataFrame, lookback: int = 2, lookahead: int = 2
) -> MarketStructure:
    """Classify the Daily market as of the *last row* in daily_df.

    daily_df must contain only candles up to and including "now" -- do
    not pass future data.
    """
    if len(daily_df) < lookback + lookahead + 3:
        return MarketStructure.RANGE

    swing_highs = detect_swing_highs(daily_df, lookback, lookahead)
    swing_lows = detect_swing_lows(daily_df, lookback, lookahead)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return MarketStructure.RANGE

    h1, h2 = swing_highs[-2].price, swing_highs[-1].price
    l1, l2 = swing_lows[-2].price, swing_lows[-1].price

    higher_high = h2 > h1
    higher_low = l2 > l1
    lower_high = h2 < h1
    lower_low = l2 < l1

    if higher_high and higher_low:
        return MarketStructure.BULLISH
    if lower_high and lower_low:
        return MarketStructure.BEARISH
    return MarketStructure.RANGE
