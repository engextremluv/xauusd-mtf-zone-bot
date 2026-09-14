"""EA-style configurable inputs for the strategy.

Mirrors section 35 ("CONFIGURABLE EA INPUTS") of the strategy spec.
Every numeric rule in the spec is exposed here rather than hard-coded,
so the backtest can be re-run under different assumptions without
touching strategy code.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InstrumentSpec:
    """Broker contract specification for the traded symbol.

    Defaults are typical retail XAUUSD CFD terms (100oz/lot, 0.01 tick).
    These MUST be replaced with the real broker's symbol specification
    before results are trusted -- risk sizing is only as correct as
    these numbers (see spec section 18 and the "Risk calculation"
    warning in the spec's closing notes).
    """

    symbol: str = "XAUUSD"
    contract_size: float = 100.0  # troy ounces per 1.0 lot
    tick_size: float = 0.01  # smallest price increment ("point")
    tick_value_per_lot: float = 1.0  # account-currency P&L per tick per 1.0 lot
    pip_size: float = 0.10  # 1 "pip" = 10 ticks for XAUUSD on most MT5 brokers
    min_lot: float = 0.01
    max_lot: float = 50.0
    lot_step: float = 0.01
    leverage: float = 100.0  # typical retail XAUUSD leverage (1:100)

    def margin_required(self, lots: float, price: float) -> float:
        """Margin to OPEN `lots` at `price`, given this symbol's leverage.
        Not a full margin-call/stop-out simulation -- see risk.py's
        `max_lots_by_margin` docstring for what this does and doesn't
        model.
        """
        return (lots * self.contract_size * price) / self.leverage

    def value_per_price_unit(self, lots: float) -> float:
        """Account-currency P&L for a 1.0 price-unit move at the given lot size."""
        ticks_per_unit = 1.0 / self.tick_size
        return lots * ticks_per_unit * self.tick_value_per_lot

    def pnl(self, lots: float, price_distance: float) -> float:
        """Account-currency P&L for moving `price_distance` at `lots` size."""
        return self.value_per_price_unit(lots) * price_distance

    def round_lots(self, lots: float) -> float:
        """Round DOWN to the nearest lot step at/above min_lot; below
        min_lot there is no valid size, so this returns 0.0 (never rounds
        a too-small size UP to min_lot -- that would silently open a
        bigger position than the caller asked for).
        """
        import math

        if lots < self.min_lot:
            return 0.0
        steps = math.floor((lots - self.min_lot) / self.lot_step + 1e-9) + 1
        rounded = self.min_lot + (steps - 1) * self.lot_step
        rounded = max(self.min_lot, min(self.max_lot, rounded))
        # avoid float noise, e.g. 0.049999999
        decimals = max(0, -int(round(math.log10(self.lot_step))))
        return round(rounded, decimals)


@dataclass
class SLStep:
    trigger_pips: float  # profit (pips) that arms this step
    adjustment_pips: float  # incremental move of SL toward entry, in pips


@dataclass
class StrategyConfig:
    ea_name: str = "XAUUSD-MTF-Zone-EA"
    magic_number: int = 990001

    # --- Risk engine (spec sections 18, 25) ---
    risk_percent: float = 10.0  # max simultaneous aggregate risk, % of equity
    daily_drawdown_limit_percent: float = 20.0  # stop new trades for the day past this

    # --- Margin sanity cap (NOT in the spec -- see risk.py docstring) ---
    # The spec sizes positions purely from $ risk-at-SL, which is
    # unbounded when the SL distance happens to be very tight (a common
    # occurrence with real candle-based stops). Without a margin check, a
    # tiny SL distance can imply an enormous, unfundable lot size that
    # still nominally "only" risks 10%. This caps a NEW position's own
    # required margin as a fraction of current equity; it does NOT track
    # cumulative margin usage across multiple simultaneously open
    # positions, so treat it as a sanity backstop, not a full margin
    # simulation.
    max_margin_usage_percent: float = 50.0

    # --- Initial stop loss (spec section 17) ---
    initial_sl_buffer_pips_min: float = 0.5
    initial_sl_buffer_pips_max: float = 1.0

    # --- Entry fill behaviour (spec sections 15/16) ---
    # The spec's literal text: prefer a fill at the "structural entry
    # level" (the setup/"X" candle's far wick), and only chase the
    # market if price "never returns" to it. True means: after a 5M
    # confirmation, place a limit-style order at that level and wait up
    # to `pullback_max_wait_bars` further 5-minute bars for price to
    # touch it; if it never does, the trade is skipped entirely (no
    # chase). False (the original simplification) enters at market
    # immediately after the confirmation candle closes, every time.
    require_pullback_entry: bool = True
    pullback_max_wait_bars: int = 12  # 12 x 5min = 1 hour

    # --- Progressive SL / break-even (spec section 21) ---
    sl_steps: list[SLStep] = field(
        default_factory=lambda: [
            SLStep(trigger_pips=5, adjustment_pips=0.2),
            SLStep(trigger_pips=10, adjustment_pips=0.2),
            SLStep(trigger_pips=15, adjustment_pips=0.2),
        ]
    )
    initial_sl_distance_pips: float = 2.0
    break_even_trigger_pips: float = 20.0

    # --- Partial take-profit at roadblock (spec sections 22-24) ---
    roadblock_close_percent: float = 80.0
    runner_percent: float = 20.0

    # --- Feature toggles (spec section 35) ---
    enable_additional_entries: bool = True
    enable_historical_zones: bool = True
    allow_buy_in_range: bool = True
    allow_sell_in_range: bool = True

    # --- Market structure detection (not fully specified numerically by
    #     the doc -- see market_structure.py docstring for the assumption
    #     this makes explicit and configurable) ---
    swing_lookback: int = 2  # bars either side for a fractal swing point
    swing_lookahead_confirm: int = 2

    # --- Zone bookkeeping ---
    max_historical_zones_per_direction: int = 25

    instrument: InstrumentSpec = field(default_factory=InstrumentSpec)

    def sl_distance_pips(self, profit_pips: float, initial_distance_pips: float | None = None) -> float:
        """Given current profit in pips, return the SL distance-from-entry
        in pips that the progressive SL ladder + break-even trigger implies.

        `initial_distance_pips` should be the position's OWN actual initial
        SL distance (section 17's candle-derived SL, which varies per
        trade) -- the ladder's adjustment_pips are cumulative reductions
        *from that starting distance*, not from a hard-coded constant.
        Falls back to `self.initial_sl_distance_pips` (the spec's
        illustrative "2 pips" example) only if the caller doesn't know the
        position's real initial distance.

        Distance is always measured from entry toward the trade's profit
        side; 0.0 means break-even. The ladder only ever tightens the SL
        (spec section 21: "must never move the SL farther away from entry").
        """
        base = self.initial_sl_distance_pips if initial_distance_pips is None else initial_distance_pips
        if profit_pips >= self.break_even_trigger_pips:
            return 0.0
        distance = base
        for step in sorted(self.sl_steps, key=lambda s: s.trigger_pips):
            if profit_pips >= step.trigger_pips:
                distance -= step.adjustment_pips
        return max(distance, 0.0)
