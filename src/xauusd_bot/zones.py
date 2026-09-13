"""Generic candle-zone state machine, used identically at the 4H, 30M and
5M timeframes (spec sections 3-14, 27).

Two explicit assumptions fill gaps the spec leaves open (flagged loudly
because the user asked for this piece to be tested "extremely
carefully" -- verify these interpretations against your own manual
chart reading before trusting results):

1. **Pending-candidate invalidation.** Section 5 says an unconfirmed
   setup candle X is tracked "until it is either confirmed, or
   invalidated according to the zone rules" but never states the
   invalidation rule for a *pending* (not-yet-confirmed) candidate. This
   module invalidates a pending BUY candidate (red X) the moment any
   later candle *closes* below X.low before ever confirming upward --
   i.e. price broke down through the candidate before it had a chance to
   flip up. Mirror rule for pending SELL candidates (green X): invalidated
   if a later candle closes above X.high first.

2. **Multiple concurrent pending candidates.** Every candle of the
   "wrong" color for the current trend also opens a brand new pending
   candidate of its own (a red candle is simultaneously (a) a possible
   confirmation of an older pending SELL candidate, and (b) a fresh
   pending BUY candidate itself). This is what lets multiple historical
   zones exist concurrently (spec section 9) instead of only ever
   tracking the single most recent candle.

Zone *direction reversal* (section 8) is implemented literally: a
confirmed zone's price range is unchanged, only its `direction` flips,
when a later candle's close breaks fully through the opposite boundary.
Per section 31, only fully closed candles are ever passed to this state
machine -- see engine.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from itertools import count

import pandas as pd

from .candles import Candle, closes_beyond, is_buy_confirmation, is_sell_confirmation

_zone_id_counter = count(1)


class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> "Direction":
        return Direction.SELL if self is Direction.BUY else Direction.BUY


@dataclass
class Zone:
    id: int
    timeframe: str
    direction: Direction
    high: float
    low: float
    setup_candle: Candle
    setup_timestamp: pd.Timestamp
    confirm_timestamp: pd.Timestamp
    confirmed: bool = True
    invalidated: bool = False
    retested: bool = False
    reversal_count: int = 0
    # bookkeeping used by higher-level orchestration (spec 27)
    child_confirmation_generated: bool = False
    trade_taken: bool = False

    def overlaps(self, candle: Candle) -> bool:
        """"Price returns to the zone" test (spec 10/13): does this
        candle's range intersect the zone's [low, high] band at all.
        """
        return candle.low <= self.high and candle.high >= self.low

    def mark_retested(self) -> None:
        self.retested = True

    def maybe_reverse(self, candle: Candle) -> bool:
        """Apply spec section 8. Returns True if this zone just flipped
        direction because of `candle`.
        """
        if self.invalidated:
            return False
        if self.direction is Direction.BUY and closes_beyond(candle, self.low, "down"):
            self.direction = Direction.SELL
            self.reversal_count += 1
            self._reset_child_state()
            return True
        if self.direction is Direction.SELL and closes_beyond(candle, self.high, "up"):
            self.direction = Direction.BUY
            self.reversal_count += 1
            self._reset_child_state()
            return True
        return False

    def _reset_child_state(self) -> None:
        # a reversed zone is effectively "new" in its new direction
        self.retested = False
        self.child_confirmation_generated = False
        self.trade_taken = False


@dataclass
class _PendingCandidate:
    direction: Direction
    setup_candle: Candle
    setup_timestamp: pd.Timestamp


@dataclass
class ZoneManager:
    """Runs the setup->confirmation->zone pipeline for one timeframe.

    Feed fully-closed candles in chronological order via `on_new_candle`.
    Confirmed zones accumulate in `.zones` (oldest first) and are never
    removed, only marked `invalidated`, so historical zones (spec 9/27)
    stay inspectable. Use `.active_zones()` for the ones still relevant.
    """

    timeframe: str
    max_history: int = 500
    zones: list[Zone] = field(default_factory=list)
    _pending: list[_PendingCandidate] = field(default_factory=list)
    _last_candle: Candle | None = None

    def active_zones(self, direction: Direction | None = None) -> list[Zone]:
        zs = [z for z in self.zones if not z.invalidated]
        if direction is not None:
            zs = [z for z in zs if z.direction is direction]
        return zs

    def on_new_candle(self, timestamp: pd.Timestamp, candle: Candle) -> list[Zone]:
        """Process one fully-closed candle. Returns any zones newly
        confirmed on this candle (empty list if none).
        """
        newly_confirmed: list[Zone] = []

        # 1) direction-reversal check against existing confirmed zones
        for zone in self.active_zones():
            zone.maybe_reverse(candle)

        # 2) try to confirm/invalidate pending candidates using this candle
        still_pending: list[_PendingCandidate] = []
        for cand in self._pending:
            if cand.direction is Direction.BUY:
                if is_buy_confirmation(cand.setup_candle, candle):
                    zone = self._confirm(cand, timestamp, candle)
                    newly_confirmed.append(zone)
                    continue
                if closes_beyond(candle, cand.setup_candle.low, "down"):
                    continue  # invalidated: drop silently
                still_pending.append(cand)
            else:
                if is_sell_confirmation(cand.setup_candle, candle):
                    zone = self._confirm(cand, timestamp, candle)
                    newly_confirmed.append(zone)
                    continue
                if closes_beyond(candle, cand.setup_candle.high, "up"):
                    continue  # invalidated: drop silently
                still_pending.append(cand)
        self._pending = still_pending

        # 3) this candle itself becomes a new pending candidate of the
        #    opposite color/direction
        if candle.is_bearish:
            self._pending.append(
                _PendingCandidate(Direction.BUY, setup_candle=candle, setup_timestamp=timestamp)
            )
        elif candle.is_bullish:
            self._pending.append(
                _PendingCandidate(Direction.SELL, setup_candle=candle, setup_timestamp=timestamp)
            )
        # a doji (open == close) starts no new candidate; it also cannot
        # itself satisfy is_buy_confirmation/is_sell_confirmation since
        # those require strict bullish/bearish confirm candles.

        # 4) retest bookkeeping against ALL active zones, using this candle
        for zone in self.active_zones():
            if not zone.retested and zone.overlaps(candle):
                zone.mark_retested()

        self._trim_history()
        self._last_candle = candle
        return newly_confirmed

    def _confirm(
        self, cand: _PendingCandidate, timestamp: pd.Timestamp, confirm_candle: Candle
    ) -> Zone:
        zone = Zone(
            id=next(_zone_id_counter),
            timeframe=self.timeframe,
            direction=cand.direction,
            high=cand.setup_candle.high,
            low=cand.setup_candle.low,
            setup_candle=cand.setup_candle,
            setup_timestamp=cand.setup_timestamp,
            confirm_timestamp=timestamp,
        )
        self.zones.append(zone)
        return zone

    def _trim_history(self) -> None:
        if len(self.zones) > self.max_history:
            self.zones = self.zones[-self.max_history :]
