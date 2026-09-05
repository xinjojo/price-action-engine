"""Gap —— 独立特征，绝不塞进虚拟 K 线。

为什么必须双算：
    除权除息日在 raw 上表现为向下跳空，在 qfq 上不表现。
    → 用 raw 算 Gap 会把每一次分红送股都误判成"看跌跳空"。
    真实隔夜跳空在 raw 和 qfq 上【都】表现。

    Core 只用 gap_qfq（Price Action 语义）；
    is_ex_dividend_date 仅作标记与过滤，**绝不用它去修正任何价格**。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class GapFeatures:
    gap_size: Decimal | None
    """Price Action 语义跳空（qfq 域）。主字段。"""

    gap_percent: float | None
    gap_atr: float | None
    """ATR 归一化跳空 —— 跨标的、跨时期可比。"""

    gap_direction: int
    """派生只读视图：-1 / 0 / +1。"""

    is_ex_dividend_date: bool
    raw_gap_size: Decimal | None
    """事实跳空（raw 域，含除权）。仅作记录，不用于 Price Action 语义。"""


def compute_gap(
    *,
    qfq_open: Decimal,
    qfq_prev_close: Decimal,
    raw_open: Decimal,
    raw_prev_close: Decimal,
    is_ex_dividend_date: bool,
    atr: Decimal | None = None,
) -> GapFeatures:
    """计算跳空。

    Args:
        qfq_open / qfq_prev_close: qfq 域（Price Action 语义）
        raw_open / raw_prev_close: raw 域（事实）
        is_ex_dividend_date: 由 PriceBridge.is_ex_dividend_date 判定
        atr: qfq 域 ATR，用于 gap_atr 归一化
    """
    gap_qfq = qfq_open - qfq_prev_close
    raw_gap = raw_open - raw_prev_close

    gap_percent = (
        float(gap_qfq / qfq_prev_close) if qfq_prev_close != 0 else None
    )
    gap_atr = float(gap_qfq / atr) if (atr is not None and atr > 0) else None

    direction = 0
    if gap_qfq > 0:
        direction = 1
    elif gap_qfq < 0:
        direction = -1

    return GapFeatures(
        gap_size=gap_qfq,
        gap_percent=gap_percent,
        gap_atr=gap_atr,
        gap_direction=direction,
        is_ex_dividend_date=is_ex_dividend_date,
        raw_gap_size=raw_gap,
    )
