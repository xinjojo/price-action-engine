# INTERFACES.md — 接口定义 V0.1

> 状态：**草案，待审核**。
> 本文只定义接口（Protocol 与数据结构），**不含实现**。
> 约定：所有输出结构为 `frozen=True` 的 dataclass——Snapshot 一旦生成不可修改，这是可复现性的前提。

---

## 0. 通用约定

```python
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol, runtime_checkable

class Direction(Enum):
    LONG = 1
    SHORT = -1

class PriceDomain(Enum):
    RAW = "raw"      # 不复权，唯一真实成交口径
    QFQ = "qfq"      # 前复权，Core 分析域
    HFQ = "hfq"      # 后复权，收益域
```

三条跨接口约束：

1. **任何跨层传递的价格都必须显式声明 `price_domain`。** 忘记换算要变成类型错误，不是一个安静的错误收益曲线。
2. **可替换组件的每个实现都必须有稳定的 `id`**，并记入 Snapshot 的 `provenance`。
3. **接口只声明"做什么"，不声明"怎么做"。** 具体实现细节（参数、窗口）进 `configs/*.yaml`。

---

## 1. `MarketDataProvider`（L0）

完整定义见 [DATA_LAYER.md §1](DATA_LAYER.md)。此处只重复签名以便通读。

```python
@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    schema_version: str

    def get_daily_price(self, symbols: list[str], start: date, end: date) -> Iterator[DailyBar]: ...
    def get_adjust_factor(self, symbols: list[str], start: date, end: date) -> Iterator[AdjustFactor]: ...
    def get_trade_calendar(self, start: date, end: date) -> list[TradeDay]: ...
    def get_stock_list(self, as_of: date) -> list[StockInfo]: ...
    def get_stock_status(self, symbols: list[str], start: date, end: date) -> Iterator[StockStatus]: ...
    def get_board_history(self, symbol: str) -> list[BoardPeriod]: ...
    def get_daily_limit(self, symbols: list[str], start: date, end: date) -> Iterator[DailyLimit]: ...
```

**调用方**：只有 `price_action_engine/data/` 内部与回测驱动器。
**删掉它会损失什么**：上层将直接依赖 Tushare 字段名，换数据源 = 重写所有代码。

---

## 2. `TradeCalendar`（L1）

```python
class TradeCalendar(Protocol):
    def is_trading_day(self, d: date) -> bool: ...
    def next_trading_day(self, d: date) -> date: ...
    def prev_trading_day(self, d: date) -> date: ...
    def index_of(self, d: date) -> int:
        """交易日序号。duration 计算的唯一合法依据。"""
    def trading_days_between(self, start: date, end: date) -> int: ...
```

**为什么单独一个接口**：`trading_day_index` 是 `duration_bars` 的唯一合法来源。停牌日不产生 bar 但它是交易日——用列表索引算 duration 会系统性少算。把它收敛到一个接口，才能强制所有人用对。

**调用方**：`market/china_a/t_plus_one.py`、`structure/leg.py`、`structure/pullback.py`、`state/snapshot.py`。

---

## 3. `PriceBridge`（L0）

```python
class PriceBridge:
    """raw ↔ qfq ↔ hfq 的唯一换算入口。

    禁止任何其他模块自行乘除 adj_factor —— 由 tests/test_layering.py 强制。
    """

    def __init__(self, factor_store: FactorStore, source: str) -> None:
        # source 用于跨源防护：Tushare 的 factor 只能配 Tushare 的 price
        ...

    def to_qfq(self, raw_price: Decimal, symbol: str, d: date) -> Decimal: ...
    def to_raw(self, qfq_price: Decimal, symbol: str, d: date) -> Decimal: ...
    def to_hfq(self, raw_price: Decimal, symbol: str, d: date) -> Decimal: ...

    @property
    def factor_source(self) -> str: ...
```

换算式（qfq 以"最新"为基准）：

```
qfq(t) = raw(t) × adj_factor(t) / adj_factor(latest)
raw(t) = qfq(t) × adj_factor(latest) / adj_factor(t)
hfq(t) = raw(t) × adj_factor(t)
```

