import pandas as pd
import pytest

from xauusd_bot.candles import Candle
from xauusd_bot.config import InstrumentSpec, StrategyConfig
from xauusd_bot.risk import RiskManager
from xauusd_bot.signals import EntrySignal
from xauusd_bot.trade_manager import TradeManager
from xauusd_bot.zones import Direction, Zone, ZoneManager

T0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")


def ts(i, unit="m"):
    delta = pd.Timedelta(minutes=5 * i) if unit == "m" else pd.Timedelta(hours=4 * i)
    return T0 + delta


def make_config():
    cfg = StrategyConfig()
    cfg.instrument = InstrumentSpec(
        contract_size=100, tick_size=0.01, tick_value_per_lot=1.0,
        min_lot=0.01, max_lot=100, lot_step=0.01,
    )
    cfg.risk_percent = 10.0
    cfg.initial_sl_buffer_pips_min = 0.5
    return cfg


def make_signal(direction=Direction.BUY, setup_high=2000.0, setup_low=1998.0):
    setup_candle = Candle(open=1999, high=setup_high, low=setup_low, close=1998.5)
    zone = Zone(
        id=1, timeframe="5min", direction=direction, high=setup_high, low=setup_low,
        setup_candle=setup_candle, setup_timestamp=T0, confirm_timestamp=T0,
    )
    return EntrySignal(direction=direction, timestamp=T0, setup_5m_zone=zone,
                        parent_4h_zone_id=99, is_continuation=False)


def test_entry_sizing_and_initial_sl_from_setup_candle():
    cfg = make_config()
    rm = RiskManager(config=cfg, starting_equity=1000.0)
    tm = TradeManager(config=cfg, risk_manager=rm)
    signal = make_signal(setup_high=2000.0, setup_low=1998.0)

    pos = tm.try_open(signal, timestamp=T0, entry_price=2001.0, equity=1000.0)
    assert pos is not None
    # BUY SL = setup_low - buffer_pips*pip_size = 1998 - 0.5*0.10 = 1997.95
    assert pos.sl == pytest.approx(1997.95)
    # risk must not exceed the 10% budget ($100)
    assert rm.committed_risk() <= 100.0 + 1e-6
    assert pos.lots >= cfg.instrument.min_lot


def test_progressive_sl_only_tightens_never_loosens():
    cfg = make_config()
    rm = RiskManager(config=cfg, starting_equity=1000.0)
    tm = TradeManager(config=cfg, risk_manager=rm)
    signal = make_signal(setup_high=2000.0, setup_low=1998.0)
    pos = tm.try_open(signal, timestamp=T0, entry_price=2001.0, equity=1000.0)
    initial_sl = pos.sl

    zm_30m = ZoneManager(timeframe="30min")
    zm_4h = ZoneManager(timeframe="4h")

    # price rallies +25 pips (pip=0.10 -> 2.5 price) -> should hit break-even
    profit_bar = Candle(open=2001, high=2003.5, low=2001, close=2003.5)
    tm.on_5m_bar(ts(1), profit_bar, zm_30m, zm_4h)
    assert pos.sl > initial_sl
    assert pos.sl == pytest.approx(pos.entry_price)  # break-even at +20 pips

    sl_after_be = pos.sl
    # price pulls back; SL must never loosen back down
    pullback_bar = Candle(open=2003, high=2003, low=2001.5, close=2002)
    tm.on_5m_bar(ts(2), pullback_bar, zm_30m, zm_4h)
    assert pos.sl == pytest.approx(sl_after_be)


