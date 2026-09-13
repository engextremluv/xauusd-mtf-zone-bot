"""Risk engine: position sizing, aggregate open risk, daily drawdown halt.

Spec sections 18, 19, 25. The critical rule this module exists to get
right: risk in dollars is derived from the *actual* SL distance and the
instrument's real contract/tick specification -- never from an assumed
"N lots = $X risk" shortcut (see spec's closing warning on risk
calculation).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import InstrumentSpec, StrategyConfig


def risk_per_lot(instrument: InstrumentSpec, sl_distance_price: float) -> float:
    """Dollar loss for exactly 1.0 lot if price moves `sl_distance_price`
    (an absolute, positive price distance) against the position.
    """
    if sl_distance_price <= 0:
        raise ValueError("sl_distance_price must be positive")
    return instrument.pnl(lots=1.0, price_distance=sl_distance_price)


def max_lots_for_budget(
    instrument: InstrumentSpec, sl_distance_price: float, risk_budget: float
) -> float:
    """Largest lot size (rounded down to the broker's lot step) whose
    monetary loss at the given SL distance does not exceed risk_budget.
    """
    if risk_budget <= 0 or sl_distance_price <= 0:
        return 0.0
    per_lot = risk_per_lot(instrument, sl_distance_price)
    raw_lots = risk_budget / per_lot
    if raw_lots < instrument.min_lot:
        return 0.0
    # round DOWN to the lot step so we never exceed the budget
    import math

    steps = math.floor((raw_lots - instrument.min_lot) / instrument.lot_step + 1e-9)
    lots = instrument.min_lot + steps * instrument.lot_step
    lots = min(lots, instrument.max_lot)
    decimals = max(0, -int(round(math.log10(instrument.lot_step))))
    lots = round(lots, decimals)
    # guard against rounding pushing us back over budget (spec 30: "never
    # exceed the available risk budget because of rounding")
    while lots > 0 and instrument.pnl(lots, sl_distance_price) > risk_budget + 1e-9:
        lots = round(lots - instrument.lot_step, decimals)
    return max(lots, 0.0)


def max_lots_by_margin(
    instrument: InstrumentSpec, price: float, equity: float, max_margin_usage_percent: float
) -> float:
    """Sanity backstop (not part of the spec): cap a single new position's
    lot size so its OWN required margin doesn't exceed a fraction of
    current equity. This exists because pure $-risk sizing (spec 18) is
    unbounded when the SL happens to sit very close to entry -- see
    StrategyConfig.max_margin_usage_percent for the full explanation of
    why this is needed and what it does not model (no cumulative margin
    tracking across concurrently open positions).
    """
    if price <= 0 or equity <= 0:
        return 0.0
    margin_budget = equity * (max_margin_usage_percent / 100.0)
    margin_per_lot = instrument.margin_required(1.0, price)
    if margin_per_lot <= 0:
        return instrument.max_lot
    return margin_budget / margin_per_lot


@dataclass
class OpenRiskLot:
    position_id: str
    risk_amount: float


@dataclass
class RiskManager:
    """Tracks aggregate simultaneous open risk (spec 18/19) and the daily
    drawdown kill-switch (spec 25) across the whole account/backtest.
    """

    config: StrategyConfig
    starting_equity: float
    _open_risk: dict[str, float] = field(default_factory=dict)
    _daily_start_equity: float = 0.0
    _current_day: object = None
    _trading_halted_today: bool = False

    def __post_init__(self) -> None:
        self._daily_start_equity = self.starting_equity

    # --- aggregate simultaneous risk (spec 18/19) ---

    def committed_risk(self) -> float:
        return sum(self._open_risk.values())

    def available_risk_budget(self, equity: float) -> float:
        cap = equity * (self.config.risk_percent / 100.0)
        return max(cap - self.committed_risk(), 0.0)

    def register_open_risk(self, position_id: str, risk_amount: float) -> None:
        self._open_risk[position_id] = risk_amount

    def update_open_risk(self, position_id: str, risk_amount: float) -> None:
        if position_id in self._open_risk:
            self._open_risk[position_id] = risk_amount

    def release_risk(self, position_id: str) -> None:
        self._open_risk.pop(position_id, None)

    # --- daily drawdown halt (spec 25) ---

    def roll_day(self, trading_day, equity: float) -> None:
        """Call once when the current bar enters a new trading day."""
        if trading_day != self._current_day:
            self._current_day = trading_day
            self._daily_start_equity = equity
            self._trading_halted_today = False

    def daily_loss(self, realised_pl_today: float, floating_pl: float) -> float:
        """Realised + floating P/L today, as a loss (positive = losing)."""
        return -(realised_pl_today + floating_pl)

    def check_daily_drawdown(self, realised_pl_today: float, floating_pl: float) -> bool:
        """Returns True (and latches) if the daily drawdown limit has been
        breached, using: loss / starting-of-day equity >= limit%.
        """
        if self._daily_start_equity <= 0:
            return self._trading_halted_today
        loss = self.daily_loss(realised_pl_today, floating_pl)
        limit = self._daily_start_equity * (self.config.daily_drawdown_limit_percent / 100.0)
        if loss >= limit:
            self._trading_halted_today = True
        return self._trading_halted_today

    @property
    def trading_halted_today(self) -> bool:
        return self._trading_halted_today