**调用方**：Core 全部计算（读 qfq）、Execution（读写 raw）、评估（读 hfq）。
**删掉它会损失什么**：qfq 触发价与 raw 成交价混用——茅台这类 `adj_factor` 上百的标的会差几个数量级，而且**不报错**，只是回测曲线一直很漂亮。

---

## 4. `LimitRuleEngine`（L1）

```python
class LimitRuleEngine(Protocol):
    id: str                      # 规则表版本，记入 provenance

    def resolve(self, input: LimitRuleInput) -> LimitRuleResult: ...
```

输入/输出结构见 [A_SHARE_MARKET_RULES.md §1](A_SHARE_MARKET_RULES.md)。

三级解析（`PROVIDER` → `DERIVED` → `UNKNOWN`），**数据优先，推不出来就承认不知道**。

**调用方**：`data/providers/*`（填充 `DailyLimit`）、`state/snapshot.py`（填 `market_constraint` block）、`execution/`（成交判定）。
**删掉它会损失什么**：涨跌停逻辑会散落到每个策略里，规则一变要改 N 处且改不干净。

---

## 5. `SwingDetector`（L3）

```python
@dataclass(frozen=True)
class SwingPoint:
    date: date
    trading_day_index: int
    price: Decimal
    kind: SwingKind            # HIGH | LOW
    strength: float            # 0–100
    detector_id: str

class SwingDetector(Protocol):
    id: str                                    # e.g. "fractal_k2_v1"
    confirmation_lag: int                      # ★ 事后 N 根 bar 才能确认

    def detect(self, bars: list[BarFeatures], up_to: date) -> list[SwingPoint]:
        """只返回【在 up_to 日之前已经确认】的 swing 点。

        实现必须自行保证：point.date + confirmation_lag <= up_to。
        Snapshot 构造期会断言这一点，违反即抛错。
        """
```

**`confirmation_lag` 是本项目最大的未来函数风险点。** 一个分形高点需要右侧 N 根 bar 都不超过它才能确认。在 T 日能给出的，只能是在 T 日之前**已经确认**的高点，而不是"事后看 T 附近的最高点"。

> 这条做错，回测会漂亮得不像话，然后实盘归零。

**V0.1 只实现 `FractalSwingDetector(k=2)`**：`high[i]` 大于左右各 2 根 bar 的 high ⇒ 确认高点，`confirmation_lag = 2`。理由：最简单、可解释、无参数歧义。

未来可替换：`ATRReversalSwingDetector`、`PercentReversalSwingDetector`、`ZigZagSwingDetector`。**上层不得依赖具体实现。**

---

## 6. `TrendModel`（L4）

```python
@dataclass(frozen=True)
class TrendState:
    bias: float          # −100..+100，主输出（符号=方向，绝对值=强度）
    strength: float      # 0–100，= abs(bias)
    confidence: float    # 0–100，可信度，与强度正交
    direction: int       # −1/0/1，只读派生视图 = sign(bias)
    model_id: str

class TrendModel(Protocol):
    id: str              # e.g. "structure_er_v1"

    def compute(self, bars: list[BarFeatures], up_to: date) -> TrendState: ...
```

**明确禁止写死**成 `EMA20 > EMA60` 这类单一判据。

**V0.1 只实现 `StructureTrendModel`**（净位移 + ATR 归一化 + 路径曲折度作置信度，见 Snapshot 文档 §5）。不同时实现多个版本——多版本会诱导"挑一个回测好看的"，那是过拟合。

未来可替换：`BrooksStyleTrendModel`、`CompositeTrendModel`。

---

## 7. `ContextFilter`（L6）

```python
class ContextFilter(Protocol):
    id: str

    def allow(self, snapshot: MarketStateSnapshot) -> tuple[bool, str]:
        """返回 (是否通过, 原因)。原因用于归因分析，不是可选的。"""
```

**为什么独立于 Setup**（需求 §34）：Brooks 的很多 Setup 需要上下文（Bull Trend Only / Range Bottom Only / No Climax）。把上下文写进 Setup 本体会让 Setup 无法复用，且无法单独统计"上下文到底有没有用"。

