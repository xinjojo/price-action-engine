"""StructureEngine —— 顶层计算入口。

设计要点（V0.2 指令 §二十一、§二十二）：

- ``compute_state(bars, as_of_date)`` 在单一入口处锁定 anchor（= as_of_date），
  整段窗口的 qfq 比较都在同一坐标系内（§二）。不允许每根 bar 各自 anchor。

- 由 SwingDetector 输出后，本模块注入 ``anchor_date`` 到所有 SwingPoint.price，
  并保证 SwingPoint/Swing 的 event/confirmed/price 三元组不被随意覆盖。

- Leg/Pullback Builder 只读 confirmed_swings + bars，不重新算 Swing。

- 公共 ``compute_state`` 是 V0.2 唯一入口；不做 SnapshotWindow（§二十一已允许延后）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence

from price_action_engine.structure.leg import LegBuilder
from price_action_engine.structure.pullback import PullbackBuilder
from price_action_engine.structure.swing import ATRReversalSwingDetector
from price_action_engine.structure.types import (
    LegState,
    PriceDomain,
    PullbackState,
    QualityFlag,
    StructureAnchor,
    StructureBar,
    StructureState,
    StructurePrice,
    SwingKind,
    SwingPoint,
)


class StructureEngine:
    """V0.2 唯一实现：Swing（ATR Reversal）→ Leg → Pullback。"""

    def __init__(
        self,
        *,
        swing_detector: ATRReversalSwingDetector | None = None,
        leg_builder: LegBuilder | None = None,
        pullback_builder: PullbackBuilder | None = None,
    ) -> None:
        self.swing = swing_detector or ATRReversalSwingDetector(
            reversal_threshold_atr=_DEFAULT_THRESHOLD_ATR,
        )
        self.legs = leg_builder or LegBuilder()
        self.pullbacks = pullback_builder or PullbackBuilder()

    def compute_state(
        self,
        *,
        bars: Sequence[StructureBar],
        as_of_date: date,
        as_of_index: int | None = None,
    ) -> StructureState:
        """参数
        ------
        bars : Sequence[StructureBar]
            已经在统一 anchor 下换算好的结构 bar 序列（含 raw_*、atr_qfq、is_suspended）。
        as_of_date : date
            锚定日期。所有结构比较、qfq 价格都在此 anchor 下。
        as_of_index : int | None
            默认 = len(bars)-1（最后一根）。

        不会修改传入的 bar；返回一个新 StructureState。
        """
        if not bars:
            raise ValueError("bars is empty")
        if as_of_index is None:
            as_of_index = len(bars) - 1
        if as_of_index >= len(bars):
            raise ValueError("as_of_index out of range")
        if as_of_index < 0:
            raise ValueError("as_of_index must be >= 0")

        anchor = StructureAnchor(as_of_date=as_of_date, suffix=f"compute@{as_of_date.isoformat()}")

        # ── 1. Swing detection
        confirmed_raw = self.swing.detect(bars)

        # ── 2. 注入 anchor 到 SwingPoint.price
        confirmed: list[SwingPoint] = []
        for sp in confirmed_raw:
            new_price = StructurePrice(
                raw=sp.price.raw,
                analysis=sp.price.analysis,
                domain=sp.price.domain if sp.price.domain is not PriceDomain.UNSET else PriceDomain.QFQ,
                anchor_date=anchor.as_of_date,
            )
            confirmed.append(
                SwingPoint(
                    kind=sp.kind,
                    event_date=sp.event_date,
                    event_index=sp.event_index,
                    confirmed_date=sp.confirmed_date,
                    confirmed_index=sp.confirmed_index,
                    price=new_price,
                    detector_id=sp.detector_id,
                    detector_params=sp.detector_params,
                )
            )

        last_high = next((s for s in reversed(confirmed) if s.kind == SwingKind.HIGH), None)
        last_low = next((s for s in reversed(confirmed) if s.kind == SwingKind.LOW), None)

        # ── 3. Legs
        previous_leg, current_leg = self.legs.build(
            confirmed_swings=confirmed,
            bars=bars,
            as_of_index=as_of_index,
        )

        # ── 4. Pullback
        pullback = self.pullbacks.build(
            confirmed_swings=confirmed,
            legs=(previous_leg, current_leg),
            bars=bars,
            as_of_index=as_of_index,
        )

        # ── 5. Quality flags
        flags: list[QualityFlag] = []
        suspended_any = any(b.is_suspended for b in bars)
        if suspended_any:
            flags.append(QualityFlag.SUSPENDED_BARS_SKIPPED)
        # 透传 swing detector 自己的 flags
        flags.extend(self.swing.last_flags)

        # 去重保序
        seen: set[QualityFlag] = set()
        unique_flags: list[QualityFlag] = []
        for f in flags:
            if f not in seen:
                seen.add(f)
                unique_flags.append(f)

        return StructureState(
            as_of_date=as_of_date,
            anchor=anchor,
            last_confirmed_swing_high=last_high,
            last_confirmed_swing_low=last_low,
            previous_confirmed_leg=previous_leg,
            current_leg=current_leg,
            current_pullback=pullback,
            confirmed_swings=tuple(confirmed),
            quality_flags=tuple(unique_flags),
        )


_DEFAULT_THRESHOLD_ATR = Decimal("1.0")


__all__ = ["StructureEngine"]
