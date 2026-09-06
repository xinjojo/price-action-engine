"""Manual structure cases —— §23 八个手工案例的可读测试。

每个 case 打印输入序列和检测出的 Swing / Leg / Pullback。
这是一个 print-based 测试，可以肉眼判断算法是否符合 Price Action 直觉。

Case A: 标准上涨 + 回调
Case B: 标准下跌 + 反弹（镜像）
Case C: 横盘噪声（不该产生假 swing）
Case D: V 反转（确认日应明显区分）
Case E: 一字涨停推进
Case F: 除权（qfq 域连续，不应有假 Bear Leg）
Case G: 停牌 placeholder 不影响结构
Case H: Outside Bar 歧义
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from price_action_engine.structure import (
    ATRReversalSwingDetector,
    LegBuilder,
    PullbackBuilder,
    StructureEngine,
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


def _print_case_header(name: str, values):
    print("=" * 70)
    print(f"Case {name}")
    print(f"  values: {values}")
    print("=" * 70)


def _build_and_dump(name, values, *, atr=ATR, threshold="1.0"):
    _print_case_header(name, values)
    bars_list = [
        make_bar(
            i, o=v, h=v, l=v, c=v, start_date=START
        ) for i, v in enumerate(values)
    ]
    # ATR 不足以触发反转，所以需要把 thr 调到至少 0.001×
    bars = bars_with_constant_atr(bars_list, atr)
    eng = StructureEngine(swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal(threshold)))
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    print(dump_state(state))
    print()
    return state


# ────────────────────────────────────────────────────────────────────
# Case A：标准上涨 + 回调
# 10, 11, 12, 13, 12.5, 12.0, 12.5, 14
# 预期：
#   - HIGH swing 约为 day3 (event)
#   - LOW swing 大约在 day5
#   - current leg 从 day5（LOW swing）向上涨到 14
#   - pullback 暂未发生或刚开始
# ────────────────────────────────────────────────────────────────────


def test_case_a_uptrend_with_pullback():
    state = _build_and_dump(
        "A: 标准上涨 + 回调",
        [Decimal("10"), Decimal("11"), Decimal("12"), Decimal("13"),
         Decimal("12.5"), Decimal("12.0"), Decimal("12.5"), Decimal("14")],
    )
    # 至少 1 个 HIGH swing
    high_swings = [s for s in state.confirmed_swings if s.kind.value == "high"]
    assert len(high_swings) >= 1
    assert high_swings[0].event_index == 3  # day3 = 13
    # 当前正在反弹
    assert state.current_leg.direction == +1


# ────────────────────────────────────────────────────────────────────
# Case B：标准下跌 + 反弹（镜像）
# 14, 13, 12, 11, 11.5, 12.0, 11.5, 10
# ────────────────────────────────────────────────────────────────────


def test_case_b_downtrend_with_pullback():
    state = _build_and_dump(
        "B: 标准下跌 + 反弹",
        [Decimal("14"), Decimal("13"), Decimal("12"), Decimal("11"),
         Decimal("11.5"), Decimal("12.0"), Decimal("11.5"), Decimal("10")],
    )
    # 至少 1 个 LOW swing
    low_swings = [s for s in state.confirmed_swings if s.kind.value == "low"]
    assert len(low_swings) >= 1
    assert low_swings[0].event_index == 3  # day3 = 11
    # 当前 leg DOWN
    assert state.current_leg.direction == -1


# ────────────────────────────────────────────────────────────────────
# Case C：横盘噪声
# thr=1.0, atr=1.0 → thr=1.0
# 任何 < 1.0 的反向不构成 swing
# ────────────────────────────────────────────────────────────────────


def test_case_c_choppy_no_swings():
    state = _build_and_dump(
        "C: 横盘噪声",
        [Decimal("10"), Decimal("10.2"), Decimal("9.8"), Decimal("10.3"),
         Decimal("9.7"), Decimal("10.1"), Decimal("10"), Decimal("9.9")],
    )
    # 横盘噪声 < thr=1.0，应无 swing
    assert len(state.confirmed_swings) == 0


# ────────────────────────────────────────────────────────────────────
# Case D：V 反转
# 单调上升 → 单调下降再强烈反转上升
# 13, 12, 11, 10, 12, 14
# 预期：HIGH@day0 confirmed@day3；LOW@day3 (10) confirmed@day4/5
# ────────────────────────────────────────────────────────────────────


def test_case_d_v_reversal_event_confirm_distinct():
    state = _build_and_dump(
        "D: V 反转",
        [Decimal("13"), Decimal("12"), Decimal("11"), Decimal("10"),
         Decimal("12"), Decimal("14")],
    )
    assert len(state.confirmed_swings) >= 2
    # HIGH@day0 至少 confirmed 在 day2 (close=11 ≤ 13-2=11 boundary)
    high = [s for s in state.confirmed_swings if s.kind.value == "high"][0]
    low = [s for s in state.confirmed_swings if s.kind.value == "low"][0]
    assert high.event_index == 0
    assert high.confirmed_index is not None and high.confirmed_index > high.event_index
    assert low.event_index == 3
    assert low.confirmed_index is not None


# ────────────────────────────────────────────────────────────────────
# Case E：一字涨停推进（§10）
# O=H=L=C 单调上升 + 多次一字
# ────────────────────────────────────────────────────────────────────


def test_case_e_one_word_limit_up_progression():
    # 8 个 bar：第一根是 daily +0.5 推进，后面 7 个一字涨停 (qfq 连涨)
    # 但实际：如果全部一字涨停 + 大幅推进，算法应无反转 swing
    qfqs = [Decimal("10"), Decimal("10.5"), Decimal("11.2"), Decimal("12"),
            Decimal("13"), Decimal("14.5"), Decimal("16"), Decimal("18")]
    bars_list = []
    for i, q in enumerate(qfqs):
        one = i >= 1  # day0 之后都一字
        bars_list.append(make_bar(i, o=q, h=q, l=q, c=q, start_date=START, one_word_up=one))
    bars = bars_with_constant_atr(bars_list, Decimal("1.0"))
    eng = StructureEngine(swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0")))
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    print("=" * 70)
    print("Case E: 一字涨停推进")
    print(f"  qfq: {qfqs}")
    print(dump_state(state))
    print()
    # 一字板单调上升，无反转 swing
    assert len(state.confirmed_swings) == 0
    # current_leg 推进 +17%
    assert state.current_leg is not None
    assert state.current_leg.direction == +1


# ────────────────────────────────────────────────────────────────────
# Case F：除权（qfq 域连续，无假 Bear Leg）
# raw：100,100,100,100,50,50,50,50 - 但 qfq 在正确 anchor 下应连续
# ────────────────────────────────────────────────────────────────────


def test_case_f_corporate_action_no_false_bear_leg():
    qfqs = [Decimal("100")] * 8
    bars_list = [
        make_bar(i, o=q, h=q, l=q, c=q, start_date=START) for i, q in enumerate(qfqs)
    ]
    bars = bars_with_constant_atr(bars_list, Decimal("1.0"))
    eng = StructureEngine(swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0")))
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    print("=" * 70)
    print("Case F: 除权（qfq 域连续）")
    print(f"  qfq (raw 视角): 100×4, 50×4   qfq 实际: 全 100")
    print(dump_state(state))
    print()
    assert len(state.confirmed_swings) == 0


# ────────────────────────────────────────────────────────────────────
# Case G：停牌 placeholder 不改变结构
# 序列：[10, 11, suspended, 12, 13, 12, 11.5, 12.5]
# 停牌日被跳过；结构应等效于：[10, 11, 12, 13, 12, 11.5, 12.5]
# ────────────────────────────────────────────────────────────────────


def test_case_g_suspended_does_not_change_structure():
    bars_list = [
        make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
        make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
        make_bar(2, o=11, h=11, l=11, c=11, suspended=True, start_date=START),
        make_bar(3, o=12, h=12, l=12, c=12, start_date=START),
        make_bar(4, o=13, h=13, l=13, c=13, start_date=START),
        make_bar(5, o=12, h=12, l=12, c=12, start_date=START),
        make_bar(6, o=11.5, h=11.5, l=11.5, c=11.5, start_date=START),
        make_bar(7, o=12.5, h=12.5, l=12.5, c=12.5, start_date=START),
    ]
    bars = bars_with_constant_atr(bars_list, Decimal("1.0"))
    eng = StructureEngine(swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0")))
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    print("=" * 70)
    print("Case G: 停牌日不改变结构")
    print("  含 suspended 的 bars 序列")
    print(dump_state(state))
    print()
    # 至少产生与「无 suspended 的相同价格序列」一致的结构
    # 等价的全 8 根：[10,11,12,13,12,11.5,12.5]
    # 实际 bars_list 跳过 suspended 后是 7 根，所以结构是 [10,11,_,12,13,12,11.5,12.5]
    # 其 swing 序列应为 HIGH @ day4 (13), confirmed day5, LOW @ day6 (11.5)
    highs = [s for s in state.confirmed_swings if s.kind.value == "high"]
    lows = [s for s in state.confirmed_swings if s.kind.value == "low"]
    assert len(highs) >= 1
    assert highs[0].event_index == 4  # day4 = 13
    # 至少 1 个 LOW swing
    assert len(lows) >= 1


# ────────────────────────────────────────────────────────────────────
# Case H：Outside Bar 歧义
# day3: high 15, low 10, close 10 - 同根 bar 内冲高 + 收盘深度反转
# 标记 STRUCTURE_INTRABAR_AMBIGUOUS
# ────────────────────────────────────────────────────────────────────


def test_case_h_outside_bar_ambiguity():
    bars_list = [
        make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
        make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
        make_bar(2, o=12, h=12, l=12, c=12, start_date=START),
        make_bar(3, o=10, h=15, l=10, c=10, start_date=START),  # outside bar
    ]
    bars = bars_with_constant_atr(bars_list, Decimal("1.0"))
    eng = StructureEngine(swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0")))
    state = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    print("=" * 70)
    print("Case H: Outside Bar 歧义")
    print("  day0..2: 10/11/12 push; day3: o=10 h=15 l=10 c=10 (outside bar)")
    print(dump_state(state))
    print()
    # Q：有 ambiguity 标记吗？ §十九 要求
    assert QualityFlag.STRUCTURE_INTRABAR_AMBIGUOUS in state.quality_flags
    # 算法仍然 confirm 1 个 HIGH
    highs = [s for s in state.confirmed_swings if s.kind.value == "high"]
    assert len(highs) == 1
    # outside bar：日内既冲高又深度反转。event/confirmed 都是 day3 同一根 bar。
    # （真实语义：当日 extreme 与反转并行触发，不应伪装成'提前 N bar 已 event'）
    assert highs[0].event_index == 3
    assert highs[0].confirmed_index == 3