V0.1 **不实现**，只定义接口。

---

## 8. `Setup`（L7）

```python
@dataclass(frozen=True)
class SetupSignal:
    setup_name: str
    setup_version: str
    symbol: str
    signal_date: date              # = snapshot.bar_date (T)
    valid_for_date: date           # = snapshot.valid_for_date (T+1)
    direction: Direction
    signal_strength: float         # 0–100
    confidence: float              # 0–100
    reference_price: Decimal       # 信号 bar 的关键价位（RAW 域），供 EntryModel 使用
    metadata: dict                 # 允许自由扩展，但不得放任何 Core 指标

class Setup(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def version(self) -> str: ...

    def required_fields(self) -> set[str]:
        """声明依赖的 Snapshot 字段路径，如 {"bar.body_ratio", "trend.bias"}。

        Snapshot 缺字段 ⇒ 构造期直接报错，不得静默给 NaN。
        """
        # 注：原方案写作 required_features()。改为 required_fields() 是为了
        # 让"缺字段"变成显式错误，而不是一个安静的 NaN。

    def detect(self, snapshot: MarketStateSnapshot) -> SetupSignal | None: ...
```

**硬约束**：

- `detect()` 只接收 `MarketStateSnapshot`。**禁止**接触 `DailyBar`、原始 DataFrame、Provider。
- **禁止**重算 swing / trend / range / pressure / breakout / ATR / 任何 bar feature。
- `metadata` 里不得塞入任何 Core 指标（防止绕开边界）。

**V0.1 不实现任何 Setup。** 只定义接口。

---

## 9. `EntryModel`（L8）

```python
@dataclass(frozen=True)
class OrderSpec:
    direction: Direction
    order_type: OrderType          # STOP（V0.1 只有 stop）
    trigger_price: Decimal         # 必须是 RAW 域
    price_domain: PriceDomain      # 必须显式 = RAW
    valid_bars: int = 1            # 默认只存活一根 bar
    created_date: date             # 信号日 T
    setup_name: str                # 溯源

class EntryModel(Protocol):
    id: str                        # e.g. "next_bar_buy_stop_v1"

    def build_order(self, snapshot: MarketStateSnapshot,
                    signal: SetupSignal) -> OrderSpec | None: ...
```

**Brooks Buy Stop 示例**：`trigger_price = to_raw(signal_bar_high) + tick`

**关键**：`signal_bar_high` 在 qfq 域算出后**必须经 `PriceBridge` 转 RAW**，`price_domain` 显式标 `RAW`。这是最容易出错、也最安静的一处。

**禁止**认定"第二天一定成交"。成交是 Execution 的事。

V0.1 不实现。未来：`NextOpen`、`BuyStopAboveSignalBar`、`SellStopBelowSignalBar`、`PullbackEntry`、`CloseEntry`。

---

## 10. `StopModel`（L8）

```python
@dataclass(frozen=True)
class StopSpec:
    stop_price: Decimal            # RAW 域
    price_domain: PriceDomain      # 必须 = RAW
    stop_type: str                 # "signal_bar" | "swing" | "atr" | "trailing" | ...
    trailing: bool = False

class StopModel(Protocol):
    id: str

    def initial_stop(self, snapshot: MarketStateSnapshot,
                     fill: FillResult) -> StopSpec: ...

    def update(self, snapshot: MarketStateSnapshot,
               position: Position) -> StopSpec | None:
        """移动止损。返回 None = 不变。"""
```

V0.1 不实现。未来：`SignalBarStop`、`SwingStop`、`ATRStop`。

**止损的成交判定走与入场相同的 `resolve_fill`**（order_type=STOP, direction=SELL for long）。跳空低开按 `open` 成交，**不得假设按止损价成交**。

---

## 11. `ExitModel`（L8）

```python
class ExitModel(Protocol):
    id: str

    def should_exit(self, snapshot: MarketStateSnapshot,
                    position: Position) -> ExitIntent | None: ...

    def build_exit_order(self, snapshot: MarketStateSnapshot,
                         position: Position) -> OrderSpec | None: ...
```

