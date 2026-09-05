"""测试 fixture —— 优先使用 Tushare 真实样本（2026-09-04 实测）。

真实样本出处见每处注释。合成样本仅在真实数据无法覆盖的边界情形使用，并明确标注。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from price_action_engine.data.schema import (
    DailyLimit,
    LimitSource,
    MarketStatus,
    PriceLimitMode,
    StockStatus,
)

TICK = Decimal("0.01")
TOL = Decimal("0.005")  # tick / 2

D = Decimal


# =====================================================================
# 真实 Tushare stk_limit 样本
# =====================================================================

# 000001.SZ（平安银行）2026-08-21：pre_close 11.40 / up 12.54 / down 10.26 —— 主板 ±10%
REAL_MAIN_10PCT = DailyLimit(
    mode=PriceLimitMode.LIMITED,
    up_limit=D("12.54"),
    down_limit=D("10.26"),
    source=LimitSource.PROVIDER,
)

# 000010.SZ（*ST美丽）2026-06-30：pre_close 1.70 / up 1.79 / down 1.62 —— 主板 ST ±5%
REAL_ST_5PCT_BEFORE = DailyLimit(
    mode=PriceLimitMode.LIMITED,
    up_limit=D("1.79"),
    down_limit=D("1.62"),
    source=LimitSource.PROVIDER,
)

# 000010.SZ（*ST美丽）2026-07-06：pre_close 1.87 / up 2.06 / down 1.68 —— 主板 ST ±10%
# ★ 这是"2026-07-06 起主板风险警示股 5%→10%"的直接实证
REAL_ST_10PCT_AFTER = DailyLimit(
    mode=PriceLimitMode.LIMITED,
    up_limit=D("2.06"),
    down_limit=D("1.68"),
    source=LimitSource.PROVIDER,
)

# 688022.SH（科创板 ST）2026-08-03：pre_close 7.90 / up 9.48 / down 6.32 —— 科创板 ±20%
REAL_STAR_ST_20PCT = DailyLimit(
    mode=PriceLimitMode.LIMITED,
    up_limit=D("9.48"),
    down_limit=D("6.32"),
    source=LimitSource.PROVIDER,
)

# 300010.SZ（创业板 ST）2026-08-03：pre_close 2.20 / up 2.64 / down 1.76 —— 创业板 ±20%
REAL_GEM_ST_20PCT = DailyLimit(
    mode=PriceLimitMode.LIMITED,
    up_limit=D("2.64"),
    down_limit=D("1.76"),
    source=LimitSource.PROVIDER,
)

# 920023.BJ（北交所 ST）2026-08-03：pre_close 1.82 / up 2.36 / down 1.28 —— 北交所 ±30%
REAL_BSE_ST_30PCT = DailyLimit(
    mode=PriceLimitMode.LIMITED,
    up_limit=D("2.36"),
    down_limit=D("1.28"),
    source=LimitSource.PROVIDER,
)

# ---- 无涨跌幅限制哨兵值的原始行（未转换，供 adapter 测试用）----

# 001248.SZ 2026-07-06 —— stk_limit 原始返回
RAW_ROW_UNLIMITED_001248: dict[str, Any] = {
    "trade_date": "20260706",
    "ts_code": "001248.SZ",
    "up_limit": 999999.999,
    "down_limit": 0.01,
}

# 300860.SZ 2020-08-24 —— 创业板注册制首批 18 只新股之一（前 5 日不限）
RAW_ROW_UNLIMITED_300860: dict[str, Any] = {
    "trade_date": "20200824",
    "ts_code": "300860.SZ",
    "up_limit": 1000000.0,
    "down_limit": 0.01,
}

# 000001.SZ 2026-08-21 —— 正常 ±10%
RAW_ROW_LIMITED: dict[str, Any] = {
    "trade_date": "20260821",
    "ts_code": "000001.SZ",
    "up_limit": 12.54,
    "down_limit": 10.26,
}

# 数据缺失：Tushare 返回 NaN（历史早期无该标的涨跌停记录）
RAW_ROW_MISSING: dict[str, Any] = {
    "trade_date": "20050104",
    "ts_code": "000001.SZ",
    "up_limit": float("nan"),
    "down_limit": float("nan"),
}

# ---- 真实 daily 样本（用于交叉验证"无涨跌幅限制"）----

# 300869.SZ（N康泰）2020-08-24：+1061.42%
# 与 stk_limit 的 UNLIMITED 哨兵相互印证 —— 若当日有 20% 限制不可能涨这么多
REAL_300869_20200824 = {
    "open": D("55.00"),
    "high": D("308.00"),
    "low": D("50.08"),
    "close": D("118.00"),
    "pre_close": D("10.16"),
    "pct_chg": 1061.4173,
}

# 300860.SZ 2020-08-24：+43.10%（>20%，同样证明当日无涨跌幅限制）
REAL_300860_20200824 = {
    "open": D("222.00"),
    "high": D("242.00"),
    "low": D("182.20"),
    "close": D("197.50"),
    "pre_close": D("138.02"),
    "pct_chg": 43.0952,
}


# =====================================================================
# 构造 helper
# =====================================================================

def limited(up: str, down: str) -> DailyLimit:
    return DailyLimit(
        mode=PriceLimitMode.LIMITED,
        up_limit=D(up),
        down_limit=D(down),
        source=LimitSource.PROVIDER,
    )


def unlimited() -> DailyLimit:
    return DailyLimit(mode=PriceLimitMode.UNLIMITED, source=LimitSource.PROVIDER)


def unknown_limit() -> DailyLimit:
    return DailyLimit(mode=PriceLimitMode.UNKNOWN, source=LimitSource.UNKNOWN)


def bar(
    o: str, h: str, l: str, c: str,
    *,
    board: str = "SZ_MAIN",
    stock_status: StockStatus = StockStatus.NORMAL,
    market_status: MarketStatus = MarketStatus.TRADABLE,
    trade_date: date = date(2026, 8, 21),
    pre_close: str | None = None,
) -> dict[str, Any]:
    """构造一根 bar 的原始参数包（供 features 直接使用）。"""
    return {
        "open_": D(o),
        "high": D(h),
        "low": D(l),
        "close": D(c),
        "board": board,
        "stock_status": stock_status,
        "market_status": market_status,
        "trade_date": trade_date,
        "pre_close": D(pre_close) if pre_close else None,
    }
