"""Pullback Builder —— §十五–§十七，pullback 作为结构状态而非 Setup。

核心要点：
- pullback.direction = -current_leg.direction（与 leg 反方向）
- 当 current_leg UP：pullback 从当前 leg 起点（Swing LOW）反向朝下测深度
- 当 current_leg DOWN：pullback 从当前 leg 起点（Swing HIGH）反向朝上测反弹
- 输出原始事实：duration_bars / depth_abs / depth_pct_of_prior_leg / depth_atr
- 不要「healthy_pullback = True」这种 boolean 压缩（§十七），留给 Setup / Context Filter
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from price_action_engine.structure.types import (
    LegState,
    PriceDomain,
    PullbackState,
    StructureBar,
    StructurePrice,
    SwingKind,
    SwingPoint,
)


class PullbackBuilder:
    def build(
        self,
        *,
        confirmed_swings: Sequence[SwingPoint],
        legs: tuple[LegState | None, LegState | None],
        bars: Sequence[StructureBar],
        as_of_index: int,
    ) -> PullbackState | None:
        """Parameters
        ----------
        confirmed_swings : Sequence[SwingPoint]
            全部 confirmed swings（升序）。
        legs : (previous_confirmed_leg, current_leg)
            由 LegBuilder 输出。可能 both None。
        bars / as_of_index : 与 LegBuilder 一致。
        """
        previous_leg, current_leg = legs
        if current_leg is None or current_leg.direction == 0:
            return None

        # pullback 方向与 current leg 反向
        pullback_direction = -current_leg.direction

        # 当前 leg 的起始 swing（如果是 developing leg 且 prior 无 swing，
        # 则取窗口第一根 bar 作起点）
        if confirmed_swings:
            origin_swing = confirmed_swings[-1]
            origin_index = origin_swing.event_index
            origin_date = origin_swing.event_date
            origin_extreme_qfq = origin_swing.price.analysis
            origin_extreme_raw = origin_swing.price.raw
            anchor_date = origin_swing.price.anchor_date
        else:
            # 没有 confirmed swing，从 bars[0] 开始
            first = bars[0]
            origin_index = first.bar_index
            origin_date = first.bar_date
            origin_extreme_qfq = first.close_qfq
            origin_extreme_raw = first.raw_close
            anchor_date = None

        # 在 origin_index+1..as_of_index 范围内找反向 extreme
        extreme_qfq: Decimal | None = None
        extreme_index = origin_index
        extreme_date = origin_date
        extreme_raw = None

        for i in range(origin_index + 1, as_of_index + 1):
            b = bars[i]
            if b.is_suspended:
                continue
            if extreme_qfq is None:
                if pullback_direction == -1:
                    extreme_qfq = b.low_qfq
                    extreme_raw = b.raw_low
                else:
                    extreme_qfq = b.high_qfq
                    extreme_raw = b.raw_high
                extreme_index = b.bar_index
                extreme_date = b.bar_date
                continue

            if pullback_direction == -1 and b.low_qfq < extreme_qfq:
                extreme_qfq = b.low_qfq
                extreme_raw = b.raw_low
                extreme_index = b.bar_index
                extreme_date = b.bar_date
            if pullback_direction == +1 and b.high_qfq > extreme_qfq:
                extreme_qfq = b.high_qfq
                extreme_raw = b.raw_high
                extreme_index = b.bar_index
                extreme_date = b.bar_date

        if extreme_qfq is None:
            return None

        # 当前价格（as_of_index 的 close）作 current_price
        last = bars[as_of_index]
        current_qfq = last.close_qfq
        current_raw = last.raw_close

        # depth_abs (qfq)
        depth_abs = origin_extreme_qfq - extreme_qfq
        if pullback_direction == +1:
            depth_abs = extreme_qfq - origin_extreme_qfq
        if depth_abs < 0:
            depth_abs = Decimal(0)  # 还未发生明显回撤

        # depth_pct_of_prior_leg：§十六 最重要
        depth_pct_prior_leg: float | None = None
        atr_distance: Decimal | None = None
        prior_leg_qfq_net: Decimal | None = None

        if previous_leg is not None and previous_leg.net_move is not None:
            # 注意 previous_leg.net_move 在 leg.py 中被定义过（绝对值已是 leg 长度），
            # 我们需要的是 prior leg 的「绝对推进幅度」（价格差）。direction 不影响正负。
            # 这里 previous_leg.net_move 是 Decimal 绝对幅度；如果 prior leg 方向 = ±1，
            # 实际就是 from-to 之差。我们这里直接用 net_move 的绝对值。
            prior_leg_qfq_net = previous_leg.net_move
            if prior_leg_qfq_net > 0:
                depth_pct_prior_leg = float(depth_abs / prior_leg_qfq_net)
        elif current_leg is not None and current_leg.net_move is not None:
            # 没有 previous_confirmed_leg 时，用 current_leg 已推进幅度
            prior_leg_qfq_net = current_leg.net_move
            if prior_leg_qfq_net > 0:
                depth_pct_prior_leg = float(depth_abs / prior_leg_qfq_net)

        # depth_atr
        atr_val = self._atr_at_bar(bars, origin_index)
        if atr_val is not None and atr_val > 0:
            atr_distance = depth_abs / atr_val

        # duration：实际可交易 bars
        dur = self._count_tradeable_bars(bars, origin_index, as_of_index)

        extreme_struct = StructurePrice(
            raw=extreme_raw if extreme_raw is not None else extreme_qfq,
            analysis=extreme_qfq,
            domain=PriceDomain.QFQ,
            anchor_date=anchor_date,
        )
        current_struct = StructurePrice(
            raw=current_raw,
            analysis=current_qfq,
            domain=PriceDomain.QFQ,
            anchor_date=anchor_date,
        )

        return PullbackState(
            direction=pullback_direction,
            is_active=(extreme_qfq != origin_extreme_qfq),  # 至少有 1 个反向 bar 才算 active
            is_current=True,
            start_date=origin_date,
            start_index=origin_index,
            extreme_price=extreme_struct,
            current_price=current_struct,
            duration_bars=dur,
            depth_abs=depth_abs,
            depth_pct_of_prior_leg=depth_pct_prior_leg,
            depth_atr=atr_distance,
        )

    @staticmethod
    def _count_tradeable_bars(bars: Sequence[StructureBar], start: int, end: int) -> int:
        if start < 0 or end < start:
            return 0
        count = 0
        for i in range(start, end + 1):
            if i >= len(bars):
                break
            if bars[i].is_suspended:
                continue
            count += 1
        return count

    @staticmethod
    def _atr_at_bar(bars: Sequence[StructureBar], idx: int) -> Decimal | None:
        if idx < 0 or idx >= len(bars):
            return None
        return bars[idx].atr_qfq


__all__ = ["PullbackBuilder"]
