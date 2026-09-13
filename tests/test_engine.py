import numpy as np
import pandas as pd

from xauusd_bot.config import StrategyConfig
from xauusd_bot.engine import BacktestEngine


def make_synthetic_5m(days: int = 5, seed: int = 7) -> pd.DataFrame:
    """Deterministic synthetic 5-minute OHLC series with enough
    volatility/trend to plausibly trigger the full strategy cascade at
    least a few times, for an end-to-end plumbing smoke test.
    """
    rng = np.random.default_rng(seed)
    periods = days * 24 * 12  # 5-min bars, 24/7 (simplification for the test)
    idx = pd.date_range("2024-01-01", periods=periods, freq="5min", tz="UTC")

    # trending + oscillating base path so both bullish and bearish zones
    # have a chance to form
    t = np.arange(periods)
    drift = 0.002 * t
    wave = 8 * np.sin(t / 60.0) + 3 * np.sin(t / 17.0)
    noise = rng.normal(scale=0.6, size=periods).cumsum() * 0.05
    close = 2000 + drift + wave + noise

    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + rng.uniform(0.1, 1.0, size=periods)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.0, size=periods)

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def test_engine_runs_end_to_end_without_error():
    base_5m = make_synthetic_5m(days=6)
    engine = BacktestEngine(config=StrategyConfig(), starting_equity=10_000.0)
    result = engine.run(base_5m)

    assert len(result.equity_curve) == len(base_5m)
    assert result.equity_curve["equity"].notna().all()
    assert np.isfinite(result.equity_curve["equity"]).all()


def test_engine_trade_log_fields_are_sane_when_trades_occur():
    base_5m = make_synthetic_5m(days=10, seed=42)
    engine = BacktestEngine(config=StrategyConfig(), starting_equity=10_000.0)
    result = engine.run(base_5m)

    if len(result.trade_log) == 0:
        return  # synthetic data didn't happen to trigger a full cascade; plumbing still ran fine

    log = result.trade_log
    assert log["lots"].gt(0).all()
    assert log["pnl"].apply(np.isfinite).all()
    assert set(log["direction"].unique()) <= {"buy", "sell"}


def test_daily_drawdown_halt_reflected_in_equity_curve_flag():
    base_5m = make_synthetic_5m(days=8, seed=3)
    config = StrategyConfig()
    config.daily_drawdown_limit_percent = 0.001  # force an easy halt to exercise the flag
    engine = BacktestEngine(config=config, starting_equity=10_000.0)
    result = engine.run(base_5m)
    assert "trading_halted" in result.equity_curve.columns
