import pytest

from xauusd_bot.config import InstrumentSpec, StrategyConfig
from xauusd_bot.risk import RiskManager, max_lots_for_budget, risk_per_lot


def make_instrument():
    # 1 lot = 100oz, tick=0.01, tick_value=$1/lot/tick -> $1 move = $100/lot
    return InstrumentSpec(contract_size=100, tick_size=0.01, tick_value_per_lot=1.0,
                           min_lot=0.01, max_lot=100, lot_step=0.01)


def test_risk_per_lot_uses_real_contract_spec_not_a_flat_assumption():
    inst = make_instrument()
    # SL distance of $2.00 -> 200 ticks -> $200 per lot
    assert risk_per_lot(inst, 2.00) == pytest.approx(200.0)
    # SL distance of $0.50 -> $50 per lot
    assert risk_per_lot(inst, 0.50) == pytest.approx(50.0)


def test_max_lots_for_budget_matches_spec_example():
    # spec section 18 example: $100 account, 10% risk = $10 budget.
    # "If one position with its calculated SL would risk $2, EA can open
    # 5 positions x $2 = $10." Equivalent single-position framing:
    # a $10 budget with $2-per-lot risk should allow 5.0 lots (or the
    # position-count analogy: 5 x smallest lot unit at $2/lot-equivalent).
    inst = InstrumentSpec(contract_size=100, tick_size=0.01, tick_value_per_lot=1.0,
                           min_lot=0.01, max_lot=100, lot_step=0.01)
    # choose SL distance such that 1.0 lot risks exactly $2
    sl_distance = 2.0 / risk_per_lot(inst, 1.0)  # price distance for $2/lot
    lots = max_lots_for_budget(inst, sl_distance, risk_budget=10.0)
    assert lots == pytest.approx(5.0, abs=0.01)


def test_max_lots_never_exceeds_budget_after_rounding():
    inst = InstrumentSpec(contract_size=100, tick_size=0.01, tick_value_per_lot=1.0,
                           min_lot=0.01, max_lot=100, lot_step=0.01)
    sl_distance = 0.37  # awkward number likely to hit rounding edge cases
    budget = 17.0
    lots = max_lots_for_budget(inst, sl_distance, budget)
    actual_risk = inst.pnl(lots, sl_distance)
    assert actual_risk <= budget + 1e-9


def test_max_lots_below_min_lot_returns_zero():
    inst = make_instrument()
    lots = max_lots_for_budget(inst, sl_distance_price=100.0, risk_budget=0.01)
    assert lots == 0.0


def test_aggregate_risk_cap_enforced_across_positions():
    config = StrategyConfig()
    config.risk_percent = 10.0
    rm = RiskManager(config=config, starting_equity=100.0)
    budget = rm.available_risk_budget(equity=100.0)
    assert budget == pytest.approx(10.0)

    rm.register_open_risk("pos1", 6.0)
    assert rm.available_risk_budget(equity=100.0) == pytest.approx(4.0)

    rm.register_open_risk("pos2", 4.0)
    assert rm.available_risk_budget(equity=100.0) == pytest.approx(0.0)

    rm.release_risk("pos1")
    assert rm.available_risk_budget(equity=100.0) == pytest.approx(6.0)


def test_daily_drawdown_halts_trading():
    config = StrategyConfig()
    config.daily_drawdown_limit_percent = 20.0
    rm = RiskManager(config=config, starting_equity=100.0)
    rm.roll_day(trading_day="2024-01-01", equity=100.0)

    # spec 25 example: existing realised loss -$12, still allowed to trade
    assert not rm.check_daily_drawdown(realised_pl_today=-12.0, floating_pl=0.0)

    # new trade loses $10 more -> total $22 loss >= $20 (20% of $100) -> halt
    assert rm.check_daily_drawdown(realised_pl_today=-22.0, floating_pl=0.0)
    assert rm.trading_halted_today


def test_daily_drawdown_resets_on_new_day():
    config = StrategyConfig()
    rm = RiskManager(config=config, starting_equity=100.0)
    rm.roll_day(trading_day="2024-01-01", equity=100.0)
    rm.check_daily_drawdown(realised_pl_today=-25.0, floating_pl=0.0)
    assert rm.trading_halted_today

    rm.roll_day(trading_day="2024-01-02", equity=75.0)
    assert not rm.trading_halted_today
    # new day's budget is now based on $75 starting equity
    assert not rm.check_daily_drawdown(realised_pl_today=-10.0, floating_pl=0.0)
