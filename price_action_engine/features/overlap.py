"""Overlap —— 与前一 bar 的价格重叠度。

Brooks 用重叠度判断趋势纯度：
    强趋势的推进 bar 之间重叠少（几乎不回吐），
    震荡区的 bar 之间重叠多（来回拉锯）。
"""

from __future__ import annotations

from decimal import Decimal


def overlap_ratio(
    high: Decimal,
    low: Decimal,
    prev_high: Decimal,
    prev_low: Decimal,
) -> float | None:
    """当前 bar 与前一 bar 的价格重叠比例。

    定义：
        overlap      = max(0, min(H, H_prev) - max(L, L_prev))
        overlap_ratio = overlap / (H - L)

    Returns:
        0..1；当日 bar 无价格区间（range=0）时返回 None。
    """
    rng = high - low
    if rng <= 0:
        return None

    overlap = min(high, prev_high) - max(low, prev_low)
    if overlap <= 0:
        return 0.0  # 完全跳空离开，零重叠

    return float(overlap / rng)
