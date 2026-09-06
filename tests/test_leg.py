"""Leg Builder 单元测试。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from price_action_engine.structure import (
    ATRReversalSwingDetector,
    LegBuilder,
    LegState,
    StructureEngine,
    SwingKind,
)
from tests.structure_helpers import (
    bars_with_constant_atr,
    dump_state,
    make_bar,
    make_engine,
    run_case,
)


START = date(2025, 1, 2)
ATR = Decimal("1.0")


def test_legs_returns_none_when_no_swings_yet():
    """还没有任何 confirmed swing 时，previous_leg 是 None 但 current_leg 仍然存在。"""
    bars = bars_with_constant_atr(
        [make_bar(i, o=10 + i, h=10 + i, l=10 + i, c=10 + i, start_date=START) for i in range(5)],
        ATR,
    )
    eng = make_engine()
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    assert state.previous_confirmed_leg is None
    assert state.current_leg is not None
    assert state.current_leg.direction == +1
    assert state.current_leg.is_confirmed is False
    assert state.current_leg.is_current is True


def test_previous_leg_direction_reflects_swing_pair():
    bars = bars_with_constant_atr(
        [make_bar(i, o=10, h=10, l=10, c=10, start_date=START) for i in range(8)],
        ATR,
    )
    bars = bars_with_constant_atr(
        [
            make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
            make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
            make_bar(2, o=12, h=12, l=12, c=12, start_date=START),
            make_bar(3, o=13, h=13, l=13, c=13, start_date=START),
            make_bar(4, o=12.5, h=12.5, l=12.5, c=12.5, start_date=START),
            make_bar(5, o=12.0, h=12.0, l=12.0, c=12.0, start_date=START),
            make_bar(6, o=12.5, h=12.5, l=12.5, c=12.5, start_date=START),
            make_bar(7, o=14, h=14, l=14, c=14, start_date=START),
        ],
        ATR,
    )
    state = run_case(bars)
    assert state.previous_confirmed_leg is not None
    assert state.previous_confirmed_leg.direction == -1
    # signed net_move from 13 → 12 = -1.0 (DOWN leg)
    assert abs(state.previous_confirmed_leg.net_move) == Decimal("1.0")
    # return_pct = -1 / 13 ≈ -0.077
    assert state.previous_confirmed_leg.return_pct < 0


def test_developing_leg_extreme_tracks_highs():
    """current leg 的 extreme_price 应该跟踪最高点（UP 方向）。"""
    bars = bars_with_constant_atr(
        [
            make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
            make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
            make_bar(2, o=12, h=12, l=12, c=12, start_date=START),
            make_bar(3, o=13, h=13, l=13, c=13, start_date=START),
        ],
        ATR,
    )
    state = run_case(bars)
    assert state.current_leg.direction == +1
    assert state.current_leg.extreme_price.analysis == Decimal("13")
    assert state.current_leg.is_confirmed is False


def test_leg_duration_counts_tradeable_bars_only():
    """§20：停牌日不计入 duration_bars（active_duration_bars）。"""
    bars_list = [
        make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
        make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
        make_bar(2, o=11, h=11, l=11, c=11, suspended=True, start_date=START),
        make_bar(3, o=12, h=12, l=12, c=12, start_date=START),
        make_bar(4, o=13, h=13, l=13, c=13, start_date=START),
    ]
    bars = bars_with_constant_atr(bars_list, ATR)
    state = run_case(bars)
    assert state.current_leg.duration_bars == 4  # bar 0, 1, 3, 4 — suspended 不计


def test_leg_atr_distance_uses_local_atr():
    """atr_distance = net_move / ATR(start_bar)。"""
    bars = bars_with_constant_atr(
        [
            make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
            make_bar(1, o=12, h=12, l=12, c=12, start_date=START),
            make_bar(2, o=14, h=14, l=14, c=14, start_date=START),
        ],
        ATR,
    )
    state = run_case(bars)
    assert state.current_leg is not None
    # net_move = 14 - 10 = 4, ATR = 1 → atr_distance = 4
    assert state.current_leg.atr_distance is not None
    assert abs(state.current_leg.atr_distance - Decimal("4")) < Decimal("0.001")


def test_leg_no_strength_score_emitted():
    """§14 LegState 不应存在 leg_strength 字段，避免主观评分。"""
    legs_fields = {f.name for f in LegState.__dataclass_fields__.values()}
    assert "leg_strength" not in legs_fields
    assert "score" not in legs_fields
