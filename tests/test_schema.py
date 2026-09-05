"""Schema 不变式测试 —— 三态语义与结构约束。"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from price_action_engine.data.schema import (
    DailyBar,
    DailyLimit,
    LimitSource,
    MarketConstraint,
    MarketStatus,
    PriceLimitMode,
    StockStatus,
)

from .fixtures import D


class TestDailyLimitInvariants(unittest.TestCase):
    def test_limited_requires_both_prices(self):
        with self.assertRaises(ValueError):
            DailyLimit(mode=PriceLimitMode.LIMITED, up_limit=D("11.0"))
        with self.assertRaises(ValueError):
            DailyLimit(mode=PriceLimitMode.LIMITED, down_limit=D("9.0"))

    def test_unlimited_must_have_no_prices(self):
        with self.assertRaises(ValueError):
            DailyLimit(mode=PriceLimitMode.UNLIMITED, up_limit=D("999999"))

    def test_unknown_must_have_no_prices(self):
        with self.assertRaises(ValueError):
            DailyLimit(mode=PriceLimitMode.UNKNOWN, up_limit=D("11.0"))

    def test_positive_prices_required(self):
        with self.assertRaises(ValueError):
            DailyLimit(mode=PriceLimitMode.LIMITED, up_limit=D("0"), down_limit=D("0"))

    def test_up_must_exceed_down(self):
        with self.assertRaises(ValueError):
            DailyLimit(mode=PriceLimitMode.LIMITED, up_limit=D("9.0"), down_limit=D("11.0"))

    def test_has_price_limit_property(self):
        lim = DailyLimit(mode=PriceLimitMode.LIMITED, up_limit=D("11.0"), down_limit=D("9.0"))
        self.assertTrue(lim.has_price_limit)
        self.assertFalse(DailyLimit(mode=PriceLimitMode.UNLIMITED).has_price_limit)
        self.assertFalse(DailyLimit(mode=PriceLimitMode.UNKNOWN).has_price_limit)

    def test_frozen(self):
        lim = DailyLimit(mode=PriceLimitMode.LIMITED, up_limit=D("11.0"), down_limit=D("9.0"))
        with self.assertRaises(Exception):
            lim.up_limit = D("12.0")  # type: ignore[misc]


class TestMarketStatusSemantics(unittest.TestCase):
    def test_halted_is_tradable_but_suspended_is_not(self):
        """盘中临停当日有成交，仍算可交易；全天停牌不算。"""
        base = dict(
            symbol="000001.SZ", trade_date=date(2026, 8, 21), trading_day_index=100,
            raw_open=D("10.0"), raw_high=D("10.5"), raw_low=D("9.9"),
            raw_close=D("10.3"), raw_pre_close=D("10.1"),
            volume=1000.0, amount=10300.0, adj_factor=1.0,
            adj_factor_source="tushare", board="SZ_MAIN",
            stock_status=StockStatus.NORMAL, quality_score=100.0,
            quality_flags=(), source="tushare", schema_version="0.1.1",
        )
        halted = DailyBar(market_status=MarketStatus.HALTED, **base)
        suspended = DailyBar(market_status=MarketStatus.SUSPENDED, **base)

        self.assertTrue(halted.is_tradable, "盘中临停当日有成交")
        self.assertFalse(suspended.is_tradable, "全天停牌无成交")


class TestMarketConstraintIsFactsOnly(unittest.TestCase):
    """market_constraint 只放客观事实，不含任何主观可交易性评分。"""

    def test_no_scoring_fields(self):
        mc = MarketConstraint(
            trade_date=date(2026, 8, 21),
            board="SZ_MAIN",
            stock_status=StockStatus.ST,
            market_status=MarketStatus.TRADABLE,
            limit=DailyLimit(mode=PriceLimitMode.LIMITED,
                             up_limit=D("2.06"), down_limit=D("1.68"),
                             source=LimitSource.PROVIDER),
        )
        field_names = {f for f in vars(mc)}
        self.assertNotIn("tradability_score", field_names)
        self.assertNotIn("tradability", field_names)

    def test_limit_mode_is_explicit(self):
        mc = MarketConstraint(
            trade_date=date(2026, 8, 21), board="SZ_MAIN",
            stock_status=StockStatus.NORMAL, market_status=MarketStatus.TRADABLE,
            limit=DailyLimit(mode=PriceLimitMode.UNKNOWN),
        )
        self.assertIs(mc.limit.mode, PriceLimitMode.UNKNOWN)
        self.assertIsNone(mc.limit.up_limit)


if __name__ == "__main__":
    unittest.main()
