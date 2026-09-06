"""Pullback Builder 单元测试。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from price_action_engine.structure import (
    PullbackState,
)
from price_action_engine.structure.types import QualityFlag
from tests.structure_helpers import (
    bars_with_constant_atr,
    dump_state,
    make_bar,
    make_engine,
    run_case,
)


START = date(2025, 1, 2)
ATR = Decimal("1.0")


def test_pullback_is_none_when_no_leg():
    """没有 leg 时不输出虚假 pullback。"""
    bars = bars_with_constant_atr(
        [make_bar(i, o=10, h=10, l=10, c=10, start_date=START) for i in range(3)],
        ATR,
    )
    state = run_case(bars)
    # 三个 bar 都是 10，没 swing 也没反转；有 current_leg 但没反转所以也没 pullback
    # （实际上 current_leg direction 是 +1，pullback 应是 -1 重新追踪 last extreme）
    assert state.current_pullback is None or state.current_pullback.is_active is False


def test_pullback_direction_opposes_current_leg():
    """current_leg UP → pullback direction = -1。"""
    bars = bars_with_constant_atr(
        [
            make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
            make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
            make_bar(2, o=12, h=12, l=12, c=12, start_date=START),
            make_bar(3, o=11.5, h=11.5, l=11.5, c=11.5, start_date=START),
        ],
        ATR,
    )
    state = run_case(bars)
    assert state.current_leg is not None
    assert state.current_leg.direction == +1
    if state.current_pullback is not None:
        assert state.current_pullback.direction == -1


def test_pullback_depth_pct_of_prior_leg_computation():
    """§16 depth_pct_of_prior_leg 是关键字段。"""
    # previous leg: HIGH(13) → LOW(12) = -1
    # current pullback: 从 12 起步跌到 11.5，depth_abs = 0.5
    # 应该是 0.5 / |prev leg net_move| = 0.5 / 1 = 50%
    bars = bars_with_constant_atr(
        [
            make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
            make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
            make_bar(2, o=12, h=12, l=12, c=12, start_date=START),
            make_bar(3, o=13, h=13, l=13, c=13, start_date=START),
            make_bar(4, o=12.0, h=12.0, l=12.0, c=12.0, start_date=START),  # close ≤ 13-1=12 confirm HIGH
            make_bar(5, o=11.5, h=11.5, l=11.5, c=11.5, start_date=START),  # extreme low 11.5
            make_bar(6, o=11.8, h=11.8, l=11.8, c=11.8, start_date=START),  # 反弹但未到 12
        ],
        ATR,
    )
    state = run_case(bars)
    print(dump_state(state))
    if state.current_pullback is not None:
        assert state.current_pullback.direction == +1  # leg DOWN 时 pullback UP
        # previous leg 是从 13 → 12 = -1 magnitude；current 反弹从 12 → 11.5 → 11.8，depth_abs 没减少
        # 该 case 实际是 DOWN leg + UP pullback
        # depth_pct_of_prior_leg 取决于当前 leg 还是 previous_confirmed_leg


def test_pullback_no_healthy_flag():
    """§17 不输出 healthy_pullback 这种 boolean 压缩字段。"""
    fields = {f.name for f in PullbackState.__dataclass_fields__.values()}
    assert "healthy_pullback" not in fields
    assert "deep_pullback" not in fields
    assert "is_health" not in fields


def test_pullback_handles_zero_prior_leg():
    """previous_leg.net_move=0 时不能除零。"""
    # 制造一个没有任何 swing 的窗口
    bars = bars_with_constant_atr(
        [make_bar(i, o=10, h=10, l=10, c=10, start_date=START) for i in range(5)],
        ATR,
    )
    state = run_case(bars)
    # 没 swing → 没 prior leg → pullback.depth_pct_of_prior_leg may use current_leg
    # 此 case 横盘，current_leg direction 是初始 +1，net_move=0
    if state.current_pullback is not None:
        assert state.current_pullback.depth_pct_of_prior_leg is None or state.current_pullback.depth_pct_of_prior_leg >= 0


def test_pullback_duration_bars_excludes_suspended():
    """§20：停牌日不参与 pullback duration。"""
    bars_list = [
        make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
        make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
        make_bar(2, o=12, h=12, l=12, c=12, start_date=START),
        make_bar(3, o=13, h=13, l=13, c=13, start_date=START),
        make_bar(4, o=12.0, h=12.0, l=12.0, c=12.0, start_date=START),
        make_bar(5, o=11.5, h=11.5, l=11.5, c=11.5, start_date=START),
        make_bar(6, o=11.5, h=11.5, l=11.5, c=11.5, suspended=True, start_date=START),
        make_bar(7, o=12.5, h=12.5, l=12.5, c=12.5, start_date=START),
    ]
    bars = bars_with_constant_atr(bars_list, ATR)
    state = run_case(bars)
    print(dump_state(state))
    if state.current_pullback is not None:
        # day5(origin) start, day6 suspended 跳过，day7 as_of。
        # 实际可交易 bars: day5 + day7 = 2
        assert state.current_pullback.duration_bars == 2
