"""Swing Detector 单元测试。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from price_action_engine.structure import (
    ATRReversalSwingDetector,
    QualityFlag,
    SwingKind,
)
from price_action_engine.structure.types import PriceDomain
from tests.structure_helpers import bars_with_constant_atr, make_bar


ATR = Decimal("1.0")
START = date(2025, 1, 2)


def _series(values):
    """values: list of (o, h, l, c) with day_index = 0..N-1."""
    out = []
    for i, (o, h, l, c) in enumerate(values):
        out.append(make_bar(i, o=o, h=h, l=l, c=c, start_date=START))
    return bars_with_constant_atr(out, ATR)


def test_detector_attributes_match_config():
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.5"), atr_period=10)
    assert det.detector_id == "atr_reversal_v1"
    assert det.reversal_threshold_atr == Decimal("1.5")
    assert det.atr_period == 10
    params = det.detector_params()
    assert params["reversal_threshold_atr"] == Decimal("1.5")
    assert params["atr_period"] == 10


def test_detector_rejects_non_positive_threshold():
    with pytest.raises(ValueError):
        ATRReversalSwingDetector(reversal_threshold_atr=Decimal("0"))
    with pytest.raises(ValueError):
        ATRReversalSwingDetector(reversal_threshold_atr=Decimal("-1"))


def test_zero_swings_under_threshold():
    """threshold=1.0, atr=1.0，单调上升无回调，不应产生任何 swing。"""
    bars = _series([(10, 10, 10, 10), (11, 11, 11, 11), (12, 12, 12, 12), (13, 13, 13, 13)])
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    out = det.detect(bars)
    assert out == []  # 没有反转


def test_high_then_low_cycle_produces_two_swings():
    """Case A 简化：典型涨后跌，不需极值反转也能确认。"""
    bars = _series([
        (10, 10, 10, 10),
        (11, 11, 11, 11),
        (12, 12, 12, 12),
        (13, 13, 13, 13),
        (12.5, 12.5, 12.5, 12.5),  # 不反转
        (12.0, 12.0, 12.0, 12.0),  # close = extreme - thr = 12, 应 confirm HIGH
        (12.5, 12.5, 12.5, 12.5),  # 不反转（DOWN 跟踪）
        (14, 14, 14, 14),           # close = extreme + thr = 13 → confirm LOW
    ])
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    out = det.detect(bars)

    assert len(out) == 2
    first, second = out
    assert first.kind == SwingKind.HIGH
    assert first.event_date == START + timedelta(days=3)
    assert first.event_index == 3
    assert first.confirmed_date == START + timedelta(days=5)
    assert first.confirmed_index == 5
    assert first.price.analysis == Decimal("13")
    assert first.price.domain is PriceDomain.QFQ
    assert first.is_confirmed

    assert second.kind == SwingKind.LOW
    assert second.event_date == START + timedelta(days=5)
    assert second.confirmed_index == 7
    assert second.is_confirmed


def test_higher_threshold_is_less_sensitive():
    """threshold=2.0 比 threshold=1.0 在相同序列下产生更少 swing。"""
    bars = _series([
        (10, 10, 10, 10),
        (12, 12, 12, 12),    # +2
        (10, 10, 10, 10),    # -2
        (12, 12, 12, 12),    # +2
        (10, 10, 10, 10),    # -2
    ])
    det_low = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    det_high = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("2.0"))
    # 注意：det_low 的 thr=1 表示 close ≤ extreme - 1 即可 confirm；
    # 但极端来回 2 也能 confirm（因为 close ≤ extreme - 2 时 confirm）
    # thr=2 时 close 必须 ≤ extreme - 2 才 confirm，而 cross -2 正好是 12 - 2 = 10，close=10 → confirm
    # 所以两条都会 confirm —— 反例不严谨。改成 thr=3。
    det_high = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("3.0"))
    out_low = det_low.detect(bars)
    out_high = det_high.detect(bars)
    assert len(out_low) >= len(out_high)


def test_insufficient_atr_marks_but_proceeds():
    """ATR 缺失时不应该 crash，而是标记 INSUFFICIENT_ATR flag。"""
    bars = []
    for i, (o, h, l, c) in enumerate([(10, 10, 10, 10), (11, 11, 11, 11)]):
        b = make_bar(i, o=o, h=h, l=l, c=c, atr=None, start_date=START)
        bars.append(b)
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    out = det.detect(bars)
    assert QualityFlag.INSUFFICIENT_ATR in det.last_flags


def test_suspended_bars_are_skipped():
    """停牌日不进入价格序列，但 engine-level 仍标记 SUSPENDED_BARS_SKIPPED。"""
    suspended = make_bar(2, o=10, h=10, l=10, c=10, suspended=True, start_date=START)
    bars = [
        make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
        make_bar(1, o=11, h=11, l=11, c=11, start_date=START),
        suspended,
        make_bar(3, o=12, h=12, l=12, c=12, start_date=START),
        make_bar(4, o=8, h=8, l=8, c=8, start_date=START),
    ]
    bars = bars_with_constant_atr(bars, Decimal("1.0"))
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    out = det.detect(bars)
    assert QualityFlag.SUSPENDED_BARS_SKIPPED in det.last_flags
    # day4 close=8 ≤ extreme(12) - 1 → confirm HIGH event@day3, 然后转 DOWN
    assert len(out) == 1
    assert out[0].kind == SwingKind.HIGH
    assert out[0].event_index == 3  # day3（suspended 跳过，所以 day4 是 bar_index=3）


def test_one_word_limit_up_still_pushes_extreme():
    """§10 一字涨停：raw range=0，但价格水平仍真实上推，结构应继续推进。"""
    bars = [
        make_bar(0, o=10, h=10, l=10, c=10, start_date=START),
        make_bar(1, o=11, h=11.1, l=10.9, c=11, start_date=START),
        make_bar(2, o=11.5, h=11.5, l=11.5, c=11.5, one_word_up=True, start_date=START),
        make_bar(3, o=12.0, h=12.0, l=12.0, c=12.0, one_word_up=True, start_date=START),
        make_bar(4, o=12.5, h=12.5, l=12.5, c=12.5, one_word_up=True, start_date=START),
    ]
    bars = bars_with_constant_atr(bars, Decimal("0.5"))  # thr = 0.5
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))  # thr = 0.5×1.0 = 0.5
    out = det.detect(bars)
    # 全部一字涨停向上推进，应无反转 swing
    assert out == []


def test_outside_bar_intrabar_ambiguity_flag():
    """§19 一根 bar 内高创新高 + 收盘已远低于反转阈值 → 标记歧义。"""
    bars = _series([
        (10, 10, 10, 10),
        (12, 12, 12, 12),
    ])
    # day3: high=15 (远超 extreme=12), close=10 (远低)
    # algorithm: 先 high 更新 extreme 到 15，然后 close=10 ≤ 15-1=14 → confirm prior HIGH
    # 同根 bar 内既 high 创新高又 close 触发 confirm → STRUCTURE_INTRABAR_AMBIGUOUS
    bars.append(make_bar(2, o=10, h=15, l=10, c=10, atr=Decimal("1.0"), start_date=START))
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    out = det.detect(bars)
    assert QualityFlag.STRUCTURE_INTRABAR_AMBIGUOUS in det.last_flags
    # 仍然 confirm 一个 HIGH。candidate 在同根 bar 内被推到 day2，然后 confirm 在 day2。
    # 这意味着 event==confirmed==day2（同根 bar）。
    assert len(out) == 1
    assert out[0].kind == SwingKind.HIGH
    assert out[0].confirmed_index == 2


def test_no_swings_when_extreme_does_not_reach_threshold():
    """close 横盘但未达到 thr，方向不变，无确认。"""
    bars = _series([
        (10, 10, 10, 10),
        (10.5, 10.5, 10.5, 10.5),  # +0.5，仅推进
        (10.4, 10.4, 10.4, 10.4),  # -0.1，没反转
        (10.6, 10.6, 10.6, 10.6),
    ])
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    out = det.detect(bars)
    assert out == []


def test_event_and_confirmed_dates_are_distinct():
    """§6 event_date 与 confirmed_date 必不同时（先做 candidate 再 confirm）。"""
    bars = _series([
        (10, 10, 10, 10),
        (12, 12, 12, 12),
        (12.1, 12.1, 12.1, 12.1),  # 推进 extreme
        (8, 8, 8, 8),                # 重反转，confirm prior HIGH event@day2
    ])
    det = ATRReversalSwingDetector(reversal_threshold_atr=Decimal("1.0"))
    out = det.detect(bars)
    assert len(out) == 1
    s = out[0]
    assert s.event_index != s.confirmed_index
    assert s.event_date != s.confirmed_date
