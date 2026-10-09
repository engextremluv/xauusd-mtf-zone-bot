"""Configurable inputs for the H4 Three-Candle Pullback strategy.

Every number named in the source strategy book is exposed here rather
than hard-coded, so the rules can be tested under different assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import InstrumentSpec


@dataclass
class H4PullbackConfig:
    # --- direction filter ---
    ema_period: int = 50  # Daily EMA period

    # --- setup / entry ---
    pullback_candles: int = 3  # consecutive against-direction H4 candles required
    atr_period: int = 14  # H4 ATR period
    atr_multiplier: float = 2.0  # stop distance = atr_multiplier x ATR(14)
    reward_risk_ratio: float = 1.0  # take-profit distance, as a multiple of the stop distance

    # --- trade management ---
    time_limit_hours: float = 200.0  # close at market if neither level hit by then
    one_trade_at_a_time: bool = True

    # --- risk / sizing ---
    risk_percent: float = 1.0  # % of current equity risked per trade (book recommends 0.5-1%)

    # --- data ---
    daily_origin_offset_hours: float = 0.0  # see timeframes.py for what this shifts

    instrument: InstrumentSpec = field(default_factory=InstrumentSpec)
