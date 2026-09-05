"""单 bar 原子特征测试 —— 含一字板 Price Action 语义覆盖。"""

from __future__ import annotations

import unittest
from decimal import Decimal

from price_action_engine.features.bar import (
    BarInterpretationSource,
    compute_bar_features,
)
from price_action_engine.market.china_a.limit_rules import (
    LimitStatus,
    classify_limit_event,
)

from .fixtures import D, TOL, limited, unlimited


class TestNormalBars(unittest.TestCase):
    """普通 K 线：几何定义必须精确。"""

    def test_strong_bull_bar(self):
        """普通大阳线。"""
        ev = classify_limit_event(
            D("10.0"), D("10.9"), D("9.9"), D("10.8"),
            limited("12.54", "10.26"), TOL,
        )
        f = compute_bar_features(
            open_=D("10.0"), high=D("10.9"), low=D("9.9"), close=D("10.8"),
            limit_event=ev, atr=D("1.0"), cap_atr=3.0,
        )

        self.assertAlmostEqual(f.body_ratio, 0.8, places=6)
        self.assertAlmostEqual(f.upper_wick_ratio, 0.1, places=6)
        self.assertAlmostEqual(f.lower_wick_ratio, 0.1, places=6)
        self.assertAlmostEqual(f.close_location, 0.9, places=6)
        self.assertFalse(f.is_single_price_bar)
        # 100 * (0.8 / (3*1.0)) * body_ratio 0.8
        self.assertAlmostEqual(f.bar_bias, 21.333333, places=4)
        self.assertAlmostEqual(f.bar_control_score, 21.333333, places=4)
        self.assertIs(f.interpretation_source, BarInterpretationSource.GEOMETRY)
        self.assertIs(ev.status, LimitStatus.NONE)

    def test_doji(self):
        """普通十字星：实体极小，方向控制应接近 0。"""
        ev = classify_limit_event(
            D("10.0"), D("10.5"), D("9.5"), D("10.02"),
            limited("12.54", "10.26"), TOL,
        )
        f = compute_bar_features(
            open_=D("10.0"), high=D("10.5"), low=D("9.5"), close=D("10.02"),
            limit_event=ev, atr=D("1.0"), cap_atr=3.0,
        )

        self.assertAlmostEqual(f.body_ratio, 0.02, places=6)
        self.assertAlmostEqual(f.upper_wick_ratio, 0.48, places=6)
        self.assertAlmostEqual(f.lower_wick_ratio, 0.5, places=6)
        self.assertAlmostEqual(f.close_location, 0.52, places=6)
        self.assertLess(abs(f.bar_bias), 1.0, "十字星的方向控制应接近 0")
        self.assertIs(f.interpretation_source, BarInterpretationSource.GEOMETRY)

    def test_atr_unavailable_yields_none(self):
        """ATR 预热不足时 bar_bias 为 None，绝不返回 0。"""
        ev = classify_limit_event(
            D("10.0"), D("10.9"), D("9.9"), D("10.8"),
            limited("12.54", "10.26"), TOL,
        )
        f = compute_bar_features(
            open_=D("10.0"), high=D("10.9"), low=D("9.9"), close=D("10.8"),
            limit_event=ev, atr=None, cap_atr=3.0,
        )
        self.assertIsNone(f.bar_bias)
        self.assertIsNone(f.bar_control_score)
        self.assertIn("ATR_UNAVAILABLE", f.flags)


class TestOneWordLimit(unittest.TestCase):
    """一字板：几何为 None，语义走制度覆盖。"""

    def _event(self, o, h, l, c, lim):
        return classify_limit_event(D(o), D(h), D(l), D(c), lim, TOL)

    def test_one_word_limit_up(self):
        """一字涨停：raw O=H=L=C=11，必须判成极强买方控制而非十字星。"""
        lim = limited("11.00", "9.00")
        ev = self._event("11.0", "11.0", "11.0", "11.0", lim)

        self.assertTrue(ev.is_one_word_limit_up)
        self.assertIs(ev.status, LimitStatus.UP_LOCKED)

        f = compute_bar_features(
            open_=D("11.0"), high=D("11.0"), low=D("11.0"), close=D("11.0"),
            limit_event=ev, atr=D("1.0"),
        )

        # 关键：几何字段是 None（不适用），不是 0
        self.assertIsNone(f.body_ratio)
        self.assertIsNone(f.close_location)
        self.assertIsNone(f.upper_wick_ratio)
        self.assertIsNone(f.lower_wick_ratio)
        # 语义覆盖
        self.assertEqual(f.bar_bias, 100.0)
        self.assertEqual(f.bar_control_score, 100.0)
        self.assertIs(f.interpretation_source, BarInterpretationSource.LIMIT_EVENT)
        self.assertTrue(f.is_single_price_bar)

    def test_one_word_limit_down(self):
        """一字跌停：镜像。"""
        lim = limited("11.00", "9.00")
        ev = self._event("9.0", "9.0", "9.0", "9.0", lim)

        self.assertTrue(ev.is_one_word_limit_down)
        self.assertIs(ev.status, LimitStatus.DOWN_LOCKED)

        f = compute_bar_features(
            open_=D("9.0"), high=D("9.0"), low=D("9.0"), close=D("9.0"),
            limit_event=ev, atr=D("1.0"),
        )
        self.assertIsNone(f.body_ratio)
        self.assertEqual(f.bar_bias, -100.0)
        self.assertEqual(f.bar_control_score, 100.0)

    def test_single_price_bar_not_a_limit_event(self):
        """单点成交但非涨跌停：不能判成一字板。"""
        # 冷门股全天只有一笔成交，收在 5.00，当日无涨跌幅限制
        lim = unlimited()
        ev = self._event("5.0", "5.0", "5.0", "5.0", lim)

        self.assertTrue(ev.is_single_price_bar, "几何上是单点 bar")
        self.assertFalse(ev.is_one_word_limit_up, "但绝非一字涨停")
        self.assertFalse(ev.is_one_word_limit_down)
        self.assertIs(ev.status, LimitStatus.NONE)
        self.assertIn("SINGLE_PRICE_NON_LIMIT", ev.flags)

        f = compute_bar_features(
            open_=D("5.0"), high=D("5.0"), low=D("5.0"), close=D("5.0"),
            limit_event=ev, atr=D("1.0"),
        )
        self.assertIsNone(f.body_ratio)
        self.assertIsNone(f.bar_bias, "无制度事件可做语义覆盖，只能是 None")


if __name__ == "__main__":
    unittest.main()
