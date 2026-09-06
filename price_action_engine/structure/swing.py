"""ATR Reversal Swing Detector（V0.2 唯一实现）。

为什么 V0.2 选这个（不选 N-bar Fractal / ZigZag）：

1. 因果性最强 —— 只有当价格从 extreme 反向移动 ≥ ``reversal_threshold_atr * ATR``
   时才确认 swing。confirm 必须在反转事件发生的同一根 bar 上发生，所以
   ``confirmed_date`` 等于触发确认的 bar，没有未来引用。

2. 参数少且可配置 —— ``reversal_threshold_atr`` + ``atr_period`` 两个；
   ATR 周期由上游 features.volatility.wilder_atr 计算，本模块不做任何 ATR 估计。

3. 与 Brooks leg/pullback 语义吻合 —— Brooks 的 leg 在"足够大幅度反转"时确认，
   这正是 Directional Change 的语义。

4. A 股日 K 适配 —— 一字涨停（O=H=L=C）下，价格水平仍上推，extreme 仍更新；
   仅 ATR 因 TR=0 被拉低（被标记 LIMIT_CENSORED 但不动数值）。

5. 实现复杂度可接受 —— 一个常量状态机 + candidate_swing 暂存。

因果边界（§九）：

- 在 bar T 进入 step() 时，bar.atr_qfq 应该是「T close 时已确定」的 ATR。
- decision-on-T 仅依赖 bar T 自身 + 此前所有 bar，不参考 T+1。
- 这是 ConfigurationContract：不在 Swing 算法内部算 ATR；ATR 由调用方提供。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from price_action_engine.structure.types import (
    PriceDomain,
    QualityFlag,
    StructureBar,
    StructurePrice,
    SwingKind,
    SwingPoint,
)


class ATRReversalSwingDetector:
    """Directional-Change Swing detector。

    Parameters
    ----------
    reversal_threshold_atr : Decimal
        反转倍数。意为「从 extreme 反向移动 ≥ 这么多倍 ATR 才确认 swing」。
        V0.2 默认 1.0，配置来自 configs/engine.toml，不散落到代码里。
    detector_id : str
        本探测器的稳定标识，写进 SwingPoint.detector_id，可用于比较不同 detector 输出。
    """

    DETECTOR_KIND = "atr_reversal"

    def __init__(
        self,
        *,
        reversal_threshold_atr: Decimal,
        atr_period: int = 14,
        detector_id: str = "atr_reversal_v1",
    ) -> None:
        if reversal_threshold_atr <= 0:
            raise ValueError("reversal_threshold_atr must be > 0")
        if atr_period <= 0:
            raise ValueError("atr_period must be > 0")
        self._threshold = reversal_threshold_atr
        self._period = atr_period
        self._detector_id = detector_id

    @property
    def detector_id(self) -> str:
        return self._detector_id

    @property
    def reversal_threshold_atr(self) -> Decimal:
        return self._threshold

    @property
    def atr_period(self) -> int:
        return self._period

    def detector_params(self) -> dict[str, object]:
        return {
            "detector_kind": self.DETECTOR_KIND,
            "reversal_threshold_atr": self._threshold,
            "atr_period": self._period,
        }

    # ────────────────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────────────────

    def detect(self, bars: Sequence[StructureBar]) -> list[SwingPoint]:
        """Causal+immutable：T 日的输出只依赖 bars[:T+1]。后文 §因果守卫 有专项测试。

        跳过停牌日（§二十）；一字涨停照常推进（§十）。
        ATR 预热不足（前 atr_period 根 bar）返回空 + INSUFFICIENT_ATR flag。
        """
        confirmed: list[SwingPoint] = []
        flags: list[QualityFlag] = []

        # ── 状态机字段（显式定义，避免复用类变量做 cache）──
        direction: int = 0               # +1 = UP, -1 = DOWN, 0 = undecided
        extreme_price: Decimal | None = None
        extreme_bar: StructureBar | None = None
        candidate: SwingPoint | None = None  # 当前正在追踪方向的 future swing

        # 先扫一遍累计有效 bars 数。如果有效 bars 数 < atr_period + 1，理论上
        # Directional Change 也无足够数据，但这并不阻止产生 swing；只是极端的
        # threshold 会极小。V0.2 仍然输出，只在 result 上加 INSUFFICIENT_ATR flag。
        tradeable_count = sum(0 if b.is_suspended else 1 for b in bars)

        for bar in bars:
            if bar.is_suspended:
                flags.append(QualityFlag.SUSPENDED_BARS_SKIPPED)
                continue

            atr = bar.atr_qfq
            if atr is None or atr <= 0:
                flags.append(QualityFlag.INSUFFICIENT_ATR)
                continue

            threshold = self._threshold * atr

            if direction == 0:
                # 起步：还没方向，把第一根 bar 作为 initial seed
                direction = +1
                extreme_price = bar.high_qfq
                extreme_bar = bar
                candidate = self._mk_candidate(
                    kind=SwingKind.HIGH,
                    bar=bar,
                    raw=bar.raw_high,
                    analysis=bar.high_qfq,
                )
                continue

            # ── UP 方向：跟踪 extreme，看是否反转 ──
            if direction == +1:
                # 1) 先尝试 high 更新 extreme；candidate 跟随极端 → 真正的 swing HIGH 事件点
                high_pushed = False
                if bar.high_qfq > extreme_price:
                    extreme_price = bar.high_qfq
                    extreme_bar = bar
                    candidate = self._mk_candidate(
                        kind=SwingKind.HIGH,
                        bar=bar,
                        raw=bar.raw_high,
                        analysis=bar.high_qfq,
                    )
                    high_pushed = True

                # 2) 用 close（确定性的、bar 收盘时已知的最终价）判断反转
                if bar.close_qfq <= extreme_price - threshold:
                    # confirm prior swing HIGH
                    swing = self._finalize_candidate_high(
                        candidate=candidate,
                        confirm_bar=bar,
                    )
                    confirmed.append(swing)

                    # 进入 DOWN 方向；新 extreme 取 bar.low（已知）
                    new_extreme = bar.low_qfq
                    new_extreme_bar = bar
                    direction = -1
                    extreme_price = new_extreme
                    extreme_bar = new_extreme_bar
                    candidate = self._mk_candidate(
                        kind=SwingKind.LOW,
                        bar=bar,
                        raw=bar.raw_low,
                        analysis=bar.low_qfq,
                    )

                    # 3) 同根 bar 内既冲上新极端又触发反转 → 标记歧义
                    if high_pushed:
                        flags.append(QualityFlag.STRUCTURE_INTRABAR_AMBIGUOUS)
                    continue

            # ── DOWN 方向 ──
            else:  # direction == -1
                if bar.low_qfq < extreme_price:
                    extreme_price = bar.low_qfq
                    extreme_bar = bar
                    candidate = self._mk_candidate(
                        kind=SwingKind.LOW,
                        bar=bar,
                        raw=bar.raw_low,
                        analysis=bar.low_qfq,
                    )

                if bar.close_qfq >= extreme_price + threshold:
                    swing = self._finalize_candidate_low(
                        candidate=candidate,
                        confirm_bar=bar,
                    )
                    confirmed.append(swing)

                    new_extreme = bar.high_qfq
                    new_extreme_bar = bar
                    direction = +1
                    extreme_price = new_extreme
                    extreme_bar = new_extreme_bar
                    candidate = self._mk_candidate(
                        kind=SwingKind.HIGH,
                        bar=bar,
                        raw=bar.raw_high,
                        analysis=bar.high_qfq,
                    )

                    if bar.low_qfq <= extreme_price - threshold and bar.high_qfq - bar.low_qfq >= threshold:
                        flags.append(QualityFlag.STRUCTURE_INTRABAR_AMBIGUOUS)
                    continue

            # 不构成反转，也不在极端更新路径上 —— candidate 不动
            # 但 candidate 的 event 仍可在下一个反向时 finalize

        # 最后一根 bar 时正在追踪的 candidate 没有 confirm（无法知道后续）
        # 不要 append —— 尚未确认

        # 给 detector 加 QualityFlag 后缀标记 INSUFFICIENT_ATR
        # (调用方可基于此决策是否信任该 detector 的输出)
        self._last_flags = tuple(dict.fromkeys(flags))
        self._last_tradeable_count = tradeable_count
        return confirmed

    # 方便测试 / 报告
    @property
    def last_flags(self) -> tuple[QualityFlag, ...]:
        return getattr(self, "_last_flags", ())

    @property
    def last_tradeable_count(self) -> int:
        return getattr(self, "_last_tradeable_count", 0)

    # ────────────────────────────────────────────────────────────────
    # 内部 helpers
    # ────────────────────────────────────────────────────────────────

    def _mk_candidate(
        self,
        *,
        kind: SwingKind,
        bar: StructureBar,
        raw: Decimal | None,
        analysis: Decimal,
    ) -> SwingPoint:
        return SwingPoint(
            kind=kind,
            event_date=bar.bar_date,
            event_index=bar.bar_index,
            confirmed_date=None,
            confirmed_index=None,
            price=StructurePrice(
                raw=raw if raw is not None else analysis,  # 若 raw 缺失用 qfq 作 fallback
                analysis=analysis,
                domain=PriceDomain.QFQ,
                anchor_date=None,  # 顶层 compute_state 统一注入
            ),
            detector_id=self._detector_id,
            detector_params=self.detector_params(),
        )

    def _finalize_candidate_high(
        self,
        *,
        candidate: SwingPoint,
        confirm_bar: StructureBar,
    ) -> SwingPoint:
        # 用真实 confirmed_date 而不是 candidate 自身的 event_date
        # 用 confirm_bar 的 bar_date 以保证 causal boundary 在 confirm_bar 上
        return SwingPoint(
            kind=SwingKind.HIGH,
            event_date=candidate.event_date,
            event_index=candidate.event_index,
            confirmed_date=confirm_bar.bar_date,
            confirmed_index=confirm_bar.bar_index,
            price=candidate.price,
            detector_id=self._detector_id,
            detector_params=self.detector_params(),
        )

    def _finalize_candidate_low(
        self,
        *,
        candidate: SwingPoint,
        confirm_bar: StructureBar,
    ) -> SwingPoint:
        return SwingPoint(
            kind=SwingKind.LOW,
            event_date=candidate.event_date,
            event_index=candidate.event_index,
            confirmed_date=confirm_bar.bar_date,
            confirmed_index=confirm_bar.bar_index,
            price=candidate.price,
            detector_id=self._detector_id,
            detector_params=self.detector_params(),
        )


__all__ = ["ATRReversalSwingDetector"]
