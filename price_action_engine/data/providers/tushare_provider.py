"""Tushare Pro Provider —— V0.1.1 最小骨架。

两条铁律：
    1. Token 只从环境变量读取，**禁止写入代码或配置文件**。
    2. 所有 Tushare 专有约定（含无涨跌幅限制哨兵值）只在本文件内部转换。
       Core 完全不知道哨兵值的存在 —— 它只看到 PriceLimitMode。

关于哨兵值（2026-09-04 实测，见 docs/DATA_LAYER.md §2.4）：
    Tushare stk_limit 用 **up_limit=999999.999 / down_limit=0.01** 表示当日无涨跌幅限制。
    验证方式：
      - 2020-08-24 创业板注册制首批新股，命中 18 只（与公开的"首批 18 家"完全吻合）
      - 2026-07-06 / 2025-08-15 / 2021-12-01 各命中 1 只，均为新股
      - 与 daily.pct_chg 交叉验证：300869.SZ 2020-08-24 实际涨幅 +1061%
      - 全部扫描日期中 **负值 -1 零命中**
    ⚠️ V0.1 文档曾写作 -1，那是第三方数据表的表示，不是 Tushare 的。已修正。
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ...config import CONFIG
from ..schema import (
    AdjustFactor,
    DailyBar,
    DailyLimit,
    LimitSource,
    MarketStatus,
    PriceLimitMode,
    StockStatus,
    TradeDay,
)

# ---- 哨兵值：仅在本模块内可见 ----
_NO_LIMIT_UP = CONFIG.data.tushare_no_limit_up          # 999999
_NO_LIMIT_DOWN = CONFIG.data.tushare_no_limit_down      # 0.01


# =====================================================================
# 纯转换函数 —— 不依赖网络，单元测试直接喂 fixture
# =====================================================================

def _dec(value: Any) -> Decimal | None:
    """把 Tushare 返回值转 Decimal。None / NaN / 空串 → None。"""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return Decimal(str(value))


def convert_limit_row(
    row: Mapping[str, Any],
    *,
    symbol: str | None = None,
    trade_date: date | None = None,
    rule_id: str | None = None,
) -> DailyLimit:
    """把 stk_limit 的一行转成 DailyLimit。

    哨兵值在此转换为 PriceLimitMode.UNLIMITED，调用方永远看不到 999999。
    数据缺失（None / NaN）→ UNKNOWN，绝不猜。
    """
    up = _dec(row.get("up_limit"))
    down = _dec(row.get("down_limit"))

    if up is None or down is None:
        return DailyLimit(mode=PriceLimitMode.UNKNOWN, source=LimitSource.UNKNOWN)

    if up >= _NO_LIMIT_UP and down <= _NO_LIMIT_DOWN:
        return DailyLimit(
            mode=PriceLimitMode.UNLIMITED,
            rule_id=rule_id,
            source=LimitSource.PROVIDER,
        )

    return DailyLimit(
        mode=PriceLimitMode.LIMITED,
        up_limit=up,
        down_limit=down,
        rule_id=rule_id,
        source=LimitSource.PROVIDER,
    )


def convert_daily_row(
    row: Mapping[str, Any],
    *,
    board: str,
    stock_status: StockStatus,
    market_status: MarketStatus = MarketStatus.TRADABLE,
    adj_factor: float | None = None,
    adj_factor_source: str = "tushare",
    trading_day_index: int | None = None,
    quality_score: float = 100.0,
    quality_flags: tuple[str, ...] = (),
) -> DailyBar:
    """把 daily 的一行转成 DailyBar。

    board / stock_status 必须由调用方提供 —— 本模块不猜板块、不猜状态。
    """
    symbol = str(row["ts_code"])
    trade_date = _parse_date(row["trade_date"])

    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        trading_day_index=trading_day_index,
        raw_open=_require_dec(row, "open"),
        raw_high=_require_dec(row, "high"),
        raw_low=_require_dec(row, "low"),
        raw_close=_require_dec(row, "close"),
        raw_pre_close=_require_dec(row, "pre_close"),
        volume=float(row.get("vol") or 0.0),
        amount=float(row.get("amount") or 0.0),
        adj_factor=float(adj_factor) if adj_factor is not None else 1.0,
        adj_factor_source=adj_factor_source,
        board=board,
        stock_status=stock_status,
        market_status=market_status,
        quality_score=quality_score,
        quality_flags=quality_flags,
        source="tushare",
        schema_version=CONFIG.data.schema_version,
    )


def _require_dec(row: Mapping[str, Any], key: str) -> Decimal:
    value = _dec(row.get(key))
    if value is None:
        raise ValueError(f"daily 行缺少必需字段 {key!r}: {dict(row)}")
    return value


def _parse_date(value: Any) -> date:
    s = str(value)
    return datetime.strptime(s, "%Y%m%d").date()


# =====================================================================
# Provider 骨架
# =====================================================================

class TushareProvider:
    """Tushare Pro 数据提供器（最小骨架）。

    Usage:
        provider = TushareProvider.from_env()   # 读取 TUSHARE_TOKEN
    """

    name = "tushare"
    schema_version = CONFIG.data.schema_version

    def __init__(self, token: str | None = None, timeout: float = 30.0) -> None:
        self._token = token or os.environ.get("TUSHARE_TOKEN")
        if not self._token:
            raise ValueError(
                "缺少 Tushare token。请设置环境变量 TUSHARE_TOKEN，"
                "或参考 .env.example 创建本地 .env（已被 .gitignore 忽略）。"
                "严禁把 token 写入代码或配置。"
            )
        self._timeout = timeout
        self._pro: Any = None

    @classmethod
    def from_env(cls, env_var: str = "TUSHARE_TOKEN", **kwargs: Any) -> TushareProvider:
        token = os.environ.get(env_var)
        if not token:
            raise ValueError(f"环境变量 {env_var} 未设置")
        return cls(token=token, **kwargs)

    @property
    def api(self) -> Any:
        """懒加载 tushare —— 使本模块在无 tushare 环境下仍可导入与单测。"""
        if self._pro is None:
            import tushare as ts  # 局部导入：避免硬依赖

            self._pro = ts.pro_api(self._token, timeout=self._timeout)
        return self._pro

    # ---- 已实现 ----

    def get_daily_limit(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[DailyLimit]:
        """交易所口径每日涨跌停价。按标的批量拉取。"""
        for symbol in symbols:
            df = self.api.stk_limit(
                ts_code=symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                fields="trade_date,ts_code,up_limit,down_limit",
            )
            if df is None or df.empty:
                continue
            for row in df.to_dict("records"):
                yield convert_limit_row(row, symbol=symbol)

    def get_daily_price(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[DailyBar]:
        """不复权日 K。board / stock_status 需调用方补齐（V0.2 接 stock_basic）。"""
        raise NotImplementedError(
            "get_daily_price 需要 board / stock_status，"
            "待 get_board_history 与 get_stock_status 在 V0.2 落地后实现"
        )

    def get_adjust_factor(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[AdjustFactor]:
        for symbol in symbols:
            df = self.api.adj_factor(
                ts_code=symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            if df is None or df.empty:
                continue
            for row in df.to_dict("records"):
                yield AdjustFactor(
                    symbol=symbol,
                    trade_date=_parse_date(row["trade_date"]),
                    adj_factor=float(row["adj_factor"]),
                    source="tushare",
                )

    def get_trade_calendar(self, start: date, end: date) -> list[TradeDay]:
        df = self.api.trade_cal(
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        days: list[TradeDay] = []
        index = 0
        for row in df.sort_values("cal_date").to_dict("records"):
            d = _parse_date(row["cal_date"])
            is_open = str(row["is_open"]) == "1"
            if is_open:
                index += 1
            days.append(TradeDay(trade_date=d, is_open=is_open, trading_day_index=index))
        return days

    # ---- V0.2 ----

    def get_stock_list(self, as_of: date) -> list[dict]:
        raise NotImplementedError("V0.2：需 stock_basic")

    def get_stock_status(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[dict]:
        raise NotImplementedError(
            "V0.2：namechange + suspend_d。\n"
            "已实测枚举（2026-09-04）：change_reason ∈ "
            "['*ST', 'ST', '从ST变为*ST', '其他', '撤消*ST并实行ST', '撤销ST', '退市整理期']\n"
            "⚠️ 注意 Tushare 自身用字不一致：「撤消*ST并实行ST」用 消，「撤销ST」用 销，必须都匹配。\n"
            "suspend_d.suspend_timing 非空 = 盘中临停（当日有成交）；"
            "为空 = 全天停牌。suspend_type 混有 S(停牌)/R(复牌)。"
        )

    def get_board_history(self, symbol: str) -> list[dict]:
        raise NotImplementedError("V0.2")
