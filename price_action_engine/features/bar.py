"""单 bar 原子特征（Level A）+ 一字板 Price Action 语义覆盖。

核心设计（对应需求 §21-§24）：
    一字涨停 O=H=L=C 在普通几何算法下得到 range=0 / body=0 ⇒ 被判成十字星（弱）。
    A 股语义下它是【极强买方控制 + 几乎买不到】。

    处理方案：几何字段返回 None（不适用），语义字段走制度覆盖。
    —— **绝不伪造 OHLC**。Raw 数据永远是真实的。

    一字板 Snapshot 上会同时出现三个独立维度，不合并成一个数：
        bar_bias            = +100   强买方控制
        gap_qfq             > 0     向上跳空
        is_one_word_limit_up = True 锁死（能否成交由 Execution 判定）
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ..market.china_a.limit_rules import LimitEvent

ATR_UNAVAILABLE = "ATR_UNAVAILABLE"
SINGLE_PRICE_NON_LIMIT = "SINGLE_PRICE_NON_LIMIT"


class BarInterpretationSource(Enum):
    GEOMETRY = "geometry"        # 由 K 线几何计算
    LIMIT_EVENT = "limit_event"  # 由涨跌停制度事件覆盖


@dataclass(frozen=True)
class BarFeatures:
    body_ratio: float | None
    upper_wick_ratio: float | None
    lower_wick_ratio: float | None
    close_location: float | None
    is_single_price_bar: bool
    bar_bias: float | None
    """单 bar 方向控制强度，带符号，范围 -100..+100。"""
    bar_control_score: float | None
    """|bar_bias|，0..100。"""
    interpretation_source: BarInterpretationSource
    """审计字段：下游必须知道这个分数是几何算的还是被制度覆盖的。"""
    flags: tuple[str, ...] = ()


def _bias_atr(net: float, atr: float, cap_atr: float) -> float:
    """ATR 归一化方向量，clamp 到 -100..+100。"""
    raw = 100.0 * (net / (cap_atr * atr))
    return max(-100.0, min(100.0, raw))


def compute_bar_features(
    *,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    limit_event: LimitEvent,
    atr: Decimal | None = None,
    cap_atr: float | None = None,
) -> BarFeatures:
    """计算单 bar 特征。

    Args:
        open_/high/low/close: **qfq 域**价格（结构识别一律在复权域）
        limit_event: 由 market.china_a.limit_rules.classify_limit_event 产出
        atr: Wilder ATR（qfq 域）。None = 预热期不足，bar_bias 返回 None
        cap_atr: ATR 归一化封顶倍数。【仅在几何分支有意义】。
            一字板覆盖分支（limit_event.is_one_word_limit_*）
            不依赖此参数，可以传 None。
            几何分支若 atr 存在但 cap_atr 为 None ⇒ 等价于 ATR 不可用。
    """
    # ---- 一字涨停：几何不适用，语义覆盖为极强买方控制 ----
    if limit_event.is_one_word_limit_up:
        return BarFeatures(
            body_ratio=None,
            upper_wick_ratio=None,
            lower_wick_ratio=None,
            close_location=None,
            is_single_price_bar=True,
            bar_bias=100.0,
            bar_control_score=100.0,
            interpretation_source=BarInterpretationSource.LIMIT_EVENT,
        )

    # ---- 一字跌停：镜像 ----
    if limit_event.is_one_word_limit_down:
        return BarFeatures(
            body_ratio=None,
            upper_wick_ratio=None,
            lower_wick_ratio=None,
            close_location=None,
            is_single_price_bar=True,
            bar_bias=-100.0,
            bar_control_score=100.0,
            interpretation_source=BarInterpretationSource.LIMIT_EVENT,
        )

    rng = high - low

    # ---- 单点 bar 但未触及限价：全天一笔成交，不是一字板 ----
    # 几何量全部不适用。返回 None 而不是 0 —— 0 会被误读成"极弱"。
    if rng <= 0:
        return BarFeatures(
            body_ratio=None,
            upper_wick_ratio=None,
            lower_wick_ratio=None,
            close_location=None,
            is_single_price_bar=True,
            bar_bias=None,
            bar_control_score=None,
            interpretation_source=BarInterpretationSource.GEOMETRY,
            flags=(SINGLE_PRICE_NON_LIMIT,),
        )

    body = abs(close - open_)
    body_ratio = float(body / rng)
    upper_wick_ratio = float((high - max(open_, close)) / rng)
    lower_wick_ratio = float((min(open_, close) - low) / rng)
    close_location = float((close - low) / rng)

    flags: list[str] = []
    bar_bias: float | None = None
    if atr is None or atr <= 0 or cap_atr is None:
        # 任意一项缺失都视为 ATR 不可用，几何分支无法给方向控制强度
        flags.append(ATR_UNAVAILABLE)
    else:
        # 方向（ATR 归一）× 实体占比（置信加权）
        # 实体占比低 = 多空拉锯，即使涨跌幅大也不算"控制"
        bar_bias = _bias_atr(float(close - open_), float(atr), cap_atr) * body_ratio

    return BarFeatures(
        body_ratio=body_ratio,
        upper_wick_ratio=upper_wick_ratio,
        lower_wick_ratio=lower_wick_ratio,
        close_location=close_location,
        is_single_price_bar=False,
        bar_bias=bar_bias,
        bar_control_score=None if bar_bias is None else abs(bar_bias),
        interpretation_source=BarInterpretationSource.GEOMETRY,
        flags=tuple(flags),
    )
