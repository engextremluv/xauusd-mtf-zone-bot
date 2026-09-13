"""Orchestrates the cascading 4H -> 30M -> 5M confirmation flow (spec
sections 10, 13, 15/16, 20, 28/29) on top of three independent
ZoneManager instances (one per timeframe).

Design choice: 30M and 5M zone detection runs as one continuous global
process (a real EA builds these off a single price feed regardless of
which 4H zone anyone is "interested" in), while a `SetupChain` per active
4H zone tracks which 30M zone it is currently waiting on, gating entries
on the retest sequence the spec describes. This also gives continuation
entries (spec 20) for free: after an entry, a chain simply goes back to
waiting for the *next* new 30M zone in the same direction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

import pandas as pd

from .candles import Candle
from .market_structure import MarketStructure, classify_structure
from .zones import Direction, Zone, ZoneManager


class ChainState(Enum):
    WAIT_4H_RETEST = auto()
    WAIT_30M_ZONE = auto()
    WAIT_30M_RETEST = auto()
    WAIT_5M_ZONE = auto()


@dataclass
class EntrySignal:
    direction: Direction
    timestamp: pd.Timestamp
    setup_5m_zone: Zone
    parent_4h_zone_id: int
    is_continuation: bool


@dataclass
class SetupChain:
    zone_4h: Zone
    state: ChainState = ChainState.WAIT_4H_RETEST
    active_30m_zone: Zone | None = None
    consumed_30m_zone_ids: set[int] = field(default_factory=set)
    consumed_5m_zone_ids: set[int] = field(default_factory=set)
    entries_made: int = 0
    last_seen_reversal_count: int = 0


@dataclass
class SignalEngine:
    config: object  # StrategyConfig (kept loosely typed to avoid import cycle)
    zm_4h: ZoneManager = field(default_factory=lambda: ZoneManager(timeframe="4h"))
    zm_30m: ZoneManager = field(default_factory=lambda: ZoneManager(timeframe="30min"))
    zm_5m: ZoneManager = field(default_factory=lambda: ZoneManager(timeframe="5min"))
    structure: MarketStructure = MarketStructure.RANGE
    _chains: dict[int, SetupChain] = field(default_factory=dict)
    _daily_buffer: list = field(default_factory=list)

    # ---- timeframe entry points, called by the backtest engine ----

    def on_daily_close(self, daily_df_up_to_now) -> None:
        self.structure = classify_structure(
            daily_df_up_to_now,
            lookback=self.config.swing_lookback,
            lookahead=self.config.swing_lookahead_confirm,
        )

    def on_4h_close(self, timestamp: pd.Timestamp, candle: Candle) -> list[Zone]:
        newly_confirmed = self.zm_4h.on_new_candle(timestamp, candle)
        for zone in newly_confirmed:
            if self._direction_allowed(zone.direction):
                self._chains[zone.id] = SetupChain(zone_4h=zone)
        for chain in self._chains.values():
            # a direction reversal (spec 8) resets the zone's own retest/
            # confirmation state; mirror that by restarting this chain's
            # flow so it doesn't keep chasing a 30M zone in the old
            # direction.
            if chain.zone_4h.reversal_count != chain.last_seen_reversal_count:
                chain.last_seen_reversal_count = chain.zone_4h.reversal_count
                chain.active_30m_zone = None
                chain.state = ChainState.WAIT_4H_RETEST
            # retest bookkeeping already happened inside zm_4h.on_new_candle;
            # promote any chains whose 4H zone just became retested
            if chain.state is ChainState.WAIT_4H_RETEST and chain.zone_4h.retested:
                chain.state = ChainState.WAIT_30M_ZONE
        return newly_confirmed

    def on_30m_close(self, timestamp: pd.Timestamp, candle: Candle) -> list[Zone]:
        newly_confirmed = self.zm_30m.on_new_candle(timestamp, candle)
        for chain in self._chains.values():
            if chain.zone_4h.invalidated:
                continue
            # "price returns to the 4H zone" (spec 10) is monitored
            # continuously in real trading, not only when a whole new 4H
            # candle happens to overlap it -- so also check retest here,
            # against this finer-grained 30M candle.
            if chain.state is ChainState.WAIT_4H_RETEST and not chain.zone_4h.retested:
                if chain.zone_4h.overlaps(candle):
                    chain.zone_4h.mark_retested()
                    chain.state = ChainState.WAIT_30M_ZONE
            if chain.state == ChainState.WAIT_30M_ZONE:
                match = next(
                    (
                        z
                        for z in newly_confirmed
                        if z.direction is chain.zone_4h.direction
                        and z.id not in chain.consumed_30m_zone_ids
                    ),
                    None,
                )
                if match is not None:
                    chain.active_30m_zone = match
                    chain.state = ChainState.WAIT_30M_RETEST
            if chain.state == ChainState.WAIT_30M_RETEST and chain.active_30m_zone is not None:
                if chain.active_30m_zone.invalidated:
                    chain.active_30m_zone = None
                    chain.state = ChainState.WAIT_30M_ZONE
                elif chain.active_30m_zone.retested:
                    chain.state = ChainState.WAIT_5M_ZONE
        return newly_confirmed

    def on_5m_close(self, timestamp: pd.Timestamp, candle: Candle) -> list[EntrySignal]:
        newly_confirmed = self.zm_5m.on_new_candle(timestamp, candle)
        signals: list[EntrySignal] = []
        for chain in self._chains.values():
            if chain.zone_4h.invalidated or chain.active_30m_zone is None:
                continue
            # "price returns to the 30M zone" (spec 13), checked against
            # this finer-grained 5M candle rather than waiting on the next
            # 30M candle close.
            if chain.state is ChainState.WAIT_30M_RETEST and not chain.active_30m_zone.retested:
                if chain.active_30m_zone.overlaps(candle):
                    chain.active_30m_zone.mark_retested()
                    chain.state = ChainState.WAIT_5M_ZONE
            if chain.state != ChainState.WAIT_5M_ZONE:
                continue
            match = next(
                (
                    z
                    for z in newly_confirmed
                    if z.direction is chain.zone_4h.direction
                    and z.id not in chain.consumed_5m_zone_ids
                ),
                None,
            )
            if match is None:
                continue
            signals.append(
                EntrySignal(
                    direction=chain.zone_4h.direction,
                    timestamp=timestamp,
                    setup_5m_zone=match,
                    parent_4h_zone_id=chain.zone_4h.id,
                    is_continuation=chain.entries_made > 0,
                )
            )
            chain.entries_made += 1
            chain.consumed_5m_zone_ids.add(match.id)
            chain.consumed_30m_zone_ids.add(chain.active_30m_zone.id)
            # continuation: go looking for the NEXT new 30M zone in the
            # same direction (spec section 20)
            if self.config.enable_additional_entries:
                chain.active_30m_zone = None
                chain.state = ChainState.WAIT_30M_ZONE
            else:
                chain.state = ChainState.WAIT_30M_RETEST  # effectively parked; won't re-trigger
        return signals

    def _direction_allowed(self, direction: Direction) -> bool:
        if self.structure is MarketStructure.BULLISH:
            return direction is Direction.BUY
        if self.structure is MarketStructure.BEARISH:
            return direction is Direction.SELL
        # RANGE
        if direction is Direction.BUY:
            return self.config.allow_buy_in_range
        return self.config.allow_sell_in_range

    def prune_chains(self) -> None:
        self._chains = {
            zid: c for zid, c in self._chains.items() if not c.zone_4h.invalidated
        }
