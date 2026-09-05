"""MarketDataProvider —— 数据层对上层的唯一出口。

上层禁止出现 tushare.* / mootdx.* 或任何第三方数据函数名。
由 tests/test_layering.py 强制。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Protocol, runtime_checkable

from ..schema import AdjustFactor, DailyBar, DailyLimit, StockStatus, TradeDay

__all__ = ["MarketDataProvider", "StockInfo", "StockStatusPeriod", "BoardPeriod"]


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    schema_version: str

    def get_daily_price(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[DailyBar]:
        """一律返回【不复权】原始 OHLCV + 当日 adj_factor。

        复权价不在此返回 —— 由 PriceBridge 在读取时派生。
        """
        ...

    def get_adjust_factor(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[AdjustFactor]: ...

    def get_trade_calendar(self, start: date, end: date) -> list[TradeDay]: ...

    def get_stock_list(self, as_of: date) -> list[StockInfo]: ...

    def get_stock_status(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[StockStatusPeriod]:
        """ST / 停牌 / 退市整理 / 新股 —— 全部为【带起止日期的时间段】。

        返回单点快照会在事件边界产生 off-by-one，是前视偏差的典型来源。
        """
        ...

    def get_board_history(self, symbol: str) -> list[BoardPeriod]:
        """板块归属时间段序列，不是单点。涨跌停规则按日期版本化，必须用时间段。"""
        ...

    def get_daily_limit(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[DailyLimit]:
        """交易所口径的每日涨跌停价。查不到必须返回 mode=UNKNOWN。"""
        ...


# ---- 辅助结构（V0.2 实现时落地为 dataclass）----

StockInfo = dict          # {symbol, name, market, list_date, delist_date}
StockStatusPeriod = dict  # {symbol, status: StockStatus, start_date, end_date}
BoardPeriod = dict        # {symbol, board, effective_from, effective_to}
