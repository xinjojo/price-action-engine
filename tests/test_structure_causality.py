"""Structure Core 因果性守卫（V0.2 指令 §二十四、§二十五）。

核心守卫：

1. ``test_confirmed_swings_are_immutable_under_future_extension``
   bars[T+1:T+20] 之后，T 时刻已确认的 SwingPoint 不能改变。
   （任何后续数据都不应回溯改写历史。）

2. ``test_prefix_equivalence``
   全序列一次性计算的 output[as_of_date=T] == 滚动 ``run(data[:T+1])`` 的 output。
   两个流程必须等价，否则说明算法偷偷看了未来。
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

import pytest

from price_action_engine.structure import (
    ATRReversalSwingDetector,
    LegBuilder,
    PullbackBuilder,
    QualityFlag,
    StructureEngine,
    SwingKind,
    SwingPoint,
)
from price_action_engine.structure.types import (
    StructureBar,
)
from tests.structure_helpers import (
    bars_with_constant_atr,
    make_bar,
)


START = date(2025, 1, 2)
ATR = Decimal("1.0")


def _bare_engine() -> StructureEngine:
    return StructureEngine(
        swing_detector=ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0")),
        leg_builder=LegBuilder(),
        pullback_builder=PullbackBuilder(),
    )


def _rsw_equal(a: SwingPoint, b: SwingPoint) -> bool:
    return (
        a.kind == b.kind
        and a.event_date == b.event_date
        and a.event_index == b.event_index
        and a.confirmed_date == b.confirmed_date
        and a.confirmed_index == b.confirmed_index
        and a.price.analysis == b.price.analysis
    )


# ─────────────────────────────────────────────────────────────
# §24：confirmed swings 不可变
# ─────────────────────────────────────────────────────────────


def test_confirmed_swings_are_immutable_under_future_extension():
    sequence = [
        (10, 10, 10, 10),
        (11, 11, 11, 11),
        (12, 12, 12, 12),
        (13, 13, 13, 13),
        (12, 12, 12, 12),
        (11.5, 11.5, 11.5, 11.5),
        (12.5, 12.5, 12.5, 12.5),
        (13.5, 13.5, 13.5, 13.5),
        (12.5, 12.5, 12.5, 12.5),
        (11.5, 11.5, 11.5, 11.5),
    ]
    bars_list = [make_bar(i, o=o, h=h, l=l, c=c, start_date=START) for i, (o, h, l, c) in enumerate(sequence)]
    bars = bars_with_constant_atr(bars_list, ATR)

    eng = _bare_engine()

    # T = 5 (day5 = 11.5)
    T = 5
    state_at_T = eng.compute_state(bars=bars[: T + 1], as_of_date=bars[T].bar_date)
    confirmed_at_T = list(state_at_T.confirmed_swings)

    # 附加未来数据再算
    full = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    confirmed_at_full = [s for s in full.confirmed_swings if s.confirmed_index is not None and s.confirmed_index <= T]

    assert len(confirmed_at_T) == len(confirmed_at_full), (
        "追加未来数据改变了 T 时刻已确认的 swing 历史"
    )
    for left, right in zip(confirmed_at_T, confirmed_at_full):
        assert _rsw_equal(left, right), (
            f"T 时刻已确认 swing {left} 与 full 时重算 {right} 不一致"
        )


# ─────────────────────────────────────────────────────────────
# §25：前缀等价
# ─────────────────────────────────────────────────────────────


def test_prefix_equivalence_rolling_vs_full():
    """对于每个 as_of=T，run(data[:T+1]) == full.confirmed_at[<=T]。"""
    import random
    rng = random.Random(42)
    # 构造含若干反转的中等长度序列
    base = 100.0
    values = []
    cur = base
    for _ in range(20):
        values.append(cur)
        cur += rng.uniform(-2.5, 2.5)
    bars_list = []
    for i, v in enumerate(values):
        # 给每根 bar 一个微小的 range（防止 reverse 都边界）
        bars_list.append(
            make_bar(
                i,
                o=Decimal(str(round(v - 0.1, 3))),
                h=Decimal(str(round(v + 0.2, 3))),
                l=Decimal(str(round(v - 0.2, 3))),
                c=Decimal(str(round(v, 3))),
                start_date=START,
            )
        )
    bars = bars_with_constant_atr(bars_list, ATR)

    eng = _bare_engine()
    full = eng.compute_state(bars=bars, as_of_date=bars[-1].bar_date)
    full_swings_by_confirm = {}
    for s in full.confirmed_swings:
        if s.confirmed_index is not None:
            full_swings_by_confirm[s.confirmed_index] = s

    for T in range(2, len(bars)):
        prefix = eng.compute_state(bars=bars[: T + 1], as_of_date=bars[T].bar_date)
        prefix_confirmed = [s for s in prefix.confirmed_swings if s.confirmed_index is not None]
        full_for_T = [full_swings_by_confirm.get(idx) for idx in range(T + 1) if idx in full_swings_by_confirm]
        # 序列对比
        assert len(prefix_confirmed) == len(full_for_T), (
            f"T={T} prefix={len(prefix_confirmed)} full={len(full_for_T)}"
        )
        for a, b in zip(prefix_confirmed, full_for_T):
            if b is None:
                continue
            assert _rsw_equal(a, b), f"T={T} swing diff {a} vs {b}"


# ─────────────────────────────────────────────────────────────
# §二十四：因果性强化 - force unwind 不允许
# ─────────────────────────────────────────────────────────────


def test_no_future_look_in_swing_decision():
    """§九 decision-on-T 必须只用 ATR(T) at close。

    验证：调整 ATR(T) 会反向影响 T 时刻的 confirm 决策，
    证明算法没在 T 时刻偷偷使用任何"未来 ATR"。
    """
    bars_list = [
        make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
        make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
        make_bar(2, o=12, h=12, l=12, c=12, start_date=START),
        make_bar(3, o=12, h=12, l=10, c=11.0, start_date=START),  # close=11 → 反转候选
    ]

    eng = _bare_engine()

    # case A: ATR = 2.0 → threshold = 2.0 → extreme(12) - thr = 10 → close=11 > 10 → NOT confirm
    bars_a = bars_with_constant_atr(bars_list, Decimal("2.0"))
    state_a = eng.compute_state(bars=bars_a, as_of_date=bars_a[-1].bar_date)
    assert len(state_a.confirmed_swings) == 0, (
        "ATR=2.0 时 close=11 > extreme-2=10，应不产生 swing"
    )

    # case B: ATR = 0.5 → threshold = 0.5 → extreme(12) - thr = 11.5 → close=11 ≤ 11.5 → confirm
    bars_b = bars_with_constant_atr(bars_list, Decimal("0.5"))
    state_b = eng.compute_state(bars=bars_b, as_of_date=bars_b[-1].bar_date)
    assert len(state_b.confirmed_swings) == 1, (
        "ATR=0.5 时 close=11 ≤ extreme-0.5=11.5，应确认 1 个 HIGH swing"
    )
    assert state_b.confirmed_swings[0].kind.value == "high"
    assert state_b.confirmed_swings[0].event_index == 2