def test_sl_hit_closes_full_remaining_position():
    cfg = make_config()
    rm = RiskManager(config=cfg, starting_equity=1000.0)
    tm = TradeManager(config=cfg, risk_manager=rm)
    signal = make_signal(setup_high=2000.0, setup_low=1998.0)
    pos = tm.try_open(signal, timestamp=T0, entry_price=2001.0, equity=1000.0)

    zm_30m = ZoneManager(timeframe="30min")
    zm_4h = ZoneManager(timeframe="4h")
    losing_bar = Candle(open=2001, high=2001, low=1995, close=1996)  # crosses SL 1997.95
    tm.on_5m_bar(ts(1), losing_bar, zm_30m, zm_4h)

    assert pos.closed
    assert pos.close_reason == "sl"
    assert pos.realized_pnl < 0
    assert rm.committed_risk() == pytest.approx(0.0)


def test_roadblock_partial_close_80_20_by_volume():
    cfg = make_config()
    rm = RiskManager(config=cfg, starting_equity=100000.0)  # large equity -> plenty of lots
    tm = TradeManager(config=cfg, risk_manager=rm)
    signal = make_signal(setup_high=2000.0, setup_low=1998.0)
    pos = tm.try_open(signal, timestamp=T0, entry_price=2001.0, equity=100000.0)
    total_lots = pos.lots
    assert total_lots > 0

    zm_30m = ZoneManager(timeframe="30min")
    zm_4h = ZoneManager(timeframe="4h")

    # build a confirmed opposite (SELL) 30M zone AFTER entry time -> becomes roadblock
    green_30m = Candle(open=2010, high=2020, low=2010, close=2018)
    red_30m = Candle(open=2017, high=2018, low=2005, close=2006)  # closes below green.low(2010)
    zm_30m.on_new_candle(ts(1), green_30m)
    zm_30m.on_new_candle(ts(2), red_30m)
    roadblock_zone = zm_30m.active_zones()[0]
    assert roadblock_zone.direction is Direction.SELL
    roadblock_level = roadblock_zone.low  # 2010, price approaches from below on a BUY

    touch_bar = Candle(open=2005, high=2011, low=2004, close=2009)
    tm.on_5m_bar(ts(3), touch_bar, zm_30m, zm_4h)

    assert pos.roadblock_partial_done
    assert pos.closed_lots == pytest.approx(total_lots * 0.8, rel=1e-2)
    assert pos.remaining_lots == pytest.approx(total_lots * 0.2, rel=1e-2)
    assert not pos.closed  # runner still open


def test_final_tp_closes_runner_after_roadblock():
    cfg = make_config()
    rm = RiskManager(config=cfg, starting_equity=100000.0)
    tm = TradeManager(config=cfg, risk_manager=rm)
    signal = make_signal(setup_high=2000.0, setup_low=1998.0)
    pos = tm.try_open(signal, timestamp=T0, entry_price=2001.0, equity=100000.0)

    zm_30m = ZoneManager(timeframe="30min")
    zm_4h = ZoneManager(timeframe="4h")

    green_30m = Candle(open=2010, high=2020, low=2010, close=2018)
    red_30m = Candle(open=2017, high=2018, low=2005, close=2006)
    zm_30m.on_new_candle(ts(1), green_30m)
    zm_30m.on_new_candle(ts(2), red_30m)
    touch_bar = Candle(open=2005, high=2011, low=2004, close=2009)
    tm.on_5m_bar(ts(3), touch_bar, zm_30m, zm_4h)
    assert pos.roadblock_partial_done and not pos.closed

    # a confirmed opposite (SELL) 4H zone AFTER entry -> final TP target
    green_4h = Candle(open=2030, high=2050, low=2030, close=2045)
    red_4h = Candle(open=2044, high=2045, low=2015, close=2016)
    zm_4h.on_new_candle(ts(4, unit="h"), green_4h)
    zm_4h.on_new_candle(ts(5, unit="h"), red_4h)
    tp_zone = zm_4h.active_zones()[0]
    tp_level = tp_zone.low

    tp_touch_bar = Candle(open=2020, high=tp_level + 1, low=2018, close=2019)
    tm.on_5m_bar(ts(6), tp_touch_bar, zm_30m, zm_4h)

    assert pos.closed
    assert pos.close_reason == "final_tp"
