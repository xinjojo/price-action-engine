"""PriceBridge 测试 —— 除权日换算是最容易出错的一处。

Core 在【qfq 域】算结构，Execution 在【raw 域】判成交。
不换算就会差出复权倍数，而且不报错，只是回测曲线一直很漂亮。
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from price_action_engine.data.bridge import (
    PriceBridge,
    PriceBridgeError,
    PriceDomain,
)

from .fixtures import D

T0 = date(2026, 8, 20)   # 除权前一日
T1 = date(2026, 8, 21)   # 除权日
T2 = date(2026, 8, 24)   # 除权后

# 除权场景：8 月 21 日除权，价格腰斩，复权因子翻倍
#   raw:  20.00 → 10.00
#   hfq:  40.00 → 40.00（连续）
FACTORS = {
    T0: 2.0,
    T1: 4.0,
    T2: 4.0,
}


class TestForwardConversion(unittest.TestCase):
    def setUp(self):
        self.bridge = PriceBridge(FACTORS, source="tushare")

    def test_to_hfq(self):
        """后复权：raw × adj_factor。"""
        self.assertEqual(self.bridge.to_adjusted_price(D("20.00"), T0, PriceDomain.HFQ), D("40.00"))
        self.assertEqual(self.bridge.to_adjusted_price(D("10.00"), T1, PriceDomain.HFQ), D("40.00"))

    def test_to_qfq_relative_to_as_of(self):
        """前复权以 as_of 日因子为基准。"""
        # as_of = T1：以除权日为基准，T1 的 qfq 价 = raw 价
        self.assertEqual(
            self.bridge.to_adjusted_price(D("10.00"), T1, PriceDomain.QFQ, as_of=T1),
            D("10.00"),
        )
        # T0 的 qfq 价 = raw(T0) × f(T0) / f(T1) = 20 × 2/4 = 10
        self.assertEqual(
            self.bridge.to_adjusted_price(D("20.00"), T0, PriceDomain.QFQ, as_of=T1),
            D("10.00"),
        )

    def test_qfq_requires_as_of(self):
        with self.assertRaises(PriceBridgeError):
            self.bridge.to_adjusted_price(D("10.00"), T1, PriceDomain.QFQ)

    def test_to_adjusted_rejects_raw_target(self):
        with self.assertRaises(PriceBridgeError):
            self.bridge.to_adjusted_price(D("10.00"), T1, PriceDomain.RAW)


class TestReverseConversion(unittest.TestCase):
    def setUp(self):
        self.bridge = PriceBridge(FACTORS, source="tushare")

    def test_qfq_trigger_price_back_to_raw(self):
        """★ 核心用例：Setup 在 qfq 域给的触发价，必须换算回 raw 才能比对 K 线。

        qfq 触发价 10.50（as_of=T1）落在 T1（除权日，f=4）：
            raw = 10.50 × f(T1) / f(T1) = 10.50
        若换算落在 T0（除权前，f=2）：
            raw = 10.50 × f(T1) / f(T0) = 10.50 × 4/2 = 21.00
        """
        self.assertEqual(
            self.bridge.to_raw_price(D("10.50"), T1, PriceDomain.QFQ, as_of=T1),
            D("10.50"),
        )
        self.assertEqual(
            self.bridge.to_raw_price(D("10.50"), T0, PriceDomain.QFQ, as_of=T1),
            D("21.00"),
        )

    def test_from_hfq_to_raw(self):
        self.assertEqual(
            self.bridge.to_raw_price(D("40.00"), T1, PriceDomain.HFQ), D("10.00")
        )


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.bridge = PriceBridge(FACTORS, source="tushare")

    def test_raw_qfq_raw_roundtrip(self):
        for d, raw in [(T0, D("20.00")), (T1, D("10.00")), (T2, D("10.50"))]:
            for domain in (PriceDomain.QFQ, PriceDomain.HFQ):
                adj = self.bridge.to_adjusted_price(raw, d, domain, as_of=T1)
                back = self.bridge.to_raw_price(adj, d, domain, as_of=T1)
                self.assertEqual(back, raw, f"{d} {domain} 往返不一致")

    def test_convert_same_domain_is_identity(self):
        self.assertEqual(
            self.bridge.convert(D("10.00"), T1, PriceDomain.RAW, PriceDomain.RAW),
            D("10.00"),
        )

    def test_convert_cross_domain(self):
        """qfq → raw → hfq 全链路。"""
        qfq = D("10.00")
        raw = self.bridge.convert(qfq, T1, PriceDomain.QFQ, PriceDomain.RAW, as_of=T1)
        self.assertEqual(raw, D("10.00"))
        hfq = self.bridge.convert(qfq, T1, PriceDomain.QFQ, PriceDomain.HFQ, as_of=T1)
        self.assertEqual(hfq, D("40.00"))


class TestExDividendDetection(unittest.TestCase):
    def setUp(self):
        self.bridge = PriceBridge(FACTORS, source="tushare")

    def test_ex_dividend_date_detected(self):
        self.assertTrue(self.bridge.is_ex_dividend_date(T1, T0), "T1 因子翻倍 ⇒ 除权日")
        self.assertFalse(self.bridge.is_ex_dividend_date(T2, T1))

    def test_no_fake_price_action_gap_on_ex_dividend(self):
        """★ 除权日不得产生虚假 Price Action 跳空。

        raw 视角：开盘 10.00 vs 昨收 20.00 ⇒ 看起来暴跌 50%（假信号）
        qfq 视角：开盘 10.00 vs 昨收 10.00 ⇒ 跳空为 0（正确）
        """
        raw_open, raw_prev_close = D("10.00"), D("20.00")
        qfq_open = self.bridge.to_adjusted_price(raw_open, T1, PriceDomain.QFQ, as_of=T1)
        qfq_prev_close = self.bridge.to_adjusted_price(
            raw_prev_close, T0, PriceDomain.QFQ, as_of=T1
        )

        self.assertEqual(qfq_open, D("10.00"))
        self.assertEqual(qfq_prev_close, D("10.00"))
        self.assertEqual(qfq_open - qfq_prev_close, D("0.00"), "qfq 域无假跳空")

        # raw 域的事实跳空仍然存在，仅作记录
        self.assertEqual(raw_open - raw_prev_close, D("-10.00"))


class TestGuards(unittest.TestCase):
    def test_cross_source_is_rejected(self):
        """禁止跨源混用复权因子。"""
        bridge = PriceBridge(FACTORS, source="tushare")
        bridge.assert_source("tushare")  # 正常
        with self.assertRaises(PriceBridgeError):
            bridge.assert_source("sina")

    def test_anonymous_factor_source_rejected(self):
        with self.assertRaises(PriceBridgeError):
            PriceBridge(FACTORS, source="")

    def test_missing_factor(self):
        bridge = PriceBridge(FACTORS, source="tushare")
        with self.assertRaises(PriceBridgeError):
            bridge.factor(date(2020, 1, 1))

    def test_negative_price_rejected(self):
        bridge = PriceBridge(FACTORS, source="tushare")
        with self.assertRaises(PriceBridgeError):
            bridge.to_adjusted_price(D("-1.00"), T1, PriceDomain.HFQ)


if __name__ == "__main__":
    unittest.main()
