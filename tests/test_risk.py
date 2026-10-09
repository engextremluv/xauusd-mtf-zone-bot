import pytest

from xauusd_bot.config import InstrumentSpec
from xauusd_bot.risk import max_lots_for_budget, risk_per_lot


def make_instrument():
    # 1 lot = 100oz, tick=0.01, tick_value=$1/lot/tick -> $1 move = $100/lot
    return InstrumentSpec(contract_size=100, tick_size=0.01, tick_value_per_lot=1.0,
                           min_lot=0.01, max_lot=50, lot_step=0.01)


def test_risk_per_lot_uses_real_contract_spec_not_a_flat_assumption():
    inst = make_instrument()
    # stop distance of $2.00 -> 200 ticks -> $200 per lot
    assert risk_per_lot(inst, 2.00) == pytest.approx(200.0)
    # stop distance of $0.50 -> $50 per lot
    assert risk_per_lot(inst, 0.50) == pytest.approx(50.0)


def test_max_lots_for_budget_basic_example():
    inst = make_instrument()
    # choose a stop distance such that 1.0 lot risks exactly $2
    sl_distance = 2.0 / risk_per_lot(inst, 1.0)
    lots = max_lots_for_budget(inst, sl_distance, risk_budget=10.0)
    assert lots == pytest.approx(5.0, abs=0.01)


def test_max_lots_never_exceeds_budget_after_rounding():
    inst = make_instrument()
    sl_distance = 0.37  # awkward number likely to hit rounding edge cases
    budget = 17.0
    lots = max_lots_for_budget(inst, sl_distance, budget)
    actual_risk = inst.pnl(lots, sl_distance)
    assert actual_risk <= budget + 1e-9


def test_max_lots_below_min_lot_returns_zero():
    inst = make_instrument()
    lots = max_lots_for_budget(inst, sl_distance_price=100.0, risk_budget=0.01)
    assert lots == 0.0


def test_round_lots_never_rounds_up_to_min_lot():
    inst = make_instrument()
    # a size smaller than min_lot has no valid lot size -- must be 0, not
    # silently bumped up to min_lot (that would open a bigger position
    # than intended).
    assert inst.round_lots(0.005) == 0.0
    assert inst.round_lots(0.0) == 0.0
    assert inst.round_lots(0.015) == pytest.approx(0.01)
