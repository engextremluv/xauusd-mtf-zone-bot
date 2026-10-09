"""Configuration: the broker/instrument contract spec, and every rule of
the H4 Three-Candle Pullback strategy exposed as a parameter rather than
hard-coded.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InstrumentSpec:
    """Broker contract specification for the traded symbol.

    Defaults are typical retail XAUUSD CFD terms (100oz/lot, 0.01 tick).
    These MUST be replaced with the real broker's symbol specification
    before results are trusted -- risk sizing is only as correct as
    these numbers.
    """

    symbol: str = "XAUUSD"
    contract_size: float = 100.0  # troy ounces per 1.0 lot
    tick_size: float = 0.01  # smallest price increment ("point")
    tick_value_per_lot: float = 1.0  # account-currency P&L per tick per 1.0 lot
    pip_size: float = 0.10  # 1 "pip" = 10 ticks for XAUUSD on most MT5 brokers
    min_lot: float = 0.01
    max_lot: float = 50.0
    lot_step: float = 0.01

    def value_per_price_unit(self, lots: float) -> float:
        """Account-currency P&L for a 1.0 price-unit move at the given lot size."""
        ticks_per_unit = 1.0 / self.tick_size
        return lots * ticks_per_unit * self.tick_value_per_lot

    def pnl(self, lots: float, price_distance: float) -> float:
        """Account-currency P&L for moving `price_distance` at `lots` size."""
        return self.value_per_price_unit(lots) * price_distance

    def round_lots(self, lots: float) -> float:
        """Round DOWN to the nearest lot step at/above min_lot; below
        min_lot there is no valid size, so this returns 0.0 (never rounds
        a too-small size UP to min_lot -- that would silently open a
        bigger position than the caller asked for).
        """
        import math

        if lots < self.min_lot:
            return 0.0
        steps = math.floor((lots - self.min_lot) / self.lot_step + 1e-9) + 1
        rounded = self.min_lot + (steps - 1) * self.lot_step
        rounded = max(self.min_lot, min(self.max_lot, rounded))
        # avoid float noise, e.g. 0.049999999
        decimals = max(0, -int(round(math.log10(self.lot_step))))
        return round(rounded, decimals)


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
