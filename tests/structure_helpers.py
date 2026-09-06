"""Structure 测试共享 helper。

提供：
- ``make_bar(date, o, h, l, c, *, atr=None, suspended=False, ...)``
- ``make_leg_sequence([...])``
- ``run_case(bars, threshold_atr=1.0)`` 返回 StructureState

绝大多数测试只需 ``make_bar`` 即可。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Sequence

from price_action_engine.structure import (
    ATRReversalSwingDetector,
    StructureAnchor,
    StructureEngine,
    StructureBar,
    SwingKind,
)


def make_bar(
    day_index: int,
    *,
    o: Decimal | float | str,
    h: Decimal | float | str,
    l: Decimal | float | str,
    c: Decimal | float | str,
    atr: Decimal | float | str | None = None,
    suspended: bool = False,
    one_word_up: bool = False,
    one_word_down: bool = False,
    start_date: date | None = None,
) -> StructureBar:
    """构造一个 StructureBar；O=H=L=C 时若 ``one_word_*`` 也为 True，自动映射到对应 flag。"""
    if start_date is None:
        start_date = date(2025, 1, 2)  # V0.1 fixture 默认起点
    bar_date = start_date + timedelta(days=day_index)

    o_d = _d(o)
    h_d = _d(h)
    l_d = _d(l)
    c_d = _d(c)
    atr_d = _d(atr) if atr is not None else None

    # 如果 O=H=L=C + one_word 标记补上；如果未指定但 O=H=L=C，自动不标记（让测试显式控制）
    return StructureBar(
        bar_date=bar_date,
        bar_index=day_index,
        open_qfq=o_d,
        high_qfq=h_d,
        low_qfq=l_d,
        close_qfq=c_d,
        raw_open=o_d,
        raw_high=h_d,
        raw_low=l_d,
        raw_close=c_d,
        atr_qfq=atr_d,
        is_suspended=suspended,
        is_one_word_limit_up=one_word_up,
        is_one_word_limit_down=one_word_down,
    )


def _d(v: Decimal | float | str | None) -> Decimal:
    if v is None:
        raise ValueError("price can't be None for normal bars")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def bars_with_constant_atr(
    bars: list[StructureBar], atr_value: Decimal | float | str
) -> list[StructureBar]:
    """为整个列表强制设置同一个 atr_qfq，方便单元测试隔离 Swing 算法本身。"""
    atr_d = _d(atr_value)
    out: list[StructureBar] = []
    for b in bars:
        out.append(
            StructureBar(
                bar_date=b.bar_date,
                bar_index=b.bar_index,
                open_qfq=b.open_qfq,
                high_qfq=b.high_qfq,
                low_qfq=b.low_qfq,
                close_qfq=b.close_qfq,
                raw_open=b.raw_open,
                raw_high=b.raw_high,
                raw_low=b.raw_low,
                raw_close=b.raw_close,
                atr_qfq=atr_d,
                is_suspended=b.is_suspended,
                is_one_word_limit_up=b.is_one_word_limit_up,
                is_one_word_limit_down=b.is_one_word_limit_down,
            )
        )
    return out


def make_engine(
    *,
    threshold_atr: Decimal | float | str = "1.0",
    atr_period: int = 14,
) -> StructureEngine:
    return StructureEngine(
        swing_detector=ATRReversalSwingDetector(
            reversal_threshold_atr=_d(threshold_atr),
            atr_period=atr_period,
        )
    )


def run_case(
    bars: Sequence[StructureBar],
    *,
    threshold_atr: Decimal | float | str = "1.0",
) -> StructureState:
    """以窗口最后一天为 as_of，跑一次完整 StructureEngine。"""
    eng = make_engine(threshold_atr=threshold_atr)
    last_date = bars[-1].bar_date
    return eng.compute_state(bars=bars, as_of_date=last_date)


def dump_state(state) -> str:
    """纯文本格式化输出，便于 A-H 案例打印。"""
    lines: list[str] = []
    lines.append(f"as_of_date={state.as_of_date}")
    lines.append(f"anchor={state.anchor.label()}")
    lines.append(f"quality_flags={','.join(f.value for f in state.quality_flags)}")
    lines.append("confirmed_swings:")
    for s in state.confirmed_swings:
        lines.append(
            f"  - {s.kind.value.upper():5s} "
            f"event={s.event_date}@{s.event_index} "
            f"confirmed={s.confirmed_date}@{s.confirmed_index} "
            f"qfq={s.price.analysis} "
            f"raw={s.price.raw}"
        )
    if state.previous_confirmed_leg:
        pl = state.previous_confirmed_leg
        lines.append(
            f"previous_leg: dir={'UP' if pl.direction>0 else 'DOWN'} "
            f"{pl.start_date}->{pl.end_date} "
            f"net_move={pl.net_move} ret={_pct(pl.return_pct)} "
            f"atr={pl.atr_distance} dur={pl.duration_bars}"
        )
    if state.current_leg:
        cl = state.current_leg
        lines.append(
            f"current_leg: dir={'UP' if cl.direction>0 else 'DOWN'} "
            f"{cl.start_date}->{cl.end_date} "
            f"extreme={cl.extreme_price.analysis if cl.extreme_price else None} "
            f"net_move={cl.net_move} ret={_pct(cl.return_pct)} "
            f"atr={cl.atr_distance} dur={cl.duration_bars} confirmed={cl.is_confirmed}"
        )
    if state.current_pullback:
        pb = state.current_pullback
        lines.append(
            f"pullback: dir={'UP' if pb.direction>0 else 'DOWN'} "
            f"start={pb.start_date} dur={pb.duration_bars} "
            f"extreme={pb.extreme_price.analysis if pb.extreme_price else None} "
            f"depth_abs={pb.depth_abs} "
            f"depth_pct_prior={_pct(pb.depth_pct_of_prior_leg)} "
            f"depth_atr={pb.depth_atr} active={pb.is_active}"
        )
    return "\n".join(lines)


def _pct(x):
    if x is None:
        return "None"
    return f"{x*100:.2f}%"
