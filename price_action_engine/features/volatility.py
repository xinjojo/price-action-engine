"""真实波幅与 Wilder ATR —— 标准定义，不做任何自定义修改。

裁决（V0.1.1 第四条）：连续一字板导致 TR=0 时，**不跳过**该 bar。

    如果跳过 TR=0，我们实际上创造了一个自定义 ATR，会破坏：
      - 可解释性
      - 与标准指标的可比性
      - Brooks 语义
      - 后续研究的复现性

    制度造成的观测压缩，用 market.china_a.limit_rules.volatility_flags()
    打 LIMIT_CENSORED 标记来表达。**标记归标记，ATR 归 ATR。**
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def true_range(high: Decimal, low: Decimal, prev_close: Decimal) -> Decimal:
    """标准 True Range。

    TR = max(H-L, |H - C_prev|, |L - C_prev|)

    注意 TR 含跳空，所以单根一字板（首日）TR 不为 0；
    只有【连续】一字板时，从第 2 天起 TR 才为 0。这是标准行为，不特殊处理。
    """
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )


def wilder_atr(
    true_ranges: Sequence[Decimal], period: int
) -> list[Decimal | None]:
    """标准 Wilder ATR。

    - 前 period-1 项为 None（样本不足）
    - 第 period 项 = 前 period 个 TR 的简单均值（种子）
    - 之后 ATR[i] = (ATR[i-1] * (period-1) + TR[i]) / period

    Args:
        true_ranges: 按时间升序的 TR 序列（qfq 域）
        period: Wilder 平滑周期

    Returns:
        与输入等长的列表，前 period-1 个为 None。
    """
    if period < 1:
        raise ValueError(f"period 必须 >= 1，收到 {period}")
    if not true_ranges:
        return []

    n = len(true_ranges)
    out: list[Decimal | None] = [None] * n
    if n < period:
        return out

    # 种子：前 period 个 TR 的简单平均
    prev = sum(true_ranges[:period], Decimal(0)) / Decimal(period)
    out[period - 1] = prev

    for i in range(period, n):
        prev = (prev * Decimal(period - 1) + true_ranges[i]) / Decimal(period)
        out[i] = prev

    return out
