"""PriceBridge —— raw / qfq / hfq 三域换算的唯一入口。

为什么必须有它：
    Setup 在【qfq 域】算出触发价（结构、形态都在复权价上识别），
    Execution 必须拿【raw 域】的 K 线判断成交（交易所按不复权价撮合）。
    两端不换算，茅台这类 adj_factor 上百的标的会差出数量级 —— 而且不报错。

刻意保持简单：只有两个方向的换算 + 一个通用 convert，不建转换框架。
详细设计见 docs/ARCHITECTURE.md §4.2。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from enum import Enum


class PriceDomain(Enum):
    RAW = "raw"    # 不复权 —— 唯一对应真实成交的口径
    QFQ = "qfq"    # 前复权 —— Core 分析域
    HFQ = "hfq"    # 后复权 —— 收益域


class PriceBridgeError(ValueError):
    pass


class PriceBridge:
    """价格域换算。

    Args:
        adj_factors: {trade_date: adj_factor}
        source: 因子来源（如 "tushare"）。禁止跨源混用 ——
                Tushare 官方声明各数据源对分红/送股/配股/税务的处理逻辑不同，
                Tushare 的因子只能配 Tushare 的价格。
    """

    def __init__(self, adj_factors: Mapping[date, float], source: str) -> None:
        if not source:
            raise PriceBridgeError("必须声明 adj_factor 来源，禁止匿名因子")
        self._factors = {
            d: Decimal(str(f)) for d, f in adj_factors.items()
        }
        self._source = source

    @property
    def source(self) -> str:
        return self._source

    def assert_source(self, bar_source: str) -> None:
        """跨源防护：价格与因子必须同源。"""
        if bar_source != self._source:
            raise PriceBridgeError(
                f"禁止跨源混用复权因子：价格来自 {bar_source!r}，因子来自 {self._source!r}"
            )

    def factor(self, bar_date: date) -> Decimal:
        try:
            return self._factors[bar_date]
        except KeyError:
            raise PriceBridgeError(f"缺少 {bar_date} 的复权因子") from None

    def to_adjusted_price(
        self,
        raw_price: Decimal,
        bar_date: date,
        domain: PriceDomain,
        as_of: date | None = None,
    ) -> Decimal:
        """raw → 复权价。

        Args:
            domain: HFQ（后复权）或 QFQ（前复权）
            as_of:  domain=QFQ 时必需。前复权以 as_of 日的因子为基准
        """
        if raw_price < 0:
            raise PriceBridgeError(f"价格不能为负：{raw_price}")
        f = self.factor(bar_date)
        if domain is PriceDomain.HFQ:
            return raw_price * f
        if domain is PriceDomain.QFQ:
            if as_of is None:
                raise PriceBridgeError("domain=QFQ 时必须提供 as_of（前复权基准日）")
            return raw_price * f / self.factor(as_of)
        raise PriceBridgeError(f"目标域必须是 QFQ 或 HFQ，收到 {domain}")

    def to_raw_price(
        self,
        adjusted_price: Decimal,
        bar_date: date,
        domain: PriceDomain,
        as_of: date | None = None,
    ) -> Decimal:
        """复权价 → raw。入场/止损触发价进 Execution 前必须过这一步。"""
        f = self.factor(bar_date)
        if domain is PriceDomain.HFQ:
            return adjusted_price / f
        if domain is PriceDomain.QFQ:
            if as_of is None:
                raise PriceBridgeError("domain=QFQ 时必须提供 as_of（前复权基准日）")
            return adjusted_price * self.factor(as_of) / f
        raise PriceBridgeError(f"来源域必须是 QFQ 或 HFQ，收到 {domain}")

    def convert(
        self,
        price: Decimal,
        bar_date: date,
        src: PriceDomain,
        dst: PriceDomain,
        as_of: date | None = None,
    ) -> Decimal:
        """任意两域互转。先落到 raw，再转到目标域。"""
        if src is dst:
            return price
        raw = price if src is PriceDomain.RAW else self.to_raw_price(
            price, bar_date, src, as_of
        )
        if dst is PriceDomain.RAW:
            return raw
        return self.to_adjusted_price(raw, bar_date, dst, as_of)

    def is_ex_dividend_date(self, d: date, prev_d: date) -> bool:
        """是否除权除息日。

        用于把"除权造成的假跳空"与"真实隔夜跳空"区分开。
        只作标记，**绝不用它去修正任何价格**。
        """
        return self.factor(d) != self.factor(prev_d)
