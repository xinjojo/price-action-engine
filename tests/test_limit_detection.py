"""涨跌停事件检测测试 —— 含制度事实的实证据。"""

from __future__ import annotations

import unittest
from decimal import Decimal, ROUND_HALF_UP

from price_action_engine.data.schema import PriceLimitMode
from price_action_engine.market.china_a.limit_rules import (
    LIMIT_CENSORED,
    LIMIT_MODE_UNKNOWN,
    SINGLE_PRICE_NON_LIMIT,
    LimitStatus,
    classify_limit_event,
    volatility_flags,
)

from .fixtures import (
    REAL_300869_20200824,
    REAL_BSE_ST_30PCT,
    REAL_GEM_ST_20PCT,
    REAL_STAR_ST_20PCT,
    REAL_ST_10PCT_AFTER,
    REAL_ST_5PCT_BEFORE,
    TOL,
    D,
    limited,
    unlimited,
    unknown_limit,
)


class TestLimitStatusClassification(unittest.TestCase):
    def test_one_word_limit_up(self):
        """一字涨停：O=H=L=C=涨停价。"""
        ev = classify_limit_event(
            D("11.0"), D("11.0"), D("11.0"), D("11.0"), limited("11.00", "9.00"), TOL
        )
        self.assertIs(ev.status, LimitStatus.UP_LOCKED)
        self.assertTrue(ev.is_one_word_limit_up)
        self.assertFalse(ev.is_one_word_limit_down)
        self.assertTrue(ev.is_limit_locked)

    def test_one_word_limit_down(self):
        ev = classify_limit_event(
            D("9.0"), D("9.0"), D("9.0"), D("9.0"), limited("11.00", "9.00"), TOL
        )
        self.assertIs(ev.status, LimitStatus.DOWN_LOCKED)
        self.assertTrue(ev.is_one_word_limit_down)

    def test_locked_after_reopening_is_not_one_word(self):
        """涨停打开后收盘仍涨停：UP_LOCKED，但【不是】一字板。

        日内有价格区间（曾打开过），只是尾盘又封回去。
        这是"一字板"最容易被误判的情形。
        """
        ev = classify_limit_event(
            D("10.5"), D("11.0"), D("10.4"), D("11.0"), limited("11.00", "9.00"), TOL
        )
        self.assertIs(ev.status, LimitStatus.UP_LOCKED, "收盘封板")
        self.assertFalse(ev.is_one_word_limit_up, "日内有区间，不是一字板")
        self.assertFalse(ev.is_single_price_bar)

    def test_touch_then_break(self):
        """盘中触板后炸板：UP_TOUCH。"""
        ev = classify_limit_event(
            D("10.5"), D("11.0"), D("10.3"), D("10.6"), limited("11.00", "9.00"), TOL
        )
        self.assertIs(ev.status, LimitStatus.UP_TOUCH)
        self.assertFalse(ev.is_one_word_limit_up)
        self.assertFalse(ev.is_limit_locked)

    def test_down_touch(self):
        ev = classify_limit_event(
            D("9.5"), D("9.8"), D("9.0"), D("9.4"), limited("11.00", "9.00"), TOL
        )
        self.assertIs(ev.status, LimitStatus.DOWN_TOUCH)

    def test_none(self):
        ev = classify_limit_event(
            D("10.0"), D("10.5"), D("9.9"), D("10.3"), limited("12.54", "10.26"), TOL
        )
        self.assertIs(ev.status, LimitStatus.NONE)


class TestUnlimitedMode(unittest.TestCase):
    def test_new_stock_no_price_limit(self):
        """新股前 5 日无涨跌幅限制（真实样本 300869.SZ 2020-08-24）。

        交叉验证：当日实际涨幅 +1061.42%，若存在任何涨跌幅限制都不可能。
        """
        row = REAL_300869_20200824
        self.assertGreater(row["pct_chg"], 100.0, "当日涨幅超过 100%，必为无限制")

        ev = classify_limit_event(
            row["open"], row["high"], row["low"], row["close"], unlimited(), TOL
        )
        self.assertIs(ev.status, LimitStatus.NONE, "无限制 ⇒ 不可能有封板事件")
        self.assertFalse(ev.is_one_word_limit_up)
        self.assertFalse(ev.is_one_word_limit_down)
        self.assertEqual(ev.flags, ())


