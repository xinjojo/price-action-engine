"""A 股涨跌停事件检测 —— 只做【已有限制事实】的分类，不做规则推导。

职责边界：
    本模块回答"给定当日涨跌停价，这根 bar 属于哪种限价事件"。
    涨跌停价【本身】从哪来（实测 / 规则推导）是 LimitRuleEngine 的事，V0.2 实现。

三条硬纪律：
    1. 不硬编码 10%
    2. 规则未知时返回 status=None（"不知道"），绝不猜
    3. 不输出任何主观可交易性评分 —— 能否成交由 Execution 结合订单方向判定
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ...data.schema import DailyLimit, PriceLimitMode

# 观察波动被涨跌停制度截断时的客观标记。
# 只做标记，【不修改 ATR】—— ATR 保持标准 Wilder 定义。
LIMIT_CENSORED = "LIMIT_CENSORED"

# 涨跌停价未知时的标记
LIMIT_MODE_UNKNOWN = "LIMIT_MODE_UNKNOWN"

# 单点 bar 但未触及限价 —— 是"全天一笔成交"，不是一字板
SINGLE_PRICE_NON_LIMIT = "SINGLE_PRICE_NON_LIMIT"


class LimitStatus(Enum):
    NONE = "none"
    UP_TOUCH = "up_touch"        # 盘中触及涨停，收盘未封
    UP_LOCKED = "up_locked"      # 收盘封涨停
    DOWN_TOUCH = "down_touch"
    DOWN_LOCKED = "down_locked"


@dataclass(frozen=True)
class LimitEvent:
    """单根 bar 的限价事件判定结果。"""

    status: LimitStatus | None
    """None 表示【无法判定】—— 当日涨跌停规则未知。

    刻意不使用 NONE：NONE 的含义是"确实没触及限价"，
    而"不知道规则"是另一回事，混用会让下游以为当日无限制。
    """

    is_one_word_limit_up: bool
    is_one_word_limit_down: bool
    is_single_price_bar: bool
    flags: tuple[str, ...] = ()

    @property
    def is_limit_locked(self) -> bool:
        return self.status in (LimitStatus.UP_LOCKED, LimitStatus.DOWN_LOCKED)

    @property
    def is_one_word_limit(self) -> bool:
        return self.is_one_word_limit_up or self.is_one_word_limit_down


def classify_limit_event(
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    limit: DailyLimit,
    tolerance: Decimal,
) -> LimitEvent:
    """判定单根 bar 的限价事件。

    Args:
        tolerance: 涨跌停价比较容差（配置里默认 tick/2 = 0.005）

    Returns:
        LimitEvent。当 limit.mode == UNKNOWN 时 status=None。
    """
    # 纯几何事实：全天只有一个价格
    single = open_ == high == low == close

    # 当日无涨跌幅限制：不可能有"一字板"这种制度事件，
    # 但仍可能是单点 bar（冷门股全天一笔成交）
    if limit.mode is PriceLimitMode.UNLIMITED:
        flags = (SINGLE_PRICE_NON_LIMIT,) if single else ()
        return LimitEvent(
            status=LimitStatus.NONE,
            is_one_word_limit_up=False,
            is_one_word_limit_down=False,
            is_single_price_bar=single,
            flags=flags,
        )

    # 规则未知：不猜。让下游自己决定如何处理
    if limit.mode is PriceLimitMode.UNKNOWN:
        flags = [LIMIT_MODE_UNKNOWN]
        if single:
            flags.append(SINGLE_PRICE_NON_LIMIT)
        return LimitEvent(
            status=None,
            is_one_word_limit_up=False,
            is_one_word_limit_down=False,
            is_single_price_bar=single,
            flags=tuple(flags),
        )

    up = limit.up_limit
    down = limit.down_limit
    assert up is not None and down is not None  # DailyLimit.__post_init__ 已保证

    # 一字板 = 单点 bar + 收在限价上。两个条件缺一不可。
    one_up = single and abs(close - up) <= tolerance
    one_down = single and abs(close - down) <= tolerance

    if one_up:
        status = LimitStatus.UP_LOCKED
    elif one_down:
        status = LimitStatus.DOWN_LOCKED
    elif abs(close - up) <= tolerance:
        # 收盘封板，但日内有价格区间（打开过或曾回落）
        status = LimitStatus.UP_LOCKED
    elif abs(close - down) <= tolerance:
        status = LimitStatus.DOWN_LOCKED
    elif abs(high - up) <= tolerance:
        # 盘中触板后炸板
        status = LimitStatus.UP_TOUCH
    elif abs(low - down) <= tolerance:
        status = LimitStatus.DOWN_TOUCH
    else:
        status = LimitStatus.NONE

    return LimitEvent(
        status=status,
        is_one_word_limit_up=one_up,
        is_one_word_limit_down=one_down,
        is_single_price_bar=single,
        flags=(),
    )


# 这些状态意味着该 bar 的【观测波动区间】被涨跌幅制度截断过：
#   UP_TOUCH / UP_LOCKED    最高价被压在涨停板上，向上的波动无法被观测到
#   DOWN_TOUCH / DOWN_LOCKED 最低价被托在跌停板上，向下的波动无法被观测到
#   一字板                   日内区间被压缩为 0
_CENSORING_STATUSES = (
    LimitStatus.UP_TOUCH,
    LimitStatus.UP_LOCKED,
    LimitStatus.DOWN_TOUCH,
    LimitStatus.DOWN_LOCKED,
)


def volatility_flags(events: Sequence[LimitEvent]) -> tuple[str, ...]:
    """ATR 窗口内的观测波动是否被涨跌停制度截断。

    客观判据：窗口内任一 bar 的最高价触及涨停，或最低价触及跌停。
    —— 这两个方向的波动被制度硬性终止，我们观测到的区间是【被截断的】。

    我们【不修改 ATR】，只打标记让下游知道这段 ATR 是被制度压缩过的观测值。
    若日后确需剔除制度影响，应新增独立变量（如 constraint_adjusted_volatility），
    而不是把标准 ATR 改成一个自定义指标。V0.1.1 不实现该变量。
    """
    if any(e.is_one_word_limit or e.status in _CENSORING_STATUSES for e in events):
        return (LIMIT_CENSORED,)
    return ()
