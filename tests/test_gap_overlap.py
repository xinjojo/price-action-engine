"""Gap 与 Overlap 测试。

Gap 必须保持独立特征 —— 不塞进虚拟 K 线。
一根一字涨停可以同时：bar_bias=+100、gap_up、is_one_word_limit_up。
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from price_action_engine.features.gap import compute_gap
from price_action_engine.features.overlap import overlap_ratio

from .fixtures import D


class TestGap(unittest.TestCase):
    def test_normal_gap_up(self):
        g = compute_gap(
            qfq_open=D("10.50"),
            qfq_prev_close=D("10.00"),
            raw_open=D("10.50"),
            raw_prev_close=D("10.00"),
            is_ex_dividend_date=False,
            atr=D("1.00"),
        )
        self.assertEqual(g.gap_size, D("0.50"))
        self.assertAlmostEqual(g.gap_percent, 0.05, places=6)
        self.assertAlmostEqual(g.gap_atr, 0.5, places=6)
        self.assertEqual(g.gap_direction, 1)
        self.assertFalse(g.is_ex_dividend_date)

    def test_gap_down(self):
        g = compute_gap(
            qfq_open=D("9.60"),
            qfq_prev_close=D("10.00"),
            raw_open=D("9.60"),
            raw_prev_close=D("10.00"),
            is_ex_dividend_date=False,
            atr=D("1.00"),
        )
        self.assertEqual(g.gap_size, D("-0.40"))
        self.assertEqual(g.gap_direction, -1)

    def test_no_gap(self):
        g = compute_gap(
            qfq_open=D("10.00"),
            qfq_prev_close=D("10.00"),
            raw_open=D("10.00"),
            raw_prev_close=D("10.00"),
            is_ex_dividend_date=False,
            atr=D("1.00"),
        )
        self.assertEqual(g.gap_size, D("0.00"))
        self.assertEqual(g.gap_direction, 0)

    def test_ex_dividend_produces_no_fake_pa_gap(self):
        """★ 除权日：raw 上是巨大向下跳空，qfq 上必须是 0。"""
        g = compute_gap(
            qfq_open=D("10.00"),        # 复权后与昨收一致
            qfq_prev_close=D("10.00"),
            raw_open=D("10.00"),        # 除权导致腰斩
            raw_prev_close=D("20.00"),
            is_ex_dividend_date=True,
            atr=D("1.00"),
        )
        self.assertEqual(g.gap_size, D("0.00"), "qfq 域不得出现除权造成的假跳空")
        self.assertEqual(g.gap_direction, 0)
        self.assertTrue(g.is_ex_dividend_date)
        self.assertEqual(g.raw_gap_size, D("-10.00"), "raw 事实跳空仍被记录")

    def test_one_word_limit_up_keeps_gap_as_separate_dimension(self):
        """一字涨停：bar_bias=+100 与 gap_up 是两个独立事实，不合并。"""
        g = compute_gap(
            qfq_open=D("11.00"),
            qfq_prev_close=D("10.00"),
            raw_open=D("11.00"),
            raw_prev_close=D("10.00"),
            is_ex_dividend_date=False,
            atr=D("1.00"),
        )
        self.assertEqual(g.gap_size, D("1.00"))
        self.assertEqual(g.gap_direction, 1)
        # is_one_word_limit_up 由 limit_rules 单独承载，gap 层不参与判定

    def test_gap_atr_none_without_atr(self):
        g = compute_gap(
            qfq_open=D("10.50"),
            qfq_prev_close=D("10.00"),
            raw_open=D("10.50"),
            raw_prev_close=D("10.00"),
            is_ex_dividend_date=False,
            atr=None,
        )
        self.assertIsNone(g.gap_atr)


class TestOverlap(unittest.TestCase):
    def test_full_overlap(self):
        self.assertAlmostEqual(
            overlap_ratio(D("10.5"), D("9.5"), D("10.5"), D("9.5")), 1.0, places=6
        )

    def test_partial_overlap(self):
        """当日 10.0~10.5，前一日 9.9~10.3 ⇒ 重叠 10.0~10.3 = 0.3，当日区间 0.5"""
        self.assertAlmostEqual(
            overlap_ratio(D("10.5"), D("10.0"), D("10.3"), D("9.9")), 0.6, places=6
        )

    def test_no_overlap_gap_up(self):
        self.assertEqual(
            overlap_ratio(D("11.0"), D("10.8"), D("10.2"), D("10.0")), 0.0
        )

    def test_zero_range_returns_none(self):
        """一字板（range=0）：重叠度不适用，返回 None 而不是 0。"""
        self.assertIsNone(overlap_ratio(D("11.0"), D("11.0"), D("10.5"), D("10.0")))


if __name__ == "__main__":
    unittest.main()
