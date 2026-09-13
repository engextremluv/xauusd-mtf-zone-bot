from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .config import StrategyConfig
from .data_provider import DukascopyDataProvider, load_csv_5m
from .engine import BacktestEngine
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

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
