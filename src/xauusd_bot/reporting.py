"""Summary statistics for an H4PullbackResult.

The equity curve from H4PullbackEngine is sampled only at trade closes
(there is at most one open position at a time, so that's every point
where equity actually changes), not a continuous bar-by-bar series.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import H4PullbackResult


@dataclass
class H4PullbackStats:
    total_trades: int
    win_rate: float
    total_r: float
    avg_r_per_trade: float
    max_drawdown_r: float
    final_equity: float
    max_drawdown_pct: float

    def as_dict(self) -> dict:
        return self.__dict__


def compute_stats(result: H4PullbackResult, starting_equity: float) -> H4PullbackStats:
    trades = result.trades
    if len(trades) == 0:
        return H4PullbackStats(0, 0.0, 0.0, 0.0, 0.0, starting_equity, 0.0)

    win_rate = (trades["r_multiple"] > 0).mean()
    total_r = trades["r_multiple"].sum()
    avg_r = trades["r_multiple"].mean()

    cum_r = trades["r_multiple"].cumsum()
    running_max_r = cum_r.cummax()
    drawdown_r = cum_r - running_max_r
    max_dd_r = drawdown_r.min()

    equity = result.equity_curve["equity"] if len(result.equity_curve) else pd.Series([starting_equity])
    running_max_eq = equity.cummax()
    dd_pct = ((equity - running_max_eq) / running_max_eq).min() * 100

    return H4PullbackStats(
        total_trades=int(len(trades)),
        win_rate=float(win_rate),
        total_r=float(total_r),
        avg_r_per_trade=float(avg_r),
        max_drawdown_r=float(max_dd_r),
        final_equity=float(equity.iloc[-1]) if len(equity) else starting_equity,
        max_drawdown_pct=float(dd_pct),
    )


def by_year(result: H4PullbackResult) -> pd.DataFrame:
    trades = result.trades.copy()
    if len(trades) == 0:
        return pd.DataFrame(columns=["count", "total_r", "avg_r", "win_rate"])
    trades["year"] = pd.to_datetime(trades["opened_at"]).dt.year
    g = trades.groupby("year")
    return pd.DataFrame(
        {
            "count": g.size(),
            "total_r": g["r_multiple"].sum(),
            "avg_r": g["r_multiple"].mean(),
            "win_rate": g["r_multiple"].apply(lambda s: (s > 0).mean()),
        }
    )


def by_direction(result: H4PullbackResult) -> pd.DataFrame:
    trades = result.trades
    if len(trades) == 0:
        return pd.DataFrame(columns=["count", "total_r", "avg_r", "win_rate"])
    g = trades.groupby("direction")
    return pd.DataFrame(
        {
            "count": g.size(),
            "total_r": g["r_multiple"].sum(),
            "avg_r": g["r_multiple"].mean(),
            "win_rate": g["r_multiple"].apply(lambda s: (s > 0).mean()),
        }
    )
