"""Position lifecycle: entry sizing, initial SL, progressive SL/break-even,
30M roadblock partial close, 4H final TP (spec sections 15-24).

Simplification made explicit: the spec's "5 positions -> close 4, leave
1" (section 23) is implemented as one Position per entry signal (initial
or continuation), whose volume is itself partially closed 80/20 at the
roadblock -- this is mathematically identical to opening N equal-sized
tickets and closing 80% of the ticket count, since section 23 itself says
the 80/20 split should be "based on total lot volume, not merely ticket
count." Each entry (initial and every continuation) tracks its OWN
roadblock and final-TP zone independently, because each has its own entry
time and the spec defines the roadblock as "the next confirmed opposite
zone formed AFTER entry" -- which necessarily differs per entry.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .candles import Candle
from .config import StrategyConfig
from .position import Position, _position_id_counter
from .risk import RiskManager, max_lots_by_margin, max_lots_for_budget, risk_per_lot
from .signals import EntrySignal
from .zones import Direction, Zone, ZoneManager


@dataclass
class TradeManager:
    config: StrategyConfig
    risk_manager: RiskManager
    positions: list[Position] = None
    closed_trade_log: list[dict] = None

    def __post_init__(self):
        self.positions = self.positions or []
        self.closed_trade_log = self.closed_trade_log or []

    # ---- entry ----

    def try_open(
        self, signal: EntrySignal, timestamp: pd.Timestamp, entry_price: float, equity: float
    ) -> Position | None:
        if self.risk_manager.trading_halted_today:
            return None
        instrument = self.config.instrument
        setup_candle = signal.setup_5m_zone.setup_candle  # spec 15/16/17: the RED/GREEN "X" candle
        buffer_pips = self.config.initial_sl_buffer_pips_min
        buffer_price = buffer_pips * instrument.pip_size
        if signal.direction is Direction.BUY:
            sl = setup_candle.low - buffer_price
        else:
            sl = setup_candle.high + buffer_price
        sl_distance = abs(entry_price - sl)
        if sl_distance <= 0:
            return None

        budget = self.risk_manager.available_risk_budget(equity)
        lots_from_risk = max_lots_for_budget(instrument, sl_distance, budget)
        lots_from_margin = max_lots_by_margin(
            instrument, entry_price, equity, self.config.max_margin_usage_percent
        )
        lots = instrument.round_lots(min(lots_from_risk, lots_from_margin))
        if lots < instrument.min_lot:
            return None

        risk_amount = instrument.pnl(lots, sl_distance)
        position = Position(
            id=next(_position_id_counter),
            setup_id=signal.parent_4h_zone_id,
            direction=signal.direction,
            entry_price=entry_price,
            initial_sl=sl,
            lots=lots,
            opened_at=timestamp,
            magic_number=self.config.magic_number,
            initial_risk=risk_amount,
        )
        self.risk_manager.register_open_risk(f"pos-{position.id}", risk_amount)
        self.positions.append(position)
        return position

    # ---- per-bar management ----

    def on_5m_bar(
        self,
        timestamp: pd.Timestamp,
        bar: Candle,
        zm_30m: ZoneManager,
        zm_4h: ZoneManager,
        spread_price: float = 0.0,
    ) -> None:
        instrument = self.config.instrument
        for pos in list(self.positions):
            if pos.closed or pos.remaining_lots <= 0:
                continue

            self._apply_progressive_sl(pos, bar, instrument)

            if self._sl_hit(pos, bar):
                fill = pos.sl
                self._close(pos, timestamp, fill, pos.remaining_lots, "sl", instrument)
                continue

            self._maybe_roadblock_partial(pos, timestamp, bar, zm_30m, instrument)
            if pos.closed:
                continue
            self._maybe_final_tp(pos, timestamp, bar, zm_4h, instrument)

    def _apply_progressive_sl(self, pos: Position, bar: Candle, instrument) -> None:
        favorable_price = bar.high if pos.direction is Direction.BUY else bar.low
        profit_pips = self._price_to_pips(pos, favorable_price, instrument)
        if profit_pips <= 0:
            return
        distance_pips = self.config.sl_distance_pips(
            profit_pips, initial_distance_pips=self._initial_distance_pips(pos, instrument)
        )
        distance_price = distance_pips * instrument.pip_size
        if pos.direction is Direction.BUY:
            candidate = pos.entry_price - distance_price
            pos.sl = max(pos.sl, candidate)
        else:
            candidate = pos.entry_price + distance_price
            pos.sl = min(pos.sl, candidate)
        self.risk_manager.update_open_risk(f"pos-{pos.id}", pos.current_risk(instrument))

    @staticmethod
    def _initial_distance_pips(pos: Position, instrument) -> float:
        return abs(pos.entry_price - pos.initial_sl) / instrument.pip_size

    @staticmethod
    def _price_to_pips(pos: Position, price: float, instrument) -> float:
        diff = price - pos.entry_price if pos.direction is Direction.BUY else pos.entry_price - price
        return diff / instrument.pip_size

    @staticmethod
    def _sl_hit(pos: Position, bar: Candle) -> bool:
        if pos.direction is Direction.BUY:
            return bar.low <= pos.sl
        return bar.high >= pos.sl

    def _maybe_roadblock_partial(
        self, pos: Position, timestamp: pd.Timestamp, bar: Candle, zm_30m: ZoneManager, instrument
    ) -> None:
        if pos.roadblock_partial_done:
            return
        roadblock = self._find_roadblock_zone(pos, zm_30m)
        if roadblock is None:
            return
        level = roadblock.low if pos.direction is Direction.BUY else roadblock.high
        touched = bar.low <= level <= bar.high
        if not touched:
            return
        close_lots = round(pos.lots * (self.config.roadblock_close_percent / 100.0), 8)
        close_lots = min(close_lots, pos.remaining_lots)
        self._close(pos, timestamp, level, close_lots, "roadblock_partial", instrument, partial=True)
        pos.roadblock_partial_done = True

    def _maybe_final_tp(
        self, pos: Position, timestamp: pd.Timestamp, bar: Candle, zm_4h: ZoneManager, instrument
    ) -> None:
        if not pos.roadblock_partial_done or pos.remaining_lots <= 0:
            return
        final_zone = self._find_final_tp_zone(pos, zm_4h)
        if final_zone is None:
            return
        level = final_zone.low if pos.direction is Direction.BUY else final_zone.high
        touched = bar.low <= level <= bar.high
        if not touched:
            return
        self._close(pos, timestamp, level, pos.remaining_lots, "final_tp", instrument)

    def _find_roadblock_zone(self, pos: Position, zm_30m: ZoneManager) -> Zone | None:
        opposite = pos.direction.opposite
        candidates = [
            z
            for z in zm_30m.zones
            if z.direction is opposite and z.confirm_timestamp > pos.opened_at
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda z: z.confirm_timestamp)

    def _find_final_tp_zone(self, pos: Position, zm_4h: ZoneManager) -> Zone | None:
        opposite = pos.direction.opposite
        candidates = [
            z
            for z in zm_4h.zones
            if z.direction is opposite and z.confirm_timestamp > pos.opened_at
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda z: z.confirm_timestamp)

    def _close(
        self,
        pos: Position,
        timestamp: pd.Timestamp,
        price: float,
        lots: float,
        reason: str,
        instrument,
        partial: bool = False,
    ) -> None:
        pnl = pos.realize_close(instrument, price, lots)
        if pos.closed:
            self.risk_manager.release_risk(f"pos-{pos.id}")
            pos.closed_at = timestamp
            pos.close_reason = reason
        else:
            self.risk_manager.update_open_risk(f"pos-{pos.id}", pos.current_risk(instrument))
        self.closed_trade_log.append(
            {
                "position_id": pos.id,
                "setup_id": pos.setup_id,
                "direction": pos.direction.value,
                "timestamp": timestamp,
                "price": price,
                "lots": lots,
                "pnl": pnl,
                "reason": reason,
                "partial": partial,
            }
        )

    def total_floating_pnl(self, instrument, price_lookup) -> float:
        total = 0.0
        for pos in self.positions:
            if pos.closed:
                continue
            price = price_lookup(pos.direction)
            total += pos.floating_pnl(instrument, price)
        return total

    def total_realized_pnl_since(self, since_ts: pd.Timestamp) -> float:
        return sum(
            t["pnl"] for t in self.closed_trade_log if t["timestamp"] >= since_ts
        )
