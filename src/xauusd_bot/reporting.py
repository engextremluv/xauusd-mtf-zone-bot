"""Summary statistics and CSV export for a BacktestResult."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .engine import BacktestResult


@dataclass
class PerformanceStats:
    total_trades: int
    win_rate: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    final_equity: float

    def as_dict(self) -> dict:
        return self.__dict__


def compute_stats(result: BacktestResult, starting_equity: float) -> PerformanceStats:
    log = result.trade_log
    equity = result.equity_curve["equity"]

    if len(log) == 0:
        total_trades = win_rate = profit_factor = total_pnl = 0.0
    else:
        # Win rate must be judged per ORIGINAL POSITION, not per closed
        # leg: a position that banked a big profit at the 30M roadblock
        # (spec 23) and then had its small runner stopped near breakeven
        # is a WINNING trade overall, even though its last leg's reason
        # is "sl". Summing every leg's pnl by position_id and checking
        # the total avoids undercounting these as losses.
        by_position = log.groupby("position_id")["pnl"].sum()
        total_trades = len(by_position)
        win_rate = (by_position > 0).sum() / total_trades if total_trades else 0.0
        gross_profit = log.loc[log["pnl"] > 0, "pnl"].sum()
        gross_loss = -log.loc[log["pnl"] < 0, "pnl"].sum()
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        total_pnl = log["pnl"].sum()

    running_max = equity.cummax()
    drawdown = equity - running_max
    max_dd = drawdown.min() if len(drawdown) else 0.0
    max_dd_pct = (drawdown / running_max).min() * 100 if len(drawdown) else 0.0

    return PerformanceStats(
        total_trades=int(total_trades),
        win_rate=float(win_rate),
        profit_factor=float(profit_factor),
        total_pnl=float(total_pnl),
        max_drawdown=float(max_dd),
        max_drawdown_pct=float(max_dd_pct),
        final_equity=float(equity.iloc[-1]) if len(equity) else starting_equity,
    )


def save_reports(result: BacktestResult, out_dir: str | Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result.trade_log.to_csv(out_dir / "trades.csv", index=False)
    result.equity_curve.to_csv(out_dir / "equity_curve.csv")
