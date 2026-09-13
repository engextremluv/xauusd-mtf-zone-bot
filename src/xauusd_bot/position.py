"""A single simulated position/ticket and its lifecycle bookkeeping.

One "trade setup" (spec workflow, sections 28/29) can spawn several
Position objects (spec 19: "one valid setup can open multiple
positions"); they share a `setup_id` so the trade manager can apply the
roadblock 80/20 partial close and final TP across all of them together,
by aggregate lot volume (spec 23: "based on total lot volume, not merely
ticket count").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

import pandas as pd

from .zones import Direction

_position_id_counter = count(1)


@dataclass
class Position:
    id: int
    setup_id: int
    direction: Direction
    entry_price: float
    initial_sl: float
    lots: float
    opened_at: pd.Timestamp
    magic_number: int
    initial_risk: float  # $ risk registered with the RiskManager at entry

    sl: float = field(init=False)
    closed_lots: float = 0.0
    realized_pnl: float = 0.0
    roadblock_partial_done: bool = False
    closed: bool = False
    closed_at: pd.Timestamp | None = None
    close_reason: str | None = None

    def __post_init__(self) -> None:
        self.sl = self.initial_sl

    @property
    def remaining_lots(self) -> float:
        return round(self.lots - self.closed_lots, 8)

    def sl_distance_price(self) -> float:
        return abs(self.entry_price - self.sl)

    def current_risk(self, instrument) -> float:
        """Dollar risk still outstanding on the open remainder at the
        CURRENT sl (spec 30: "recalculate risk whenever additional
        positions are considered").
        """
        if self.remaining_lots <= 0:
            return 0.0
        dist = self.sl_distance_price()
        if dist <= 0:
            return 0.0  # SL at/through entry: no further downside on this leg
        return instrument.pnl(self.remaining_lots, dist)

    def floating_pnl(self, instrument, current_price: float) -> float:
        if self.remaining_lots <= 0:
            return 0.0
        signed_distance = (
            current_price - self.entry_price
            if self.direction is Direction.BUY
            else self.entry_price - current_price
        )
        return instrument.pnl(self.remaining_lots, signed_distance)

    def realize_close(self, instrument, price: float, lots_to_close: float) -> float:
        lots_to_close = min(lots_to_close, self.remaining_lots)
        if lots_to_close <= 0:
            return 0.0
        signed_distance = (
            price - self.entry_price
            if self.direction is Direction.BUY
            else self.entry_price - price
        )
        pnl = instrument.pnl(lots_to_close, signed_distance)
        self.closed_lots = round(self.closed_lots + lots_to_close, 8)
        self.realized_pnl += pnl
        if self.remaining_lots <= 1e-9:
            self.closed = True
        return pnl
