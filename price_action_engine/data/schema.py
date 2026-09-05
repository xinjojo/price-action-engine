"""内部统一数据 Schema —— 所有数据源进入 Core 前的唯一形态。

设计原则见 docs/DATA_LAYER.md：
  - 价格一律 Decimal（涨跌停判定、成交判定不能有浮点误差）
  - raw / qfq / hfq 三域分离；本文件只存 raw + adj_factor，复权价由 bridge 派生
  - 制度事实与几何事实分开存放
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class StockStatus(Enum):
    """证券状态。带起止时间段，不是当日快照（防前视偏差）。"""

    NORMAL = "normal"
    ST = "st"
    STAR_ST = "star_st"          # *ST
    NEW = "new"
    DELISTING = "delisting"      # 退市整理期（仍可交易，规则特殊）
    DELISTED = "delisted"


class MarketStatus(Enum):
    """该交易日该标的是否可交易。

    注意 HALTED 与 SUSPENDED 的区别：
      HALTED    盘中临时停牌 —— 当日【有成交、有 K 线】，按正常 bar 处理
      SUSPENDED 全天停牌     —— 当日无 bar，需在序列中补 gap bar 占位
    """

    TRADABLE = "tradable"
    SUSPENDED = "suspended"
    HALTED = "halted"
    PRE_LISTING = "pre_listing"
    DELISTED = "delisted"


class PriceLimitMode(Enum):
    """当日是否存在涨跌幅限制 —— 显式三态。

    LIMITED    有涨跌幅限制，up_limit / down_limit 必有值
    UNLIMITED  当日无涨跌幅限制（新股前 5 日、退市整理期首日等）
    UNKNOWN    规则未知

    为什么不用 bool：bool 的 False 同时表示"没有"和"不知道"，
    会让下游把"规则缺失"当成"可以自由涨跌"，是一类极安静的错误。
    """

    LIMITED = "limited"
    UNLIMITED = "unlimited"
    UNKNOWN = "unknown"


class LimitSource(Enum):
    """涨跌停价的来源，决定可信度。"""

    PROVIDER = "provider"   # 交易所口径实测值（Tushare stk_limit）
    DERIVED = "derived"     # 按规则表推导
    UNKNOWN = "unknown"     # 推导不出


@dataclass(frozen=True)
class DailyLimit:
    """某交易日某标的的涨跌幅限制事实。"""

    mode: PriceLimitMode
    up_limit: Decimal | None = None
    down_limit: Decimal | None = None
    rule_id: str | None = None
    source: LimitSource = LimitSource.UNKNOWN

    def __post_init__(self) -> None:
        if self.mode is PriceLimitMode.LIMITED:
            if self.up_limit is None or self.down_limit is None:
                raise ValueError("mode=LIMITED 时必须同时提供 up_limit 与 down_limit")
            if self.up_limit <= 0 or self.down_limit <= 0:
                raise ValueError(
                    f"涨跌停价必须为正，收到 up={self.up_limit} down={self.down_limit}"
                )
            if self.up_limit < self.down_limit:
                raise ValueError(
                    f"涨停价不得低于跌停价：up={self.up_limit} down={self.down_limit}"
                )
        elif self.up_limit is not None or self.down_limit is not None:
            raise ValueError(
                f"mode={self.mode.value} 时 up_limit / down_limit 必须为 None，"
                f"收到 up={self.up_limit} down={self.down_limit}"
            )

    @property
    def has_price_limit(self) -> bool:
        return self.mode is PriceLimitMode.LIMITED


@dataclass(frozen=True)
class MarketConstraint:
    """某交易日某标的的全部 A 股制度事实。

    只放客观事实，**不含任何主观可交易性评分**。
    能否成交由 ExecutionEngine.can_fill() 结合订单方向判定。
    """

    trade_date: date
    board: str
    stock_status: StockStatus
    market_status: MarketStatus
    limit: DailyLimit


@dataclass(frozen=True)
class AdjustFactor:
    symbol: str
    trade_date: date
    adj_factor: float
    source: str  # 禁止跨源混用：Tushare 的因子只能配 Tushare 的价格


@dataclass(frozen=True)
class TradeDay:
    trade_date: date
    is_open: bool
    trading_day_index: int


@dataclass(frozen=True)
class DailyBar:
    """统一日 K。

    只存 raw（真实成交口径）+ adj_factor。
    qfq / hfq 不落库、不缓存 —— 前复权以"最新"为基准，每次除权全部历史都会变，
    缓存 qfq 等于缓存一个会过期的结论。复权价一律经 PriceBridge 在读取时派生。
    """

    symbol: str
    trade_date: date
    trading_day_index: int | None

    # ---- 原始未复权（真实成交口径，永不修改）----
    raw_open: Decimal
    raw_high: Decimal
    raw_low: Decimal
    raw_close: Decimal
    raw_pre_close: Decimal  # 除权后前收，可直接用于涨跌停计算

    # ---- 成交量额（事实，永不复权）----
    volume: float
    amount: float

    # ---- 复权 ----
    adj_factor: float
    adj_factor_source: str

    # ---- 制度与质量 ----
    board: str
    stock_status: StockStatus
    market_status: MarketStatus
    quality_score: float
    quality_flags: tuple[str, ...]

    source: str
    schema_version: str

    @property
    def is_single_price_bar(self) -> bool:
        """纯几何事实：全天只有一个价格。

        与"一字板"是两件事 —— 一字板要求同时满足【单点】+【触及限价】。
        """
        return self.raw_open == self.raw_high == self.raw_low == self.raw_close

    @property
    def is_tradable(self) -> bool:
        """盘中临停(HALTED)当日有成交，仍算可交易。"""
        return self.market_status in (MarketStatus.TRADABLE, MarketStatus.HALTED)
