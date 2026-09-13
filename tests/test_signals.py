import pandas as pd

from xauusd_bot.candles import Candle
from xauusd_bot.config import StrategyConfig
from xauusd_bot.signals import SignalEngine
from xauusd_bot.zones import Direction

T0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")


def ts(i):
    return T0 + pd.Timedelta(minutes=5 * i)


def test_full_4h_30m_5m_cascade_produces_entry_signal():
    engine = SignalEngine(config=StrategyConfig())

    # 1) 4H BUY zone: red setup, then green confirm that does NOT overlap
    #    the zone itself (so retest is a separate, later event).
    red_4h = Candle(open=2010, high=2012, low=2000, close=2002)
    green_4h = Candle(open=2013, high=2020, low=2013, close=2016)
    assert engine.on_4h_close(ts(0), red_4h) == []
    confirmed = engine.on_4h_close(ts(1), green_4h)
    assert len(confirmed) == 1
    zone_4h = confirmed[0]
    assert zone_4h.direction is Direction.BUY
    assert (zone_4h.low, zone_4h.high) == (2000, 2012)
    assert not zone_4h.retested

    # 2) price returns to the 4H zone, observed via a 30M candle
    retest_30m = Candle(open=2015, high=2016, low=2005, close=2010)
    engine.on_30m_close(ts(2), retest_30m)
    assert zone_4h.retested

    # 3) build a 30M BUY confirmation zone (red -> green, not self-overlapping)
    red_30m = Candle(open=2008, high=2009, low=2003, close=2004)
    green_30m = Candle(open=2010, high=2020, low=2010, close=2015)
    engine.on_30m_close(ts(3), red_30m)
    confirmed_30m = engine.on_30m_close(ts(4), green_30m)
    assert len(confirmed_30m) == 1
    zone_30m = confirmed_30m[0]
    assert (zone_30m.low, zone_30m.high) == (2003, 2009)
    assert not zone_30m.retested

    # 4) price returns to the 30M zone via a 5M candle
    retest_5m = Candle(open=2012, high=2013, low=2005, close=2008)
    engine.on_5m_close(ts(5), retest_5m)
    assert zone_30m.retested

    # 5) build the 5M BUY confirmation (red -> green)
    red_5m = Candle(open=2007, high=2008, low=2002, close=2003)
    green_5m = Candle(open=2004, high=2015, low=2004, close=2010)
    assert engine.on_5m_close(ts(6), red_5m) == []
    signals = engine.on_5m_close(ts(7), green_5m)

    assert len(signals) == 1
    sig = signals[0]
    assert sig.direction is Direction.BUY
    assert sig.parent_4h_zone_id == zone_4h.id
    assert not sig.is_continuation
    # entry reference (spec 15/17): the RED 5M candle's high/low
    assert sig.setup_5m_zone.setup_candle.high == 2008
    assert sig.setup_5m_zone.setup_candle.low == 2002


def test_continuation_entry_after_new_30m_5m_cascade():
    engine = SignalEngine(config=StrategyConfig())
    red_4h = Candle(open=2010, high=2012, low=2000, close=2002)
    green_4h = Candle(open=2013, high=2020, low=2013, close=2016)
    engine.on_4h_close(ts(0), red_4h)
    engine.on_4h_close(ts(1), green_4h)
    engine.on_30m_close(ts(2), Candle(open=2015, high=2016, low=2005, close=2010))  # retest

    red_30m = Candle(open=2008, high=2009, low=2003, close=2004)
    green_30m = Candle(open=2010, high=2020, low=2010, close=2015)
    engine.on_30m_close(ts(3), red_30m)
    engine.on_30m_close(ts(4), green_30m)
    engine.on_5m_close(ts(5), Candle(open=2012, high=2013, low=2005, close=2008))  # retest 30m
    red_5m = Candle(open=2007, high=2008, low=2002, close=2003)
    green_5m = Candle(open=2004, high=2015, low=2004, close=2010)
    engine.on_5m_close(ts(6), red_5m)
    first_signals = engine.on_5m_close(ts(7), green_5m)
    assert len(first_signals) == 1

    # a fresh 30M BUY zone forms further up (continuation, spec section 20)
    red_30m_2 = Candle(open=2025, high=2026, low=2020, close=2021)
    green_30m_2 = Candle(open=2027, high=2035, low=2027, close=2030)
    engine.on_30m_close(ts(8), red_30m_2)
    confirmed2 = engine.on_30m_close(ts(9), green_30m_2)
    assert len(confirmed2) == 1

    engine.on_5m_close(ts(10), Candle(open=2032, high=2033, low=2022, close=2028))  # retest
    red_5m_2 = Candle(open=2019, high=2020, low=2015, close=2016)
    green_5m_2 = Candle(open=2017, high=2028, low=2017, close=2025)
    engine.on_5m_close(ts(11), red_5m_2)
    second_signals = engine.on_5m_close(ts(12), green_5m_2)

    assert len(second_signals) == 1
    assert second_signals[0].is_continuation
    assert second_signals[0].parent_4h_zone_id == first_signals[0].parent_4h_zone_id
