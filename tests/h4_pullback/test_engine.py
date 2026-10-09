import pandas as pd
import pytest

from xauusd_bot.config import InstrumentSpec
from xauusd_bot.h4_pullback.config import H4PullbackConfig
from xauusd_bot.h4_pullback.engine import H4PullbackEngine

SPREAD = 0.30


def bars(rows: list[tuple]) -> pd.DataFrame:
    """rows: (timestamp, open, high, low, close) bid tuples. Ask = bid + SPREAD."""
    data = []
    for ts, o, h, l, c in rows:
        data.append(
            {
                "timestamp": pd.Timestamp(ts, tz="UTC"),
                "open": o, "high": h, "low": l, "close": c,
                "ao": o + SPREAD, "ah": h + SPREAD, "al": l + SPREAD, "ac": c + SPREAD,
            }
        )
    return pd.DataFrame(data).set_index("timestamp").sort_index()


def make_config():
    return H4PullbackConfig(
        ema_period=2,
        atr_period=3,
        atr_multiplier=2.0,
        reward_risk_ratio=1.0,
        time_limit_hours=200.0,
        risk_percent=1.0,
        instrument=InstrumentSpec(contract_size=100, tick_size=0.01, tick_value_per_lot=1.0,
                                   min_lot=0.01, max_lot=50, lot_step=0.01),
    )


def test_buy_entry_fills_at_ask_and_exits_at_tp_on_bid_touch():
    rows = [
        # priming days: sustained uptrend so day6 mode = BUY
        ("2024-01-01 00:00", 100, 101, 99, 100),
        ("2024-01-02 00:00", 100, 103, 99, 102),
        ("2024-01-03 00:00", 102, 105, 101, 104),
        ("2024-01-04 00:00", 104, 107, 103, 106),
        ("2024-01-05 00:00", 106, 109, 105, 108),
        # day 6: three red H4 candles (pullback against the uptrend)
        ("2024-01-06 00:00", 108, 108.5, 106.5, 107),  # red
        ("2024-01-06 04:00", 107, 107.2, 105.0, 106),  # red
        ("2024-01-06 08:00", 106, 106.2, 104.0, 105),  # red -> signal after this closes
        # entry candle: fills at ASK open = 105 + 0.30 = 105.30
        ("2024-01-06 12:00", 105.0, 105.1, 104.9, 105.05),
        # a later 5-min bar inside the same H4 period that touches TP on the BID side
        ("2024-01-06 12:05", 105.0, 110.0, 104.0, 109.8),
        # need a further H4 bucket so the engine's management step actually
        # looks at the 12:05 bar (management runs when the NEXT H4 bar is processed)
        ("2024-01-06 16:00", 109.8, 110.0, 109.5, 109.9),
    ]
    df = bars(rows)
    engine = H4PullbackEngine(config=make_config(), starting_equity=10_000.0)
    result = engine.run(df)

    assert len(result.trades) == 1
    t = result.trades.iloc[0]
    assert t["direction"] == "buy"
    assert t["entry"] == pytest.approx(105.30)
    # ATR(3) at the 08:00 signal candle, hand-computed from TR(06:00)=2,
    # TR(04:00... wait see test docstring in repo for the full derivation
    expected_atr = (2.0 + 2.2 + 2.2) / 3
    expected_stop_distance = 2.0 * expected_atr
    assert t["stop_distance"] == pytest.approx(expected_stop_distance, rel=1e-6)
    assert t["sl"] == pytest.approx(105.30 - expected_stop_distance, rel=1e-6)
    assert t["tp"] == pytest.approx(105.30 + expected_stop_distance, rel=1e-6)
    assert t["reason"] == "tp"
    assert t["exit_price"] == pytest.approx(t["tp"])
    assert t["closed_at"] == pd.Timestamp("2024-01-06 12:05", tz="UTC")
    assert t["r_multiple"] == pytest.approx(1.0, rel=1e-6)
    assert t["pnl"] > 0


