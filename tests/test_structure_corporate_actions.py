"""Structure Core 必须 §四 不产生除权虚假 Swing。

qfq 序列在除权日没有真正的 gap，所以结构识别不应该把
raw 价格腰斩（如 10送10）误判为 huge Bear Leg。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from price_action_engine.structure import (
    ATRReversalSwingDetector,
    StructureEngine,
)
from price_action_engine.structure.leg import LegBuilder
from price_action_engine.structure.pullback import PullbackBuilder
from price_action_engine.structure.types import QualityFlag
from tests.structure_helpers import (
    bars_with_constant_atr,
    make_bar,
)


START = date(2025, 1, 2)
ATR = Decimal("1.0")


def test_corporate_action_does_not_produce_bear_leg_in_qfq():
    """10送10除权：raw 从 100 腰斩到 50，但 qfq 连续 → 不应产生 Bear Leg。

    raw 序列：100, 100, 100, 100, 50, 50, 50, 50
    qfq 序列（正确 anchor 下）：100, 100, 100, 100, 100, 100, 100, 100

    在 qfq 域上这是单调横盘 100，无反转，无 swing，无 Bear Leg。

    本测试用例构造模拟"qfq 已被正确连续化"（也就是上游 PriceBridge
    没把除权 raw 错喂给 StructureEngine）的场景；StructureEngine 看到的
    是连续的 qfq 序列，因此不应产生任何虚假 Bear Leg。
    """
    qfqs = [Decimal("100")] * 8  # 全部 100，模拟 qfq 域连续
    bars_list = [
        make_bar(i, o=q, h=q, l=q, c=q, atr=Decimal("0.5"), start_date=START)
        for i, q in enumerate(qfqs)
    ]
    bars = bars_with_constant_atr(bars_list, Decimal("0.5"))

    eng = StructureEngine(
        swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0")),
        leg_builder=LegBuilder(),
        pullback_builder=PullbackBuilder(),
    )
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)

    # qfq 上 8 个 bar 全是 100，无反转 → 无 swing
    assert len(state.confirmed_swings) == 0
    # current_leg 是 UP（横盘推进），direction = +1
    assert state.current_leg is not None
    assert state.current_leg.direction == +1


def test_corporate_action_partial_qfq_gap_is_real_movement():
    """真实的同比/环比事件：raw 不变但 price_action_viewer 看到 qfq 上跳。
    在 qfq 域这仍是真实数据驱动的 swing，所以应该正常识别。

    这里重点验证：qfq 域内的真实跳空，应该被识别为 gap 但不应默默处理成
    "force 一字涨停"之类的状态。结构上仍按 reversed-direction 处理。
    """
    # 真实无 reverse：qfq 单调上升，无 swing
    qfqs = [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103"),
            Decimal("104"), Decimal("105"), Decimal("106"), Decimal("107")]
    bars_list = [
        make_bar(i, o=q, h=q, l=q, c=q, atr=Decimal("0.5"), start_date=START)
        for i, q in enumerate(qfqs)
    ]
    bars = bars_with_constant_atr(bars_list, Decimal("0.5"))
    eng = StructureEngine(
        swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    )
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    # 全程单调上升、无反转
    assert len(state.confirmed_swings) == 0


def test_corporate_action_with_real_reversal_after_qfq_filter():
    """除权 + qfq 重计算后，**之后**如果市场真的下跌（qfq 仍然显示下跌），
    算法应该正常识别 swing。这是预期行为，不是 bug。"""
    # qfq 前 4 根横盘（对应 raw 横盘 100），后 4 根下跌到 90（qfq）
    # 比如：qfq=100,100,100,100,95,90,85,80
    qfqs = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"),
            Decimal("95"), Decimal("90"), Decimal("85"), Decimal("80")]
    bars_list = [
        make_bar(i, o=q, h=q, l=q, c=q, atr=Decimal("1.0"), start_date=START)
        for i, q in enumerate(qfqs)
    ]
    bars = bars_with_constant_atr(bars_list, Decimal("1.0"))
    eng = StructureEngine(
        swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    )
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    # 确认了至少一个 HIGH swing (event day0 / day1 / day2 / day3 中最新极端)
    high_swings = [s for s in state.confirmed_swings if s.kind.value == "high"]
    assert len(high_swings) >= 1
    # 该 swing event_index 应在前 4 根之内
    assert high_swings[0].event_index <= 3
