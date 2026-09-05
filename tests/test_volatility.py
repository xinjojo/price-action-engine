"""TR / Wilder ATR 测试。

裁决四（V0.1.1）：TR=0 的 bar **不跳过**，ATR 保持标准 Wilder 定义。
制度造成的观测压缩只用 LIMIT_CENSORED 标记表达，不改 ATR。
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from price_action_engine.features.volatility import true_range, wilder_atr
from price_action_engine.market.china_a.limit_rules import (
    LIMIT_CENSORED,
    LimitStatus,
    classify_limit_event,
    volatility_flags,
)

from .fixtures import D, TOL, limited, unlimited


class TestTrueRange(unittest.TestCase):
    def test_standard_definition(self):
        """TR = max(H-L, |H-C_prev|, |L-C_prev|)。"""
        self.assertEqual(
            true_range(D("10.5"), D("10.0"), D("10.2")), D("0.5")
        )
        # 跳空上行：H-L 小于跳空幅度时以跳空为准
        self.assertEqual(
            true_range(D("11.0"), D("10.9"), D("10.0")), D("1.0")
        )
        # 跳空下行
        self.assertEqual(
            true_range(D("9.5"), D("9.0"), D("10.0")), D("1.0")
        )

    def test_one_word_limit_bar_has_nonzero_tr(self):
        """一字板当日 TR 不为 0 —— TR 含跳空。

        这是 V0.1 判断失误的地方：曾担心"连续一字板导致 TR=0 拉低 ATR"。
        实际上首日一字板的跳空会贡献 TR；TR=0 只在价格完全不动时出现。
        """
        # 昨收 10.00，今日一字涨停 11.00
        self.assertEqual(
            true_range(D("11.0"), D("11.0"), D("10.0")), D("1.0")
        )

    def test_zero_tr_when_price_flat(self):
        """价格完全不动时 TR=0（如长期停牌后的一笔平价成交）。"""
        self.assertEqual(true_range(D("10.0"), D("10.0"), D("10.0")), Decimal(0))


class TestWilderAtr(unittest.TestCase):
    def test_seed_and_recursion(self):
        """前 period-1 项为 None；种子为简单均值；之后 Wilder 递推。"""
        trs = [D("2"), D("4"), D("6"), D("8")]
        atr = wilder_atr(trs, period=2)

        self.assertIsNone(atr[0])
        self.assertEqual(atr[1], D("3"))      # (2+4)/2
        self.assertEqual(atr[2], D("4.5"))    # (3*1 + 6)/2
        self.assertEqual(atr[3], D("6.25"))   # (4.5*1 + 8)/2

    def test_zero_tr_is_not_skipped(self):
        """★ 裁决四：TR=0 必须正常参与 Wilder 平滑，不得跳过。

        若跳过：ATR 会停在 2 不变。
        标准 Wilder：ATR[2] = (2*1 + 0)/2 = 1，ATR[3] = (1*1 + 0)/2 = 0.5。
        """
        trs = [D("2"), D("2"), D("0"), D("0")]
        atr = wilder_atr(trs, period=2)

        self.assertIsNone(atr[0])
        self.assertEqual(atr[1], D("2"))
        self.assertEqual(atr[2], D("1"), "TR=0 必须被平滑进去")
        self.assertEqual(atr[3], D("0.5"), "TR=0 必须被平滑进去")

    def test_insufficient_samples(self):
        trs = [D("1"), D("2")]
        self.assertEqual(wilder_atr(trs, period=5), [None, None])

    def test_empty(self):
        self.assertEqual(wilder_atr([], period=14), [])

    def test_invalid_period(self):
        with self.assertRaises(ValueError):
            wilder_atr([D("1")], period=0)


class TestLimitCensoringFlag(unittest.TestCase):
    def test_consecutive_one_word_limit_up_is_flagged_but_atr_unchanged(self):
        """连续两日一字涨停：ATR 按标准算，同时打 LIMIT_CENSORED。

        注意：一字板 H-L=0，【日内波动】被制度压缩到 0，TR 只剩跳空部分。
        这确实是"观测被截断"，但我们的做法是【打标记】，不是改 ATR。
        """
        lim_day1 = limited("11.00", "9.00")   # 昨收 10.00
        lim_day2 = limited("12.10", "9.90")   # 昨收 11.00

        bars = [
            # (open, high, low, close, prev_close, limit)
            (D("11.0"), D("11.0"), D("11.0"), D("11.0"), D("10.0"), lim_day1),
            (D("12.1"), D("12.1"), D("12.1"), D("12.1"), D("11.0"), lim_day2),
        ]

        events = [
            classify_limit_event(o, h, l, c, lim, TOL)
            for o, h, l, c, _, lim in bars
        ]
        trs = [true_range(h, l, pc) for _, h, l, _, pc, _ in bars]

        # 两天都是一字涨停
        self.assertTrue(all(e.is_one_word_limit_up for e in events))
        self.assertTrue(all(e.status is LimitStatus.UP_LOCKED for e in events))

        # TR 只有跳空部分（日内区间被制度压成 0）
        self.assertEqual(trs[0], D("1.0"))
        self.assertEqual(trs[1], D("1.1"))

        # ATR 严格按标准 Wilder 计算，无任何自定义跳过
        atr = wilder_atr(trs, period=2)
        self.assertIsNone(atr[0])
        self.assertEqual(atr[1], D("1.05"))  # (1.0 + 1.1) / 2

        # 同时存在制度截断标记
        flags = volatility_flags(events)
        self.assertIn(LIMIT_CENSORED, flags)

    def test_no_flag_for_normal_bars(self):
        events = [
            classify_limit_event(D("10.0"), D("10.5"), D("9.8"), D("10.3"),
                                 limited("12.54", "10.26"), TOL),
            classify_limit_event(D("10.3"), D("10.8"), D("10.1"), D("10.6"),
                                 limited("12.54", "10.26"), TOL),
        ]
        self.assertNotIn(LIMIT_CENSORED, volatility_flags(events))

    def test_touch_is_censored_too(self):
        """盘中触板（UP_TOUCH）也属于被制度截断。

        最高价被压在涨停板上 ⇒ 向上的波动无法被观测 ⇒ 该 bar 的区间是残缺的。
        """
        ev = classify_limit_event(D("10.0"), D("12.54"), D("9.9"), D("12.0"),
                                  limited("12.54", "10.26"), TOL)
        self.assertIs(ev.status, LimitStatus.UP_TOUCH)
        self.assertIn(LIMIT_CENSORED, volatility_flags([ev]))

        down = classify_limit_event(D("10.0"), D("10.5"), D("10.26"), D("10.4"),
                                    limited("12.54", "10.26"), TOL)
        self.assertIs(down.status, LimitStatus.DOWN_TOUCH)
        self.assertIn(LIMIT_CENSORED, volatility_flags([down]))


if __name__ == "__main__":
    unittest.main()