def test_sell_entry_fills_at_bid_and_exits_at_sl_on_ask_touch():
    rows = [
        # priming days: sustained downtrend so day6 mode = SELL
        ("2024-01-01 00:00", 108, 109, 107, 108),
        ("2024-01-02 00:00", 108, 108, 105, 106),
        ("2024-01-03 00:00", 106, 106, 103, 104),
        ("2024-01-04 00:00", 104, 104, 101, 102),
        ("2024-01-05 00:00", 102, 102, 99, 100),
        # day 6: three green H4 candles (pullback against the downtrend)
        ("2024-01-06 00:00", 100.0, 101.5, 99.8, 101.0),   # green
        ("2024-01-06 04:00", 101.0, 102.2, 100.8, 102.0),  # green
        ("2024-01-06 08:00", 102.0, 103.2, 101.8, 103.0),  # green -> signal
        # entry candle: SELL fills at BID open = 103
        ("2024-01-06 12:00", 103.0, 103.2, 102.8, 103.1),
        # later bar: ASK high touches SL (sl = 106.0, so bid high 105.8 -> ask 106.1)
        ("2024-01-06 12:05", 103.1, 105.8, 103.0, 105.5),
        ("2024-01-06 16:00", 105.5, 105.6, 105.4, 105.5),
    ]
    df = bars(rows)
    engine = H4PullbackEngine(config=make_config(), starting_equity=10_000.0)
    result = engine.run(df)

    assert len(result.trades) == 1
    t = result.trades.iloc[0]
    assert t["direction"] == "sell"
    assert t["entry"] == pytest.approx(103.0)
    expected_atr = (1.7 + 1.4 + 1.4) / 3
    expected_stop_distance = 2.0 * expected_atr
    assert t["sl"] == pytest.approx(103.0 + expected_stop_distance, rel=1e-6)
    assert t["tp"] == pytest.approx(103.0 - expected_stop_distance, rel=1e-6)
    assert t["reason"] == "sl"
    assert t["r_multiple"] == pytest.approx(-1.0, rel=1e-6)
    assert t["pnl"] < 0


def test_one_trade_at_a_time_blocks_a_second_signal_while_one_is_open():
    rows = [
        ("2024-01-01 00:00", 100, 101, 99, 100),
        ("2024-01-02 00:00", 100, 103, 99, 102),
        ("2024-01-03 00:00", 102, 105, 101, 104),
        ("2024-01-04 00:00", 104, 107, 103, 106),
        ("2024-01-05 00:00", 106, 109, 105, 108),
        ("2024-01-06 00:00", 108, 108.5, 106.5, 107),
        ("2024-01-06 04:00", 107, 107.2, 105.0, 106),
        ("2024-01-06 08:00", 106, 106.2, 104.0, 105),  # 1st signal after this
        # entry candle: price barely moves, trade stays open (no SL/TP hit)
        ("2024-01-06 12:00", 105.0, 105.2, 104.8, 105.0),
        # a SECOND fresh 3-red-candle run forms while the first trade is still open.
        # If the engine incorrectly let this open a second position, the
        # eventually-recorded trade below would show THIS entry (~103.5,
        # day7 00:00) instead of the first one (105.30, day6 12:00).
        ("2024-01-06 16:00", 105.0, 105.1, 104.0, 104.5),   # red
        ("2024-01-06 20:00", 104.5, 104.6, 103.5, 104.0),   # red
        ("2024-01-07 00:00", 104.0, 104.1, 103.0, 103.5),   # red -> would-be 2nd signal
        ("2024-01-07 04:00", 103.5, 103.6, 103.4, 103.5),   # candle after the would-be entry
        # first trade's 20h time limit (12:00 Jan6 + 20h) expires exactly here
        ("2024-01-07 08:00", 103.5, 103.5, 103.5, 103.5),
    ]
    df = bars(rows)
    cfg = make_config()
    cfg.time_limit_hours = 20.0
    engine = H4PullbackEngine(config=cfg, starting_equity=10_000.0)
    result = engine.run(df)

    assert len(result.trades) == 1
    t = result.trades.iloc[0]
    # the ONE recorded trade must be the FIRST entry, not the blocked second one
    assert t["opened_at"] == pd.Timestamp("2024-01-06 12:00", tz="UTC")
    assert t["entry"] == pytest.approx(105.30)
    assert t["reason"] == "time_limit"
