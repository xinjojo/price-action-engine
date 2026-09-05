"""Tushare Adapter 测试 —— 哨兵值转换与脱敏。

两条铁律：
  1. Core 永远看不到哨兵值 999999.999，只看到 PriceLimitMode.UNLIMITED
  2. Token 只从环境变量读取，代码里不允许出现
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal

from price_action_engine.data.providers.tushare_provider import (
    TushareProvider,
    convert_daily_row,
    convert_limit_row,
)
from price_action_engine.data.schema import (
    LimitSource,
    MarketStatus,
    PriceLimitMode,
    StockStatus,
)

from .fixtures import (
    RAW_ROW_LIMITED,
    RAW_ROW_MISSING,
    RAW_ROW_UNLIMITED_001248,
    RAW_ROW_UNLIMITED_300860,
    REAL_300860_20200824,
    D,
)


class TestSentinelContractGuard(unittest.TestCase):
    """★ 本轮新增：Provider 契约回归守卫。

    目标：如果未来 Tushare 改变"无限涨跌幅"的编码方式（哨兵值变化），
    这些测试必须【立即失败】，而不是继续默默把极端值当成正常涨跌停价。
    失败时人工检查 Tushare 当前 API 行为，更新 fixtures 与 adapter 判定。
    """

    def test_current_sentinel_value_is_documented(self):
        """当前实测哨兵值是 up=999999.999 / down=0.01。

        如果 Tushare 改了，RED：人工确认是否值得升级 Sentinel 判定。
        """
        self.assertEqual(
            (RAW_ROW_UNLIMITED_001248["up_limit"], RAW_ROW_UNLIMITED_001248["down_limit"]),
            (999999.999, 0.01),
            "Tushare 哨兵值发生变化 → 必须更新此处并重做 Provider Adapter",
        )
        self.assertEqual(
            (RAW_ROW_UNLIMITED_300860["up_limit"], RAW_ROW_UNLIMITED_300860["down_limit"]),
            (1000000.0, 0.01),
        )

    def test_sentinel_recognized_as_unlimited(self):
        """当前哨兵必须被识别为 UNLIMITED，up/down_limit 落 None。"""
        for fixture in (RAW_ROW_UNLIMITED_001248, RAW_ROW_UNLIMITED_300860):
            lim = convert_limit_row(fixture)
            self.assertIs(
                lim.mode, PriceLimitMode.UNLIMITED,
                f"哨兵值未被识别：{fixture}",
            )
            self.assertIsNone(lim.up_limit, "Core 不得看到哨兵数字")
            self.assertIsNone(lim.down_limit)

    def test_extreme_normal_price_not_misclassified(self):
        """★ 防回归：把一个正常的涨停价误判成 UNLIMITED 是头号静默错误。

        历史教训：St 股票 1 字头的涨停价（小数涨跌停）也落在 0.01/1.79 量级，
        adapter 必须【同时】检查 up >= 阈值 AND down <= 阈值 —— 单边不构成哨兵。
        """
        # 极端正常价：1000.00 元上方仍有限制
        lim_normal = convert_limit_row({
            "ts_code": "000001.SZ",
            "up_limit": 1000000.0,   # 巨大 but 不是哨兵
            "down_limit": 10000.0,   # 巨大 but 不是哨兵
        })
        self.assertIs(
            lim_normal.mode, PriceLimitMode.LIMITED,
            "正常大数值涨跌停价不应被误判成 UNLIMITED",
        )

        lim_down_only = convert_limit_row({
            "ts_code": "000001.SZ",
            "up_limit": 100.0,         # 正常涨停
            "down_limit": 0.01,        # 触底 —— 但 up 不满足哨兵条件
        })
        self.assertIs(
            lim_down_only.mode, PriceLimitMode.LIMITED,
            "down_limit=0.01 但 up_limit 正常 ⇒ LIMITED，不是 UNLIMITED",
        )

    def test_negative_one_is_not_treated_as_sentinel(self):
        """★ 防回归：第三方表格曾用 -1 表示无涨跌幅限制，但 Tushare 实际不用。

        我们的 adapter 只认定自己测得的哨兵值。任何 -1 必须：
          - 永不静默归类成 UNLIMITED；
          - 上抛可识别的 ValueError，让调用方看到"非 Tushare 真实契约"。
        """
        with self.assertRaises(ValueError) as ctx:
            convert_limit_row({
                "ts_code": "000001.SZ",
                "up_limit": -1.0,
                "down_limit": -1.0,
            })
        msg = str(ctx.exception)
        self.assertTrue(
            "涨跌停价必须为正" in msg or "UNKNOWN" in msg,
            f"必须明确报错：{msg}",
        )


class TestSentinelConversion(unittest.TestCase):
    """哨兵值必须在 adapter 内部被吃掉，不外泄到 Core。"""

    def test_unlimited_sentinel_001248(self):
        lim = convert_limit_row(RAW_ROW_UNLIMITED_001248)
        self.assertIs(lim.mode, PriceLimitMode.UNLIMITED)
        self.assertIsNone(lim.up_limit, "Core 不得看到哨兵值")
        self.assertIsNone(lim.down_limit)
        self.assertFalse(lim.has_price_limit)
        self.assertIs(lim.source, LimitSource.PROVIDER)

    def test_unlimited_sentinel_300860(self):
        """创业板注册制首批新股 2020-08-24：18 只全部命中哨兵值。"""
        lim = convert_limit_row(RAW_ROW_UNLIMITED_300860)
        self.assertIs(lim.mode, PriceLimitMode.UNLIMITED)

        # 与当日实际涨幅交叉验证：+43.10% > 20% 限制，必为无限制
        self.assertGreater(REAL_300860_20200824["pct_chg"], 20.0)

    def test_limited_row(self):
        lim = convert_limit_row(RAW_ROW_LIMITED)
        self.assertIs(lim.mode, PriceLimitMode.LIMITED)
        self.assertEqual(lim.up_limit, D("12.54"))
        self.assertEqual(lim.down_limit, D("10.26"))
        self.assertTrue(lim.has_price_limit)

    def test_missing_data_is_unknown_not_unlimited(self):
        """★ 数据缺失 ⇒ UNKNOWN，绝不能退化成 UNLIMITED。

        把"没有数据"当成"没有涨跌幅限制"是一类极危险的错误。
        """
        lim = convert_limit_row(RAW_ROW_MISSING)
        self.assertIs(lim.mode, PriceLimitMode.UNKNOWN)
        self.assertIsNone(lim.up_limit)
        self.assertIsNone(lim.down_limit)
        self.assertIs(lim.source, LimitSource.UNKNOWN)
        self.assertNotEqual(lim.mode, PriceLimitMode.UNLIMITED)

    def test_true_zero_is_not_treated_as_sentinel(self):
        """边界：down_limit=0.01 但 up_limit 正常 ⇒ 视为正常 LIMITED。

        防止"只看 down_limit"造成误判 —— 必须两个条件同时满足才是哨兵。
        """
        lim = convert_limit_row({"ts_code": "000001.SZ", "up_limit": 12.54,
                                 "down_limit": 0.01})
        self.assertIs(lim.mode, PriceLimitMode.LIMITED)


class TestDailyRowConversion(unittest.TestCase):
    def test_convert_daily_row(self):
        row = {
            "ts_code": "300869.SZ",
            "trade_date": "20200824",
            "open": 55.00, "high": 308.00, "low": 50.08, "close": 118.00,
            "pre_close": 10.16, "vol": 123456.0, "amount": 987654321.0,
        }
        bar = convert_daily_row(
            row,
            board="SZ_GEM",
            stock_status=StockStatus.NEW,
            market_status=MarketStatus.TRADABLE,
            adj_factor=1.0,
        )
        self.assertEqual(bar.symbol, "300869.SZ")
        self.assertEqual(bar.trade_date, date(2020, 8, 24))
        self.assertEqual(bar.raw_open, D("55.00"))
        self.assertEqual(bar.raw_close, D("118.00"))
        self.assertEqual(bar.volume, 123456.0)
        self.assertFalse(bar.is_single_price_bar)

    def test_single_price_property(self):
        bar = convert_daily_row(
            {"ts_code": "000001.SZ", "trade_date": "20260821",
             "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0,
             "pre_close": 10.0, "vol": 100.0, "amount": 1100.0},
            board="SZ_MAIN",
            stock_status=StockStatus.ST,
        )
        self.assertTrue(bar.is_single_price_bar)

    def test_missing_required_field_raises(self):
        with self.assertRaises(ValueError):
            convert_daily_row(
                {"ts_code": "000001.SZ", "trade_date": "20260821",
                 "open": 11.0, "high": 11.0, "low": 11.0},  # 缺 close / pre_close
                board="SZ_MAIN",
                stock_status=StockStatus.NORMAL,
            )


class TestTokenHandling(unittest.TestCase):
    """脱敏：token 只走环境变量。"""

    def test_missing_token_raises_with_guidance(self):
        env_backup = os.environ.pop("TUSHARE_TOKEN", None)
        try:
            with self.assertRaises(ValueError) as ctx:
                TushareProvider()
            self.assertIn("TUSHARE_TOKEN", str(ctx.exception))
        finally:
            if env_backup is not None:
                os.environ["TUSHARE_TOKEN"] = env_backup

    def test_token_read_from_env(self):
        os.environ["TUSHARE_TOKEN"] = "dummy_for_test"
        try:
            provider = TushareProvider.from_env()
            self.assertEqual(provider.name, "tushare")
            self.assertEqual(provider._token, "dummy_for_test")
        finally:
            os.environ.pop("TUSHARE_TOKEN", None)

    def test_no_token_literal_in_source(self):
        """源码里不得出现任何疑似 token 的 40 位十六进制串。"""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        pattern = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
        offenders = []
        for path in root.rglob("*.py"):
            if ".venv" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(str(path))
        self.assertEqual(offenders, [], "发现疑似 token 的字面量")


class TestNotImplementedBoundaries(unittest.TestCase):
    """明确标注 V0.2 才实现的接口 —— 宁可抛错，不要静默返回空。"""

    def setUp(self):
        os.environ["TUSHARE_TOKEN"] = "dummy_for_test"
        self.provider = TushareProvider.from_env()

    def tearDown(self):
        os.environ.pop("TUSHARE_TOKEN", None)

    def test_stock_status_not_implemented(self):
        with self.assertRaises(NotImplementedError) as ctx:
            self.provider.get_stock_status(["000001.SZ"], date(2026, 1, 1), date(2026, 8, 1))
        # 错误信息里带上已实测的枚举与坑，避免下次重新调研
        self.assertIn("撤消", str(ctx.exception))
        self.assertIn("撤销", str(ctx.exception))
        self.assertIn("suspend_timing", str(ctx.exception))

    def test_get_daily_price_requires_board_info(self):
        with self.assertRaises(NotImplementedError):
            self.provider.get_daily_price(["000001.SZ"], date(2026, 1, 1), date(2026, 8, 1))


if __name__ == "__main__":
    unittest.main()
