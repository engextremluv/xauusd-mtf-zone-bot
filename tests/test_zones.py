import pandas as pd

from xauusd_bot.candles import Candle
from xauusd_bot.zones import Direction, ZoneManager

T0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")


def ts(i):
    return T0 + pd.Timedelta(hours=4 * i)


def feed(zm: ZoneManager, candles: list[Candle]):
    results = []
    for i, c in enumerate(candles):
        results.append(zm.on_new_candle(ts(i), c))
    return results


def test_immediate_buy_confirmation():
    zm = ZoneManager(timeframe="4h")
    red = Candle(open=2010, high=2012, low=2000, close=2002)  # red, setup candle
    green = Candle(open=2003, high=2020, low=2001, close=2015)  # closes above red.high(2012)
    results = feed(zm, [red, green])
    assert results[0] == []  # red alone confirms nothing yet
    assert len(results[1]) == 1
    zone = results[1][0]
    assert zone.direction is Direction.BUY
    assert zone.high == 2012 and zone.low == 2000
    assert not zone.invalidated


def test_delayed_confirmation_still_valid():
    zm = ZoneManager(timeframe="4h")
    red = Candle(open=2010, high=2012, low=2000, close=2002)
    weak_green = Candle(open=2003, high=2008, low=2001, close=2006)  # doesn't close above 2012
    strong_green = Candle(open=2006, high=2020, low=2005, close=2015)  # closes above 2012
    results = feed(zm, [red, weak_green, strong_green])
    assert results[1] == []  # weak green: still pending, not confirmed
    assert len(results[2]) == 1
    assert results[2][0].direction is Direction.BUY


def test_pending_candidate_invalidated_before_confirming():
    zm = ZoneManager(timeframe="4h")
    red = Candle(open=2010, high=2012, low=2000, close=2002)  # candidate BUY zone, low=2000
    breakdown = Candle(open=2001, high=2003, low=1990, close=1995)  # closes below 2000 -> invalid
    later_green = Candle(open=1996, high=2020, low=1994, close=2015)  # would have confirmed red
    results = feed(zm, [red, breakdown, later_green])
    # breakdown itself becomes a new pending BUY candidate too (it's bearish),
    # but the ORIGINAL red candidate must be dead: later_green should NOT
    # produce a zone anchored on the original red (2000-2012) since that
    # candidate was invalidated by `breakdown` closing below 2000.
    all_zones = zm.zones
    assert not any(z.low == 2000 and z.high == 2012 for z in all_zones)


def test_sell_confirmation_and_reversal_to_buy():
    zm = ZoneManager(timeframe="4h")
    green = Candle(open=2000, high=2012, low=1998, close=2010)  # green setup, SELL candidate
    red = Candle(open=2009, high=2011, low=1990, close=1995)  # closes below green.low(1998)
    results = feed(zm, [green, red])
    assert len(results[1]) == 1
    zone = results[1][0]
    assert zone.direction is Direction.SELL
    assert zone.low == 1998 and zone.high == 2012

    # now push a candle that closes ABOVE the zone's high (2012) -> reversal to BUY
    breakout_up = Candle(open=1996, high=2020, low=1995, close=2015)
    zm.on_new_candle(ts(2), breakout_up)
    assert zone.direction is Direction.BUY
    assert zone.reversal_count == 1
    # price range must be unchanged by the reversal
    assert zone.low == 1998 and zone.high == 2012


def test_retest_detection():
    zm = ZoneManager(timeframe="4h")
    red = Candle(open=2010, high=2012, low=2000, close=2002)
    # confirming candle entirely above the zone (low=2013 > zone.high=2012):
    # confirms (close 2016 > 2012) without itself overlapping the zone
    green = Candle(open=2013, high=2020, low=2013, close=2016)
    zm.on_new_candle(ts(0), red)
    results = zm.on_new_candle(ts(1), green)
    zone = results[0]
    assert not zone.retested  # confirming candle itself never entered the zone

    far_away = Candle(open=2020, high=2025, low=2018, close=2022)
    zm.on_new_candle(ts(2), far_away)
    assert not zone.retested

    retest_candle = Candle(open=2018, high=2019, low=2005, close=2010)  # dips into [2000,2012]
    zm.on_new_candle(ts(3), retest_candle)
    assert zone.retested


def test_multiple_concurrent_historical_zones():
    zm = ZoneManager(timeframe="4h")
    # Monday: red->green BUY zone A
    a_red = Candle(open=2010, high=2012, low=2000, close=2002)
    a_green = Candle(open=2003, high=2020, low=2001, close=2015)
    # Tuesday: green->red SELL zone B, entirely disjoint prices, formed after A confirmed
    b_green = Candle(open=2030, high=2040, low=2025, close=2038)
    b_red = Candle(open=2037, high=2039, low=2010, close=2015)  # closes below b_green.low(2025)

    feed(zm, [a_red, a_green, b_green, b_red])
    active = zm.active_zones()
    assert len(active) == 2
    buy_zones = zm.active_zones(Direction.BUY)
    sell_zones = zm.active_zones(Direction.SELL)
    assert len(buy_zones) == 1 and buy_zones[0].low == 2000 and buy_zones[0].high == 2012
    assert len(sell_zones) == 1 and sell_zones[0].low == 2025 and sell_zones[0].high == 2040


def test_wick_only_breach_does_not_confirm_or_reverse():
    zm = ZoneManager(timeframe="4h")
    red = Candle(open=2010, high=2012, low=2000, close=2002)
    # bullish candle that spikes above 2012 intrabar but closes back inside the range
    fake_confirm = Candle(open=2003, high=2020, low=2001, close=2008)
    results = feed(zm, [red, fake_confirm])
    assert results[1] == []  # no confirmation: close (2008) didn't clear 2012

    # now confirm it properly, then test wick-only reversal attempt
    real_confirm = Candle(open=2008, high=2020, low=2005, close=2015)
    results2 = zm.on_new_candle(ts(2), real_confirm)
    zone = results2[0]

    wick_spike_down = Candle(open=2005, high=2006, low=1995, close=2003)  # low<2000 but closes 2003
    zm.on_new_candle(ts(3), wick_spike_down)
    assert zone.direction is Direction.BUY  # unchanged: close (2003) stayed above zone.low(2000)
