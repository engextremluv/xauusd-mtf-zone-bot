"""Candle-level primitives used by every timeframe.

Spec section 32 ("Precision requirement") reduces the wordy "body breaks
body, body breaks wick, closes above entire range" rules in sections
4/6/11/12/13/14 to exact OHLC comparisons:

    BUY confirmation:  confirm.is_bullish AND confirm.close > setup.high
    SELL confirmation: confirm.is_bearish AND confirm.close < setup.low

This is because a candle's body top/bottom is (max/min(open, close)) and
its wick extends to high/low; requiring the confirming candle's *close*
to be beyond the setup candle's high/low necessarily also clears the
setup candle's body (high >= body top) and the confirming candle's own
body top *is* its close when it's bullish (and body bottom is its close
when bearish) -- so no separate body-vs-body check is needed once the
color and close-vs-wick conditions hold.

Section 31 ("candle closure") requirement: none of these checks may be
evaluated on a still-forming candle. Callers must only pass fully closed
candles into these functions -- see engine.py for how bar boundaries are
detected.
"""
from __future__ import annotations

from typing import NamedTuple


class Candle(NamedTuple):
    open: float
    high: float
    low: float
    close: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)


def is_buy_confirmation(setup: Candle, confirm: Candle) -> bool:
    """Section 4/11/13: red setup candle X, green confirm candle Y.
    Y's body must close above the *entire* range (high/wick) of X.
    """
    return confirm.is_bullish and confirm.close > setup.high


def is_sell_confirmation(setup: Candle, confirm: Candle) -> bool:
    """Section 6/12/14: green setup candle X, red confirm candle Y.
    Y's body must close below the *entire* range (high/wick) of X.
    """
    return confirm.is_bearish and confirm.close < setup.low


def closes_beyond(candle: Candle, level: float, direction: str) -> bool:
    """Generic "closed beyond a price level" check used for zone
    invalidation / direction-reversal (section 8) and roadblock / final
    TP triggers, where only a completed candle's *close* counts -- a
    wick-only spike through the level is explicitly insufficient.
    """
    if direction == "up":
        return candle.close > level
    if direction == "down":
        return candle.close < level
    raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
