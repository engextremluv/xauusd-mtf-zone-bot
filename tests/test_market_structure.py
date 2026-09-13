import pandas as pd

from xauusd_bot.market_structure import MarketStructure, classify_structure


def make_df(highs, lows):
    n = len(highs)
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    opens = closes
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=idx)


def test_uptrend_hh_hl():
    # zigzag with each swing high/low a 3-bar fractal, both rising over time
    highs = [10, 15, 11, 20, 16, 25, 18]
    lows = [5, 9, 6, 12, 10, 15, 11]
    df = make_df(highs, lows)
    assert classify_structure(df, lookback=1, lookahead=1) == MarketStructure.BULLISH


def test_downtrend_lh_ll():
    # time-reverse of the uptrend fixture: swing highs/lows both falling
    highs = [18, 25, 16, 20, 11, 15, 10]
    lows = [11, 15, 10, 12, 6, 9, 5]
    df = make_df(highs, lows)
    assert classify_structure(df, lookback=1, lookahead=1) == MarketStructure.BEARISH


def test_ranging_market():
    # swing highs and lows both flat / mixed -> range
    highs = [20, 22, 19, 21, 18, 22, 19, 21, 18]
    lows = [10, 12, 9, 11, 8, 12, 9, 11, 8]
    df = make_df(highs, lows)
    result = classify_structure(df, lookback=2, lookahead=2)
    assert result == MarketStructure.RANGE


def test_insufficient_data_is_range():
    df = make_df([10, 11], [5, 6])
    assert classify_structure(df) == MarketStructure.RANGE


def test_no_lookahead_bias_swing_not_confirmed_without_future_bars():
    # a clear rising sequence but too short after the "current" bar for the
    # last swing to be confirmed -- classify_structure must not use data it
    # doesn't have, so passing only up to the ambiguous point should not
    # spuriously confirm a swing at the very last bars.
    highs = [10, 12, 11, 15, 13]  # last potential swing high (15) unconfirmed w/ lookahead=2
    lows = [5, 8, 6, 10, 9]
    df = make_df(highs, lows)
    from xauusd_bot.market_structure import detect_swing_highs

    swings = detect_swing_highs(df, lookback=2, lookahead=2)
    # index 3 (price 15) cannot be confirmed: needs 2 bars after it, only 1 exists
    assert all(s.index != 3 for s in swings)
