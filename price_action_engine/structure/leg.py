"""Leg Builder —— 把 Swing 序列拼成 leg。

按 V0.2 指令 §十二–§十四 实现：

- 一条 leg = 两个相邻结构转折点之间的 directional move
- current leg = 尚未结束的 developing leg（§十三）
- 输出原始事实项，不拍 0~100 leg_strength（§十四）
- duration_bars 按 tradeable bars 计（§二十），停牌日跳过
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from price_action_engine.structure.types import (
    LegState,
    PriceDomain,
    StructureBar,
    StructurePrice,
    SwingKind,
    SwingPoint,
)


class LegBuilder:
    """把 Swing 序列 + 当前 developing extreme 转成 previous/current legs。"""

    def build(
        self,
        *,
        confirmed_swings: Sequence[SwingPoint],
        bars: Sequence[StructureBar],
        as_of_index: int,
    ) -> tuple[LegState | None, LegState | None]:
        """Parameters
        ----------
        confirmed_swings : Sequence[SwingPoint]
            swing detector 输出的、按 ``event_index`` 升序的 confirmed 序列。
        bars : Sequence[StructureBar]
            整个窗口的 bars（停牌日保留占位也行，但 leg duration 不数它们）。
        as_of_index : int
            当前最后一个 bar 在 bars 中的索引。

        Returns
        -------
        (previous_confirmed_leg, current_developing_leg)
            至多各一个；都可能是 None（窗口里数据不足时）。
        """
        if as_of_index < 0 or as_of_index >= len(bars):
            raise ValueError("as_of_index out of range")

        # ── 当前最后一根 bar 的 close_qfq 与 raw 用于 current leg 的 current_price
        last_bar = bars[as_of_index]
        if last_bar.is_suspended:
            current_close_qfq = last_bar.close_qfq  # 用 prev_close 替代（已由上层代入）
            current_close_raw = last_bar.raw_close
        else:
            current_close_qfq = last_bar.close_qfq
            current_close_raw = last_bar.raw_close

        # ── 上一条 confirmed leg = 最后两个相邻 confirmed swing ──
        previous = self._build_previous(confirmed_swings, bars)

        # ── current leg = 从最后 confirmed swing 到 as_of_index 的 developing ──
        current = self._build_current(
            confirmed_swings=confirmed_swings,
            bars=bars,
            as_of_index=as_of_index,
            current_close_qfq=current_close_qfq,
            current_close_raw=current_close_raw,
        )

        return previous, current

    # ────────────────────────────────────────────────────────────────

    def _build_previous(
        self,
        swings: Sequence[SwingPoint],
        bars: Sequence[StructureBar],
    ) -> LegState | None:
        if len(swings) < 2:
            return None
        a, b = swings[-2], swings[-1]
        if a.kind == b.kind:
            # 不应该发生：相邻两个 confirmed swing 必须一 high 一 low
            return None

        if a.kind == SwingKind.HIGH and b.kind == SwingKind.LOW:
            direction = -1
        else:
            direction = +1

        return self._mk_leg(
            direction=direction,
            is_confirmed=True,
            is_current=False,
            start_swing=a,
            end_swing=b,
            bars=bars,
            start_index_for_duration=a.event_index,
            end_index_for_duration=b.event_index,
        )

    def _build_current(
        self,
        *,
        confirmed_swings: Sequence[SwingPoint],
        bars: Sequence[StructureBar],
        as_of_index: int,
        current_close_qfq: Decimal,
        current_close_raw: Decimal | None,
    ) -> LegState | None:
        last_swing = confirmed_swings[-1] if confirmed_swings else None

        if last_swing is None:
            # 没有 confirmed swing。把当前 developing leg 从 bars[0] 算到 as_of_index
            direction = +1
            start_index = 0
            start_bar = bars[0]
            start_swing_price = StructurePrice(
                raw=start_bar.raw_close if start_bar.raw_close is not None else start_bar.close_qfq,
                analysis=start_bar.close_qfq,
                domain=PriceDomain.QFQ,
                anchor_date=None,
            )
            start_date = start_bar.bar_date
        else:
            if last_swing.kind == SwingKind.HIGH:
                direction = -1  # price coming down from a high
            else:
                direction = +1
            start_index = last_swing.event_index
            start_date = last_swing.event_date
            start_swing_price = last_swing.price

        # 找到 start_index+1..as_of_index 范围内（含）的 extreme
        extreme_price_qfq: Decimal | None = None
        extreme_bar_index = start_index
        extreme_bar_date = start_date

        for i in range(start_index, as_of_index + 1):
            b = bars[i]
            if b.is_suspended:
                continue
            if extreme_price_qfq is None:
                extreme_price_qfq = (
                    b.high_qfq if direction == +1 else b.low_qfq
                )
                extreme_bar_index = b.bar_index
                extreme_bar_date = b.bar_date
                continue
            if direction == +1 and b.high_qfq > extreme_price_qfq:
                extreme_price_qfq = b.high_qfq
                extreme_bar_index = b.bar_index
                extreme_bar_date = b.bar_date
            if direction == -1 and b.low_qfq < extreme_price_qfq:
                extreme_price_qfq = b.low_qfq
                extreme_bar_index = b.bar_index
                extreme_bar_date = b.bar_date

        if extreme_price_qfq is None:
            return None

        # end = 当前位置（as_of_index 的 close_qfq）
        end_price_qfq = current_close_qfq
        end_index = as_of_index
        end_date = bars[as_of_index].bar_date

        net_move = end_price_qfq - start_swing_price.analysis
        if direction == -1:
            net_move = start_swing_price.analysis - extreme_price_qfq

        # 实际上 current leg 的 net_move 用 extreme 而不是 end，
        # 因为 leg 是从起点到当前"extended" extreme；end 只是当前位置
        net_move_signed = (
            extreme_price_qfq - start_swing_price.analysis
            if direction == +1
            else start_swing_price.analysis - extreme_price_qfq
        )

        return self._mk_leg_partial(
            direction=direction,
            start_index=start_index,
            start_date=start_date,
            start_price=start_swing_price,
            extreme_index=extreme_bar_index,
            extreme_date=extreme_bar_date,
            extreme_price_qfq=extreme_price_qfq,
            end_index=end_index,
            end_date=end_date,
            end_price_qfq=end_price_qfq,
            bars=bars,
        )

    def _mk_leg(
        self,
        *,
        direction: int,
        is_confirmed: bool,
        is_current: bool,
        start_swing: SwingPoint,
        end_swing: SwingPoint,
        bars: Sequence[StructureBar],
        start_index_for_duration: int,
        end_index_for_duration: int,
    ) -> LegState:
        dur = self._count_tradeable_bars(bars, start_index_for_duration, end_index_for_duration)
        start_qfq = start_swing.price.analysis
        end_qfq = end_swing.price.analysis
        # signed_net 与 direction 一致（UP 正、DOWN 负）
        signed_net = end_qfq - start_qfq
        net_move_abs = abs(signed_net)
        return_pct = float(signed_net / start_qfq) if start_qfq != 0 else None

        atr_val = self._atr_at_bar(bars, start_index_for_duration)
        atr_distance: Decimal | None = None
        if atr_val is not None and atr_val > 0:
            atr_distance = net_move_abs / atr_val

        return LegState(
            direction=direction,
            is_confirmed=is_confirmed,
            is_current=is_current,
            start_date=start_swing.event_date,
            end_date=end_swing.event_date,
            start_index=start_swing.event_index,
            end_index=end_swing.event_index,
            start_price=start_swing.price,
            end_price=end_swing.price,
            extreme_price=end_swing.price,
            duration_bars=dur,
            net_move=signed_net,
            return_pct=return_pct,
            atr_distance=atr_distance,
        )

    def _mk_leg_partial(
        self,
        *,
        direction: int,
        start_index: int,
        start_date,
        start_price: StructurePrice,
        extreme_index: int,
        extreme_date,
        extreme_price_qfq: Decimal,
        end_index: int,
        end_date,
        end_price_qfq: Decimal,
        bars: Sequence[StructureBar],
    ) -> LegState:
        dur = self._count_tradeable_bars(bars, start_index, end_index)
        # signed_net 一致于 leg 方向；Pullback 取 abs() 即可
        diff = extreme_price_qfq - start_price.analysis
        signed_net = diff if direction == +1 else -diff
        net_move_abs = abs(signed_net)
        return_pct = float(signed_net / start_price.analysis) if start_price.analysis != 0 else None

        atr_val = self._atr_at_bar(bars, start_index)
        atr_distance: Decimal | None = None
        if atr_val is not None and atr_val > 0:
            atr_distance = net_move_abs / atr_val

        end_price_struct = StructurePrice(
            raw=None,  # current leg 的 end 没有独立 raw 价，只有 qfq close
            analysis=end_price_qfq,
            domain=PriceDomain.QFQ,
            anchor_date=start_price.anchor_date,
        )

        extreme_price_struct = StructurePrice(
            raw=None,  # developing extreme 没有对应原始 swing 的 raw price
            analysis=extreme_price_qfq,
            domain=PriceDomain.QFQ,
            anchor_date=start_price.anchor_date,
        )

        return LegState(
            direction=direction,
            is_confirmed=False,
            is_current=True,
            start_date=start_date,
            end_date=end_date,
            start_index=start_index,
            end_index=end_index,
            start_price=start_price,
            end_price=end_price_struct,
            extreme_price=extreme_price_struct,
            duration_bars=dur,
            net_move=signed_net,
            return_pct=return_pct,
            atr_distance=atr_distance,
        )

    @staticmethod
    def _count_tradeable_bars(
        bars: Sequence[StructureBar], start: int, end: int
    ) -> int:
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


__all__ = ["LegBuilder"]
