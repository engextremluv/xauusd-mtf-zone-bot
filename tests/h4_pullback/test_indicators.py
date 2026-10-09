import pandas as pd
import pytest

from xauusd_bot.h4_pullback.indicators import candle_color, compute_atr, compute_ema


def test_candle_color():
    assert candle_color(open_=10, close=12) == "green"
    assert candle_color(open_=12, close=10) == "red"
    assert candle_color(open_=10, close=10) == "doji"


def test_compute_ema_matches_hand_calculation():
    # period=3 -> alpha = 2/(3+1) = 0.5
    close = pd.Series([10.0, 12.0, 11.0, 15.0, 14.0])
    ema = compute_ema(close, period=3)
    # ema[0] = 10 (seed)
    # ema[1] = 0.5*12 + 0.5*10 = 11
    # ema[2] = 0.5*11 + 0.5*11 = 11
    # ema[3] = 0.5*15 + 0.5*11 = 13
    # ema[4] = 0.5*14 + 0.5*13 = 13.5
    expected = [10.0, 11.0, 11.0, 13.0, 13.5]
    for got, exp in zip(ema, expected):
        assert got == pytest.approx(exp)


def test_compute_atr_simple_moving_average_of_true_range():
    # 4 bars, period=3 -> first 2 ATR values are NaN, 3rd/4th are the
    # plain mean of the first/last 3 true ranges.
    df = pd.DataFrame(
        {
            "open": [100, 102, 101, 105],
            "high": [103, 104, 106, 108],
            "low": [99, 100, 100, 103],
            "close": [102, 101, 105, 104],
        }
    )
    atr = compute_atr(df, period=3)

    # TR[0] = high-low = 103-99 = 4 (no prev close)
    # TR[1] = max(104-100, |104-102|, |100-102|) = max(4,2,2) = 4
    # TR[2] = max(106-100, |106-101|, |100-101|) = max(6,5,1) = 6
    # TR[3] = max(108-103, |108-105|, |103-105|) = max(5,3,2) = 5
    assert pd.isna(atr.iloc[0])
    assert pd.isna(atr.iloc[1])
    assert atr.iloc[2] == pytest.approx((4 + 4 + 6) / 3)
    assert atr.iloc[3] == pytest.approx((4 + 6 + 5) / 3)
