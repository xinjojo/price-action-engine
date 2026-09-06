"""Structure Core — V0.2 数据类型。

参考：
- PA Engine V0.2 指令 §二十二（目录）
- V0.2 指令 §三（Price Domain 契约）
- V0.2 指令 §六（SwingPoint event/confirmed 分离）

设计原则：

1. Structure Window 内统一 anchor。
   ``StructureEngine.compute_state`` 在入口处固定 ``as_of_date``，
   整段窗口内的 qfq 都以此为基准换算，禁止每根 bar 各自换算坐标系。

2. Raw price 永远保留可恢复性。
   ``StructurePrice.raw`` 是成交价（不复权），``analysis`` 是统一 anchor 下的
   复权价格。下游 Execution 需要 raw 时可以直接用，不需要再次反推。

3. Swing 必须区分 event 与 confirmed。
   §六明确要求 SwingPoint 同时记录 ``event_date`` 与 ``confirmed_date``，
   前者是真实极端，后者是因果确认。Setup 不可能在 event 那一刻就使用 Swing。

4. Leg Strength 不要拍脑袋 0~100（§十四）。
   只保留 ``duration_bars / return_pct / atr_distance / net_move``，未来研究阶段
   再合成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Sequence


# ────────────────────────────────────────────────────────────────────
# 价格域
# ────────────────────────────────────────────────────────────────────


class PriceDomain(Enum):
    """价格所属坐标系。V0.2 仅使用 RAW / QFQ；HFQ 仅供未来展示使用，UNSET 不可见。"""

    RAW = "raw"               # 不复权
    QFQ = "qfq"               # 前复权，qfq = raw × adj_factor(bar) / adj_factor(anchor)
    HFQ = "hfq"               # 后复权（V0.2 不使用，仅占位）
    UNSET = "unset"           # 内部初始化占位，不应出现在产出物里


@dataclass(frozen=True)
class StructureAnchor:
    """一次 StructureEngine 计算窗口内的统一坐标系基准。

    ``as_of_date`` 即窗口最后一根 bar 的 trade_date（§二 决策原则）。
    任何一根 bar 在该窗口中都以 ``adj_factor(bar) / adj_factor(as_of_date)`` 为系数。
    """

    as_of_date: date
    suffix: str = ""

    def label(self) -> str:
        return f"qfq@{self.as_of_date.isoformat()}"


@dataclass(frozen=True)
class StructurePrice:
    """结构域一个价格点，必须同时保留 raw + analysis + 坐标系元数据。"""

    raw: Decimal                       # 真实成交价（不复权）
    analysis: Decimal                  # 在当前 anchor 下复权后的价格
    domain: PriceDomain                # 当前结构算法实际使用的坐标系
    anchor_date: date | None           # 统一 anchor；UNSET 时为 None


# ────────────────────────────────────────────────────────────────────
# Quality Flags
# ────────────────────────────────────────────────────────────────────


class QualityFlag(str, Enum):
    """Structure 计算过程中的客观事实标记。下游可消费，不影响算法。"""

    INSUFFICIENT_ATR = "INSUFFICIENT_ATR"                 # ATR 预热不足，跳过
    SUSPENDED_BARS_SKIPPED = "SUSPENDED_BARS_SKIPPED"     # 跳过停牌日
    STRUCTURE_INTRABAR_AMBIGUOUS = "STRUCTURE_INTRABAR_AMBIGUOUS"  # 同一根 bar 内高/低歧义
    EX_CORPORATE_ACTION_PRESENT = "EX_CORPORATE_ACTION_PRESENT"    # 检测到除权日
    ATR_LIMIT_CENSORED = "ATR_LIMIT_CENSORED"             # §裁决四：制度截断 flag，不进 ATR


# ────────────────────────────────────────────────────────────────────
# 输入：StructureBar
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StructureBar:
    """结构算法看到的单根 bar 轻量表示。

    关键约束：
    - ``*_qfq`` 在传入前已用统一 anchor 换算，本模块不做二次换算
    - ``raw_*`` 用于可恢复性；停牌日允许为 None（见 §二十）
    - 停牌日 ``is_suspended=True`` 时所有 qfq/raw 仍可保留 prev_close 副本，
      但 Swing 检测器会把它**从序列中跳过**，duration 只数可交易 bars
    """

    bar_date: date
    bar_index: int                     # 在窗口中的位置，从 0 开始
    open_qfq: Decimal
    high_qfq: Decimal
    low_qfq: Decimal
    close_qfq: Decimal
    raw_open: Decimal | None = None
    raw_high: Decimal | None = None
    raw_low: Decimal | None = None
    raw_close: Decimal | None = None
    atr_qfq: Decimal | None = None     # 已按统一 anchor 换算后的 ATR
    is_suspended: bool = False         # True → Swing 计算跳过该 bar
    is_one_word_limit_up: bool = False
    is_one_word_limit_down: bool = False


# ────────────────────────────────────────────────────────────────────
# 产出：Swing / Leg / Pullback / StructureState
# ────────────────────────────────────────────────────────────────────


class SwingKind(Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class SwingPoint:
    """一个 Swing 转折点。§六 强制要求 event_date 与 confirmed_date 分离。"""

    kind: SwingKind
    event_date: date
    event_index: int
    confirmed_date: date | None                # None 表示尚未确认
    confirmed_index: int | None
    price: StructurePrice                      # raw + analysis 永久成对
    detector_id: str
    detector_params: dict[str, Any] = field(default_factory=dict)

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_date is not None


@dataclass(frozen=True)
class LegState:
    """一条 Swing-to-Swing 腿的事实记录，不含主观 strength。

    §十四 明确：leg_strength 不要现在拍 0~100。只保存组成事实。
    """

    direction: int                              # +1 = UP, -1 = DOWN, 0 = 未定义
    is_confirmed: bool                          # False 表示 developing
    is_current: bool                            # True = current_leg

    start_date: date | None
    end_date: date | None                       # 未结束时为 None
    start_index: int | None
    end_index: int | None                       # 未结束时为 None

    start_price: StructurePrice | None
    end_price: StructurePrice | None            # 未结束时为当前追踪的极端
    extreme_price: StructurePrice | None        # 当前/最终的极端

    duration_bars: int                          # 实际可交易 bars，§二十
    net_move: Decimal | None                    # extreme - start (qfq)
    return_pct: float | None                    # net_move / start_price (qfq)
    atr_distance: Decimal | None                # net_move / ATR(leg start)，单位 ATR


@dataclass(frozen=True)
class PullbackState:
    """Brooks 的 pullback 作为结构状态而非 Setup（§十五）。"""

    direction: int                              # +1 = UP (向 leg 反方向回撤) 或 -1
    is_active: bool                             # 是否正在回撤
    is_current: bool

    start_date: date | None
    start_index: int | None

    extreme_price: StructurePrice | None        # 当前回撤极端
    current_price: StructurePrice | None

    duration_bars: int                          # 已进行的回撤 bars

    depth_abs: Decimal | None                   # |prior_leg_extreme - current_extreme| (qfq)
    depth_pct_of_prior_leg: float | None        # §十六 最重要字段
    depth_atr: Decimal | None                   # depth_abs / ATR(start)


@dataclass(frozen=True)
class StructureState:
    """某 as_of_date 的完整结构状态。"""

    as_of_date: date
    anchor: StructureAnchor

    last_confirmed_swing_high: SwingPoint | None
    last_confirmed_swing_low: SwingPoint | None

    previous_confirmed_leg: LegState | None
    current_leg: LegState | None

    current_pullback: PullbackState | None

    confirmed_swings: Sequence[SwingPoint] = field(default_factory=tuple)

    quality_flags: tuple[QualityFlag, ...] = ()


__all__ = [
    "PriceDomain",
    "StructureAnchor",
    "StructurePrice",
    "QualityFlag",
    "StructureBar",
    "SwingKind",
    "SwingPoint",
    "LegState",
    "PullbackState",
    "StructureState",
]
