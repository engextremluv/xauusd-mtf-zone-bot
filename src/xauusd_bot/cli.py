from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .config import StrategyConfig
from .data_provider import DukascopyDataProvider, load_csv_5m, load_csv_5m_bidask
from .engine import BacktestEngine
from .h4_pullback.config import H4PullbackConfig
from .h4_pullback.engine import H4PullbackEngine
from .h4_pullback.reporting import by_direction, by_year, compute_stats as compute_h4_stats
from .reporting import compute_stats, save_reports


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def cmd_fetch_data(args: argparse.Namespace) -> None:
    provider = DukascopyDataProvider(symbol=args.symbol, cache_dir=Path(args.cache_dir))
    df = provider.fetch_5m(_parse_date(args.start), _parse_date(args.end))
    print(f"Fetched {len(df)} 5-minute bars for {args.symbol}: {df.index.min()} -> {df.index.max()}")


def cmd_run_backtest(args: argparse.Namespace) -> None:
    if args.csv:
        base_5m = load_csv_5m(args.csv)
    else:
        provider = DukascopyDataProvider(symbol=args.symbol, cache_dir=Path(args.cache_dir))
        base_5m = provider.fetch_5m(_parse_date(args.start), _parse_date(args.end))

    config = StrategyConfig()
    engine = BacktestEngine(config=config, starting_equity=args.equity)
    result = engine.run(base_5m, daily_origin_offset_hours=args.daily_origin_offset_hours)

    stats = compute_stats(result, starting_equity=args.equity)
    for k, v in stats.as_dict().items():
        print(f"{k:>20}: {v}")

    if args.out:
        save_reports(result, args.out)
        print(f"Saved trade log and equity curve to {args.out}/")


def cmd_run_h4_pullback(args: argparse.Namespace) -> None:
    if args.csv:
        bidask = load_csv_5m_bidask(args.csv)
    else:
        provider = DukascopyDataProvider(symbol=args.symbol, cache_dir=Path(args.cache_dir))
        bidask = provider.fetch_5m_bidask(_parse_date(args.start), _parse_date(args.end))

    config = H4PullbackConfig(risk_percent=args.risk_percent)
    engine = H4PullbackEngine(config=config, starting_equity=args.equity)
    result = engine.run(bidask)

    stats = compute_h4_stats(result, starting_equity=args.equity)
    for k, v in stats.as_dict().items():
        print(f"{k:>20}: {v}")
    print()
    print("by year:")
    print(by_year(result))
    print()
    print("by direction:")
    print(by_direction(result))

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        result.trades.to_csv(out_dir / "h4_pullback_trades.csv", index=False)
        result.equity_curve.to_csv(out_dir / "h4_pullback_equity_curve.csv")
        print(f"Saved trade log and equity curve to {out_dir}/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xauusd-bot")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch-data", help="Fetch and cache base 5-minute OHLC from Dukascopy")
    p_fetch.add_argument("--symbol", default="XAUUSD")
    p_fetch.add_argument("--start", required=True, help="YYYY-MM-DD")
    p_fetch.add_argument("--end", required=True, help="YYYY-MM-DD")
    p_fetch.add_argument("--cache-dir", default=".cache/xauusd_bot")
    p_fetch.set_defaults(func=cmd_fetch_data)

    p_run = sub.add_parser("run-backtest", help="Run the strategy backtest")
    p_run.add_argument("--symbol", default="XAUUSD")
    p_run.add_argument("--start", help="YYYY-MM-DD (ignored if --csv is given)")
    p_run.add_argument("--end", help="YYYY-MM-DD (ignored if --csv is given)")
    p_run.add_argument("--csv", help="Path to a local 5-minute OHLC CSV instead of fetching")
    p_run.add_argument("--cache-dir", default=".cache/xauusd_bot")
    p_run.add_argument("--equity", type=float, default=10_000.0)
    p_run.add_argument("--daily-origin-offset-hours", type=float, default=0.0)
    p_run.add_argument("--out", help="Directory to write trades.csv / equity_curve.csv")
    p_run.set_defaults(func=cmd_run_backtest)

    p_h4 = sub.add_parser(
        "run-h4-pullback", help="Run the H4 Three-Candle Pullback strategy backtest"
    )
    p_h4.add_argument("--symbol", default="XAUUSD")
    p_h4.add_argument("--start", help="YYYY-MM-DD (ignored if --csv is given)")
    p_h4.add_argument("--end", help="YYYY-MM-DD (ignored if --csv is given)")
    p_h4.add_argument(
        "--csv", help="Path to a local 5-minute bid+ask CSV (columns open/high/low/close/ao/ah/al/ac)"
    )
    p_h4.add_argument("--cache-dir", default=".cache/xauusd_bot")
    p_h4.add_argument("--equity", type=float, default=10_000.0)
    p_h4.add_argument("--risk-percent", type=float, default=1.0)
    p_h4.add_argument("--out", help="Directory to write h4_pullback_trades.csv / equity_curve.csv")
    p_h4.set_defaults(func=cmd_run_h4_pullback)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
