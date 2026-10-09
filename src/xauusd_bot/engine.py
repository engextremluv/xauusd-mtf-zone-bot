"""Backtest engine for the H4 Three-Candle Pullback strategy.

Takes a single 5-minute bid+ask feed (columns open/high/low/close for
bid, ao/ah/al/ac for ask) and derives Daily and H4 candles from the bid
side via `timeframes.build_timeframes` -- see that module for why
deriving timeframes from one base feed (rather than fetching each
separately) is the correct, not-an-approximation way to build them.

Fill convention (this is what makes the backtest realistic rather than
assuming a single frictionless price): a BUY enters at the ASK and exits
(stop, target, or time-limit) at the BID; a SELL enters at the BID and
exits at the ASK. This is the actual cost a retail spread imposes on a
round trip, not a flat fee bolted on afterward.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .risk import max_lots_for_budget
from .timeframes import build_timeframes
from .config import H4PullbackConfig
from .indicators import candle_color, compute_atr, compute_ema


@dataclass
class _OpenTrade:
    direction: str  # "buy" | "sell"
    opened_at: pd.Timestamp
    entry: float
    sl: float
    tp: float
    stop_distance: float
    lots: float
    deadline: pd.Timestamp


@dataclass
class H4PullbackResult:
    trades: pd.DataFrame
    equity_curve: pd.DataFrame


class H4PullbackEngine:
    def __init__(self, config: H4PullbackConfig, starting_equity: float = 10_000.0):
        if not config.one_trade_at_a_time:
            # the engine only ever tracks a single open trade slot; this
            # flag documents the rule, it isn't a switch for concurrent
            # positions (not implemented).
            raise NotImplementedError(
                "H4PullbackEngine only supports one_trade_at_a_time=True"
            )
        self.config = config
        self.starting_equity = starting_equity

    def run(self, bidask_5m: pd.DataFrame) -> H4PullbackResult:
        """bidask_5m: DataFrame indexed by UTC timestamp with columns
        open/high/low/close (bid) and ao/ah/al/ac (ask), at 5-minute
        resolution (see data_provider.load_csv_5m_bidask).
        """
        cfg = self.config
        required_ask = {"ao", "ah", "al", "ac"}
        if not required_ask.issubset(bidask_5m.columns):
            raise ValueError(f"bidask_5m is missing ask columns: {required_ask - set(bidask_5m.columns)}")

        base_bid = bidask_5m[["open", "high", "low", "close"]]
        tfs = build_timeframes(base_bid, daily_origin_offset_hours=cfg.daily_origin_offset_hours)
        daily = tfs["1D"].copy()
        h4 = tfs["4h"].copy()
        m5 = bidask_5m

        daily["ema"] = compute_ema(daily["close"], cfg.ema_period)
        h4["atr"] = compute_atr(h4, cfg.atr_period)
        h4["color"] = [candle_color(o, c) for o, c in zip(h4["open"], h4["close"])]

        # "yesterday's" completed daily candle decides today's mode
        daily_mode = pd.Series(
            np.where(daily["close"] > daily["ema"], "buy", "sell"), index=daily.index
        ).shift(1)

        trades: list[dict] = []
        equity = self.starting_equity
        equity_rows = []
        open_trade: _OpenTrade | None = None
        run_color: str | None = None
        run_len = 0
        h4_index = h4.index

        for i in range(len(h4)):
            ts = h4_index[i]
            row = h4.iloc[i]

            day = ts.normalize()
            prior_days = daily_mode.index[daily_mode.index <= day]
            if len(prior_days) == 0 or pd.isna(daily_mode.get(prior_days[-1])):
                run_color, run_len = None, 0
                continue
            mode = daily_mode.loc[prior_days[-1]]

            if open_trade is not None:
                open_trade, closed, equity = self._manage_open_trade(open_trade, m5, ts, equity, trades)
                if closed:
                    equity_rows.append({"timestamp": ts, "equity": equity})

            color = row["color"]
            target_color = "red" if mode == "buy" else "green"
            if color == target_color:
                run_len = run_len + 1 if run_color == target_color else 1
                run_color = target_color
            else:
                run_color, run_len = None, 0

            if open_trade is None and run_len >= cfg.pullback_candles and i + 1 < len(h4):
                atr = row["atr"]
                if pd.notna(atr) and atr > 0:
                    new_trade = self._try_enter(mode, atr, h4_index[i + 1], m5, equity)
                    if new_trade is not None:
                        open_trade = new_trade
                        run_color, run_len = None, 0

        trade_log = pd.DataFrame(trades)
        equity_curve = pd.DataFrame(equity_rows).set_index("timestamp") if equity_rows else pd.DataFrame(
            columns=["equity"]
        )
        return H4PullbackResult(trades=trade_log, equity_curve=equity_curve)

    def _try_enter(self, direction: str, atr: float, entry_ts: pd.Timestamp, m5: pd.DataFrame, equity: float):
        cfg = self.config
        candidates = m5.loc[entry_ts:]
        if len(candidates) == 0:
            return None
        entry_bar = candidates.iloc[0]
        entry_ts = candidates.index[0]

        entry_price = entry_bar["ao"] if direction == "buy" else entry_bar["open"]
        stop_distance = cfg.atr_multiplier * atr
        tp_distance = cfg.reward_risk_ratio * stop_distance
        if direction == "buy":
            sl = entry_price - stop_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + stop_distance
            tp = entry_price - tp_distance

        budget = equity * (cfg.risk_percent / 100.0)
        lots = max_lots_for_budget(cfg.instrument, stop_distance, budget)
        if lots < cfg.instrument.min_lot:
            return None

        return _OpenTrade(
            direction=direction,
            opened_at=entry_ts,
            entry=entry_price,
            sl=sl,
            tp=tp,
            stop_distance=stop_distance,
            lots=lots,
            deadline=entry_ts + pd.Timedelta(hours=cfg.time_limit_hours),
        )

    def _manage_open_trade(
        self, open_trade: _OpenTrade, m5: pd.DataFrame, h4_ts: pd.Timestamp, equity: float, trades: list[dict]
    ):
        window = m5.loc[open_trade.opened_at : h4_ts]
        window = window[window.index > open_trade.opened_at]
        for t5, bar in window.iterrows():
            if t5 > open_trade.deadline:
                break
            if open_trade.direction == "buy":
                hit_sl = bar["low"] <= open_trade.sl  # exits at BID
                hit_tp = bar["high"] >= open_trade.tp
            else:
                hit_sl = bar["ah"] >= open_trade.sl  # exits at ASK
                hit_tp = bar["al"] <= open_trade.tp
            if hit_sl or hit_tp:
                if hit_sl and hit_tp:
                    reason, exit_price = "sl_and_tp_same_bar", open_trade.sl
                elif hit_sl:
                    reason, exit_price = "sl", open_trade.sl
                else:
                    reason, exit_price = "tp", open_trade.tp
                equity = self._close(open_trade, exit_price, t5, reason, equity, trades)
                return None, True, equity
        if h4_ts >= open_trade.deadline:
            last_bar = m5.loc[:h4_ts].iloc[-1]
            exit_price = last_bar["close"] if open_trade.direction == "buy" else last_bar["ac"]
            equity = self._close(open_trade, exit_price, h4_ts, "time_limit", equity, trades)
            return None, True, equity
        return open_trade, False, equity

    def _close(
        self, open_trade: _OpenTrade, exit_price: float, exit_ts: pd.Timestamp, reason: str, equity: float, trades: list[dict]
    ) -> float:
        cfg = self.config
        signed_move = (
            exit_price - open_trade.entry if open_trade.direction == "buy" else open_trade.entry - exit_price
        )
        r_multiple = signed_move / open_trade.stop_distance
        if reason == "sl_and_tp_same_bar":
            # same convention the source strategy book uses: counts as a loss
            r_multiple = -1.0
            signed_move = -open_trade.stop_distance
        pnl = cfg.instrument.pnl(open_trade.lots, signed_move)
        equity += pnl
        trades.append(
            {
                "direction": open_trade.direction,
                "opened_at": open_trade.opened_at,
                "entry": open_trade.entry,
                "sl": open_trade.sl,
                "tp": open_trade.tp,
                "stop_distance": open_trade.stop_distance,
                "lots": open_trade.lots,
                "closed_at": exit_ts,
                "exit_price": exit_price,
                "reason": reason,
                "r_multiple": r_multiple,
                "pnl": pnl,
                "equity_after": equity,
            }
        )
        return equity