V0.1 不实现。未来：`FixedRExit`、`TrailingExit`、`OppositeSignalExit`、`TimeExit`。

**必须区分"想退出"和"退得出去"**：`should_exit` 只是意图，实际能否退出由 Execution 判定（可能遇一字跌停卖不掉）。

---

## 12. `ExecutionEngine`（L9）

```python
@dataclass(frozen=True)
class FillResult:
    order: OrderSpec
    fill_date: date | None
    triggered: bool
    filled: bool
    fill_price: Decimal | None
    reason: FillReason             # 见 A_SHARE_MARKET_RULES §7.4
    expired: bool                  # 超过 valid_bars 未成交

class ExecutionEngine(Protocol):
    def resolve_fill(self, order: OrderSpec, bar: DailyBar,
                     limit: DailyLimit) -> FillResult: ...

    def can_sell(self, entry_date: date, current_date: date) -> bool:
        """T+1 约束。内部调 TradeCalendar.earliest_sell_date。"""

    def expire(self, order: OrderSpec) -> None:
        """valid_bars 用尽后订单失效，不得自动顺延到下一根 bar。"""
```

**三条硬约束**：

1. `resolve_fill` 的输入 bar 必须是 **raw 域**。
2. T+1 断言失败 ⇒ **抛错**，不静默忽略。
3. 订单过期即失效。T+2 是否交易必须重新读新的 Snapshot 判断（需求 §29）。

**明确不做**：Level-2、封单排队、订单队列、成交概率模型。一字板一律按不成交处理。

---

## 13. 可替换性契约

任何标记为"可替换"的组件，替换时必须满足：

1. **相同输入 + 相同配置 ⇒ 相同输出格式**（数值可以不同）。
2. **提供稳定的 `id`**，记入 `provenance`。
3. **不得改变 Snapshot 的字段结构**（只能改变数值）。改结构 = 升 `schema_version`。
4. **V0.1 阶段不同时实现多个版本。** 多版本会诱导"挑一个回测最好看的"——那不是研究，是过拟合。

---

## 14. 版本管理

```
snapshot_schema_version   Snapshot 结构版本；字段增删改时 +1
feature_version           L2 特征计算版本
config_hash               configs/*.yaml 内容哈希
swing_detector_id         含 confirmation_lag
trend_model_id            —
```

**回测可复现的判定**：同输入 + 同 `config_hash` + 上述版本全部一致 ⇒ Snapshot 字节级相同。

---

## 15. 接口依赖关系图

```
MarketDataProvider ──┐
                     ├──► PriceBridge ──► (所有价格换算)
TradeCalendar ───────┤
                     │
LimitRuleEngine ─────┼──► MarketStateSnapshot ──► ContextFilter ──► Setup
                     │            ▲                                   │
SwingDetector ───────┤            │                                   ▼
TrendModel ──────────┘            │                          EntryModel ──► OrderSpec
                                  │                                            │
                                  └────────────────────────────────────────────┼──► ExecutionEngine
                                                                               │
                                                              StopModel ────────┤
                                                              ExitModel ────────┘
```

**没有任何箭头指向左上角的 Core 组件。** 依赖单向，这就是架构约束的图形表达。

---

## 16. 需您裁决的问题

| # | 问题 | 我的倾向 |
|---|---|---|
| I1 | `Setup.required_features()` 改名为 `required_fields()`，是否接受？ | 接受。"缺字段报错"优于"静默 NaN" |
| I2 | V0.1 的 `SwingDetector` 只实现 `FractalSwingDetector(k=2)`，是否接受？ | 接受。最简单可解释；多实现会诱发挑参数 |
| I3 | `TrendModel` V0.1 只实现结构版，是否接受？ | 同上 |
| I4 | `OrderSpec.price_domain` 强制显式声明，是否接受？ | 接受。这是把最隐蔽的 bug 变成类型错误的唯一办法 |
| I5 | `StopModel` / `ExitModel` 也走 `resolve_fill`（跳空按 open 成交），是否接受？ | 接受。止损精度虚高是回测失真的第二大来源，仅次于未来函数 |
