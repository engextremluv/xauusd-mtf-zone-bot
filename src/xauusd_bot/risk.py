"""Position sizing. The rule this module exists to get right: risk in
dollars is derived from the *actual* stop distance and the instrument's
real contract/tick specification -- never from an assumed "N lots = $X
risk" shortcut.
"""
from __future__ import annotations

from .config import InstrumentSpec


def risk_per_lot(instrument: InstrumentSpec, sl_distance_price: float) -> float:
    """Dollar loss for exactly 1.0 lot if price moves `sl_distance_price`
    (an absolute, positive price distance) against the position.
    """
    if sl_distance_price <= 0:
        raise ValueError("sl_distance_price must be positive")
    return instrument.pnl(lots=1.0, price_distance=sl_distance_price)


def max_lots_for_budget(
    instrument: InstrumentSpec, sl_distance_price: float, risk_budget: float
) -> float:
    """Largest lot size (rounded down to the broker's lot step) whose
    monetary loss at the given SL distance does not exceed risk_budget.
    """
    if risk_budget <= 0 or sl_distance_price <= 0:
        return 0.0
    per_lot = risk_per_lot(instrument, sl_distance_price)
    raw_lots = risk_budget / per_lot
    if raw_lots < instrument.min_lot:
        return 0.0
    # round DOWN to the lot step so we never exceed the budget
    import math

    steps = math.floor((raw_lots - instrument.min_lot) / instrument.lot_step + 1e-9)
    lots = instrument.min_lot + steps * instrument.lot_step
    lots = min(lots, instrument.max_lot)
    decimals = max(0, -int(round(math.log10(instrument.lot_step))))
    lots = round(lots, decimals)
    # guard against rounding pushing us back over budget
    while lots > 0 and instrument.pnl(lots, sl_distance_price) > risk_budget + 1e-9:
        lots = round(lots - instrument.lot_step, decimals)
    return max(lots, 0.0)