class TestUnknownModeIsNotUnlimited(unittest.TestCase):
    """★ UNKNOWN 绝不能被当成 UNLIMITED 使用。"""

    def test_unknown_returns_none_status(self):
        ev = classify_limit_event(
            D("10.0"), D("12.0"), D("9.0"), D("11.5"), unknown_limit(), TOL
        )
        # UNLIMITED 给的是 LimitStatus.NONE；UNKNOWN 给的是 None。两者必须可区分。
        self.assertIsNone(ev.status, "规则未知 ⇒ 无法判定，不是 NONE")
        self.assertIn(LIMIT_MODE_UNKNOWN, ev.flags)

    def test_unknown_differs_from_unlimited(self):
        unk = classify_limit_event(
            D("10.0"), D("12.0"), D("9.0"), D("11.5"), unknown_limit(), TOL
        )
        unl = classify_limit_event(
            D("10.0"), D("12.0"), D("9.0"), D("11.5"), unlimited(), TOL
        )
        self.assertIsNone(unk.status)
        self.assertIs(unl.status, LimitStatus.NONE)
        self.assertNotEqual(unk.status, unl.status)

    def test_single_price_bar_with_unknown_mode(self):
        ev = classify_limit_event(
            D("5.0"), D("5.0"), D("5.0"), D("5.0"), unknown_limit(), TOL
        )
        self.assertIsNone(ev.status)
        self.assertTrue(ev.is_single_price_bar)
        self.assertIn(LIMIT_MODE_UNKNOWN, ev.flags)
        self.assertIn(SINGLE_PRICE_NON_LIMIT, ev.flags)


class TestOfficialRuleFacts(unittest.TestCase):
    """交易所官方规则的实证据。

    来源：上交所公告 https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml
    《上海证券交易所交易规则（2026年修订）》于 **2026年7月6日起正式实施**，
    主板风险警示股票涨跌幅限制比例由 5% 调整为 10%。深交所、北交所同步。

    以下用 Tushare stk_limit 的真实涨跌停价验证该变更确实发生。
    """

    def test_main_board_st_was_5pct_before_2026_07_06(self):
        """2026-06-30：*ST美丽 pre_close 1.70 → up_limit 1.79（5%）。"""
        lim = REAL_ST_5PCT_BEFORE
        self.assertAlmostEqual(float(lim.up_limit / D("1.70") - 1), 0.05, places=2)
        self.assertAlmostEqual(float(1 - lim.down_limit / D("1.70")), 0.05, places=2)

    def test_main_board_st_is_10pct_from_2026_07_06(self):
        """2026-07-06：*ST美丽 pre_close 1.87 → up_limit 2.06（10%）。"""
        lim = REAL_ST_10PCT_AFTER
        self.assertAlmostEqual(float(lim.up_limit / D("1.87") - 1), 0.10, places=2)
        self.assertAlmostEqual(float(1 - lim.down_limit / D("1.87")), 0.10, places=2)

    def test_limit_price_uses_round_half_up(self):
        """涨跌停价按四舍五入到分（交易所口径），不是 Python 默认的银行家舍入。

        1.70 × 1.05 = 1.785 → 1.79（HALF_UP）
        若用银行家舍入会得到 1.78，与交易所不符。
        """
        computed = (D("1.70") * D("1.05")).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(computed, D("1.79"))
        self.assertEqual(computed, REAL_ST_5PCT_BEFORE.up_limit)

    def test_gem_st_is_20pct(self):
        """创业板风险警示股票 ±20%（2026-07-06 后维持不变）。"""
        lim = REAL_GEM_ST_20PCT
        self.assertAlmostEqual(float(lim.up_limit / D("2.20") - 1), 0.20, places=2)

    def test_star_st_is_20pct(self):
        """科创板风险警示股票 ±20%。"""
        lim = REAL_STAR_ST_20PCT
        self.assertAlmostEqual(float(lim.up_limit / D("7.90") - 1), 0.20, places=2)

    def test_bse_st_is_30pct(self):
        """北交所风险警示股票 ±30%。"""
        lim = REAL_BSE_ST_30PCT
        self.assertAlmostEqual(float(lim.up_limit / D("1.82") - 1), 0.30, places=2)


class TestVolatilityCensoringFlag(unittest.TestCase):
    def test_flag_present_for_locked(self):
        ev = classify_limit_event(
            D("11.0"), D("11.0"), D("11.0"), D("11.0"), limited("11.00", "9.00"), TOL
        )
        self.assertIn(LIMIT_CENSORED, volatility_flags([ev]))

    def test_flag_absent_for_normal(self):
        ev = classify_limit_event(
            D("10.0"), D("10.5"), D("9.9"), D("10.3"), limited("12.54", "10.26"), TOL
        )
        self.assertEqual(volatility_flags([ev]), ())

    def test_empty_window(self):
        self.assertEqual(volatility_flags([]), ())


class TestPriceLimitModeEnum(unittest.TestCase):
    """三态必须互斥且可区分。"""

    def test_three_distinct_states(self):
        modes = {m.value for m in PriceLimitMode}
        self.assertEqual(modes, {"limited", "unlimited", "unknown"})
        self.assertIsNot(PriceLimitMode.UNLIMITED, PriceLimitMode.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
