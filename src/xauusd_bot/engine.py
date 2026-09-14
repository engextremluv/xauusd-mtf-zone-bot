"""Bar-by-bar backtest engine: drives the Daily -> 4H -> 30M -> 5M
hierarchy (spec section 33) off one base 5-minute feed, feeding the
SignalEngine and TradeManager and tracking equity/risk over time.

Only fully closed candles are ever handed to the strategy (spec section
31): a higher-timeframe bar is "closed" the moment the *next* 5-minute
bar belongs to a different bucket of that timeframe -- so the very last,
possibly-still-forming bucket at the end of the requested range is never
treated as closed, since there is no following 5M bar to prove it ended.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .candles import Candle
from .config import StrategyConfig
from .risk import RiskManager
from .signals import SignalEngine
from .timeframes import build_timeframes
from .trade_manager import TradeManager
from .zones import Direction


def _owning_bucket_starts(base_index: pd.DatetimeIndex, bucket_index: pd.DatetimeIndex) -> np.ndarray:
    """For each timestamp in base_index, the start-timestamp of the
    bucket in bucket_index that contains it (last bucket start <= ts).
    """
    positions = np.searchsorted(bucket_index.values, base_index.values, side="right") - 1
    result = np.full(len(base_index), np.datetime64("NaT"), dtype="datetime64[ns]")
    valid = positions >= 0
    result[valid] = bucket_index.values[positions[valid]]
    return result


@dataclass
class BacktestResult:
    trade_log: pd.DataFrame
    equity_curve: pd.DataFrame
    missed_entries: int = 0
    positions: pd.DataFrame = None  # one row per opened Position: entry/exit detail


@dataclass
class SpreadCommissionModel:
    spread_price: float = 0.30  # typical retail XAUUSD spread, price units (e.g. $0.30)
    commission_per_lot_round_turn: float = 0.0


@dataclass
class _PendingEntry:
    signal: object  # EntrySignal
    entry_level: float
    bars_waited: int = 0


@dataclass
class BacktestEngine:
    config: StrategyConfig
    costs: SpreadCommissionModel = field(default_factory=SpreadCommissionModel)
    starting_equity: float = 10_000.0

    def run(self, base_5m: pd.DataFrame, daily_origin_offset_hours: float = 0.0) -> BacktestResult:
        tfs = build_timeframes(base_5m, daily_origin_offset_hours=daily_origin_offset_hours)
        m5, m30, h4, d1 = tfs["5min"], tfs["30min"], tfs["4h"], tfs["1D"]

        bucket_30 = _owning_bucket_starts(m5.index, m30.index)
        bucket_4h = _owning_bucket_starts(m5.index, h4.index)
        bucket_1d = _owning_bucket_starts(m5.index, d1.index)

        signal_engine = SignalEngine(config=self.config)
        risk_manager = RiskManager(config=self.config, starting_equity=self.starting_equity)
        trade_manager = TradeManager(config=self.config, risk_manager=risk_manager)

        equity_rows = []
        realized_pnl_total = 0.0
        current_day = None
        current_day_start_realized = 0.0

        prev_30 = prev_4h = prev_1d = None
        n = len(m5)
        pending_entries: list[_PendingEntry] = []
        missed_entries = 0

        for i in range(n):
            ts = m5.index[i]
            row = m5.iloc[i]
            candle_5m = Candle(row.open, row.high, row.low, row.close)

            b30, b4h, b1d = bucket_30[i], bucket_4h[i], bucket_1d[i]

            # emit close events for the PREVIOUS bucket the moment we
            # observe membership changed -- see module docstring.
            # bucket_* arrays are plain numpy datetime64 (tz stripped by
            # np.searchsorted); re-attach UTC before using them to index
            # the tz-aware resampled frames.
            if prev_1d is not None and b1d != prev_1d and pd.notna(prev_1d):
                d_ts = pd.Timestamp(prev_1d).tz_localize("UTC")
                closed_so_far = d1.loc[:d_ts]
                signal_engine.on_daily_close(closed_so_far)

            if prev_4h is not None and b4h != prev_4h and pd.notna(prev_4h):
                h_ts = pd.Timestamp(prev_4h).tz_localize("UTC")
                r = h4.loc[h_ts]
                signal_engine.on_4h_close(h_ts, Candle(r.open, r.high, r.low, r.close))

            if prev_30 is not None and b30 != prev_30 and pd.notna(prev_30):
                t_ts = pd.Timestamp(prev_30).tz_localize("UTC")
                r = m30.loc[t_ts]
                signal_engine.on_30m_close(t_ts, Candle(r.open, r.high, r.low, r.close))

            # daily drawdown day-roll, using the day this 5m bar falls in
            trading_day = pd.Timestamp(b1d).tz_localize("UTC") if pd.notna(b1d) else None
            if trading_day is not None and trading_day != current_day:
                current_day = trading_day
                current_day_start_realized = realized_pnl_total
                risk_manager.roll_day(trading_day, equity=self.starting_equity + realized_pnl_total)

            # manage existing positions against this closed 5M bar
            log_len_before = len(trade_manager.closed_trade_log)
            trade_manager.on_5m_bar(ts, candle_5m, signal_engine.zm_30m, signal_engine.zm_4h)
            realized_pnl_total += sum(
                t["pnl"] for t in trade_manager.closed_trade_log[log_len_before:]
            )

            floating = trade_manager.total_floating_pnl(
                self.config.instrument, lambda direction, c=candle_5m: c.close
            )
            realized_today = realized_pnl_total - current_day_start_realized
            halted = risk_manager.check_daily_drawdown(realized_today, floating)

            # resolve pending pullback-entry orders queued from EARLIER
            # bars against THIS bar's range (never the bar a signal was
            # just generated on -- see module docstring / new-signal
            # handling below for why).
            if pending_entries:
                still_pending: list[_PendingEntry] = []
                for pe in pending_entries:
                    touched = candle_5m.low <= pe.entry_level <= candle_5m.high
                    if not halted and touched:
                        # limit-style fill AT the structural entry level,
                        # still crossing the spread on a BUY.
                        entry_price = pe.entry_level + (
                            self.costs.spread_price if pe.signal.direction is Direction.BUY else 0.0
                        )
                        trade_manager.try_open(
                            pe.signal, ts, entry_price, equity=self.starting_equity + realized_pnl_total
                        )
                    else:
                        pe.bars_waited += 1
                        if pe.bars_waited < self.config.pullback_max_wait_bars:
                            still_pending.append(pe)
                        else:
                            missed_entries += 1  # price never pulled back; per config, we do NOT chase
                pending_entries = still_pending

            # generate + act on entry signals from this closed 5M bar
            signals = signal_engine.on_5m_close(ts, candle_5m)
            if not halted:
                for sig in signals:
                    if self.config.require_pullback_entry:
                        # spec 15/16: prefer a fill at the structural entry
                        # level (the setup/"X" candle's far wick) rather
                        # than chasing the market -- checked starting the
                        # NEXT bar, never this one (this candle already
                        # produced the confirmation; using it again to
                        # decide the fill would be look-ahead).
                        setup_candle = sig.setup_5m_zone.setup_candle
                        entry_level = (
                            setup_candle.high if sig.direction is Direction.BUY else setup_candle.low
                        )
                        pending_entries.append(_PendingEntry(signal=sig, entry_level=entry_level))
                    else:
                        # base_5m is fetched at OFFER_SIDE_BID: close == bid.
                        # A BUY fills at ask (bid + spread); a SELL fills at
                        # the bid itself, i.e. close unadjusted.
                        entry_price = row.close + (
                            self.costs.spread_price if sig.direction is Direction.BUY else 0.0
                        )
                        trade_manager.try_open(
                            sig, ts, entry_price, equity=self.starting_equity + realized_pnl_total
                        )

            equity_rows.append(
                {
                    "timestamp": ts,
                    "equity": self.starting_equity + realized_pnl_total + floating,
                    "realized_pnl": realized_pnl_total,
                    "floating_pnl": floating,
                    "open_positions": sum(1 for p in trade_manager.positions if not p.closed),
                    "trading_halted": halted,
                }
            )

            prev_30, prev_4h, prev_1d = b30, b4h, b1d
            signal_engine.prune_chains()

        trade_log = pd.DataFrame(trade_manager.closed_trade_log)
        equity_curve = pd.DataFrame(equity_rows).set_index("timestamp")
        positions = pd.DataFrame(
            [
                {
                    "position_id": p.id,
                    "setup_id": p.setup_id,
                    "direction": p.direction.value,
                    "opened_at": p.opened_at,
                    "entry_price": p.entry_price,
                    "initial_sl": p.initial_sl,
                    "final_sl": p.sl,
                    "lots": p.lots,
                    "closed_at": p.closed_at,
                    "close_reason": p.close_reason,
                    "realized_pnl": p.realized_pnl,
                }
                for p in trade_manager.positions
            ]
        )
        return BacktestResult(
            trade_log=trade_log, equity_curve=equity_curve, missed_entries=missed_entries, positions=positions
        )
