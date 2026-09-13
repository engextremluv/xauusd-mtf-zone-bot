from xauusd_bot.candles import Candle, closes_beyond, is_buy_confirmation, is_sell_confirmation


def test_buy_confirmation_requires_close_beyond_high_not_just_wick_spike():
    setup = Candle(open=2000, high=2010, low=1995, close=1998)  # red
    # confirm candle spikes intrabar above setup.high but CLOSES back inside -> not a confirmation
    spike_only = Candle(open=1999, high=2015, low=1998, close=2005)
    assert spike_only.is_bullish
    assert not is_buy_confirmation(setup, spike_only)  # close 2005 < high 2010

    # confirm candle closes beyond the entire wick -> valid
    real_confirm = Candle(open=2005, high=2020, low=2004, close=2012)
    assert is_buy_confirmation(setup, real_confirm)


def test_buy_confirmation_requires_bullish_confirm_candle():
    setup = Candle(open=2000, high=2010, low=1995, close=1998)
    bearish_but_closes_high = Candle(open=2020, high=2025, low=2011, close=2012)
    assert bearish_but_closes_high.is_bearish
    assert not is_buy_confirmation(setup, bearish_but_closes_high)


def test_sell_confirmation_mirrors_buy():
    setup = Candle(open=1998, high=2010, low=1995, close=2002)  # green
    # wick pokes below setup.low intrabar but closes back above it -> not a confirmation
    spike_only = Candle(open=1998, high=1999, low=1990, close=1996)
    assert spike_only.is_bearish
    assert not is_sell_confirmation(setup, spike_only)  # close 1996 > low 1995

    real_confirm = Candle(open=1994, high=1995, low=1985, close=1988)
    assert is_sell_confirmation(setup, real_confirm)


def test_closes_beyond():
    c = Candle(open=10, high=12, low=8, close=11)
    assert closes_beyond(c, 10.5, "up")
    assert not closes_beyond(c, 11.5, "up")
    c2 = Candle(open=10, high=12, low=8, close=9)
    assert closes_beyond(c2, 9.5, "down")
    assert not closes_beyond(c2, 8.5, "down")
