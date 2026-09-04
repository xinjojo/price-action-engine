# ARCHITECTURE.md — Price Action Engine V0.1

> 状态：**草案，待审核**。本文只定义架构，不含实现。
> 与本文配套的裁决清单见 [DESIGN_CONFLICTS.md](DESIGN_CONFLICTS.md)。

---

## 0. 一句话定位

输入 A 股日 K 的 OHLCV + 市场制度数据，输出**可复现、无未来函数、带质量标记**的 `MarketStateSnapshot`。

V0.1 **不产出交易信号，不产出策略，不做回测**。Setup 层在 V0.1 只定义接口，不实现内容。

---

## 1. 依赖分层

依赖**只能向下**。下层永远不知道上层的存在。

```
┌──────────────────────────────────────────────────────────────┐
│ L0  Data            数据获取 · 缓存 · 校验 · 统一 Schema        │
├──────────────────────────────────────────────────────────────┤
│ L1  Market Rules    A 股制度：日历 · 涨跌停 · T+1 · ST · 停牌    │
├──────────────────────────────────────────────────────────────┤
│ L2  Features        原子特征：K 线几何 · ATR · Overlap · Gap     │
├──────────────────────────────────────────────────────────────┤
│ L3  Structure       结构：Swing · Leg · Pullback · Range        │
├──────────────────────────────────────────────────────────────┤
│ L4  Market State    Trend · Pressure · Breakout · Climax        │
├──────────────────────────────────────────────────────────────┤
│ L5  Snapshot        ★ 唯一出口：MarketStateSnapshot             │
└──────────────────────────────────────────────────────────────┘
                              ↓  （边界：以下是"利用"，不是"描述"）
┌──────────────────────────────────────────────────────────────┐
│ L6  Filters         Context Filter（如 Bull Trend Only）        │
│ L7  Setups          交易概念插件（Brooks H2 / ICT / 自定义）     │
│ L8  Entry / Stop / Exit  各自独立，自由组合                      │
│ L9  Execution       T+1 · Trigger/Fill 分离 · 可交易性约束       │
│ L10 Portfolio / Backtest / Evaluation                          │
└──────────────────────────────────────────────────────────────┘
```

**L0–L5 = Core。** 只回答一个问题：**市场现在是什么状态？**
**L6–L10 = 应用层。** 回答：**这个状态下有没有可交易机会，能不能成交。**

### 1.1 逐层职责与禁止项

| 层 | 职责 | 输入 | 输出 | 禁止 |
|---|---|---|---|---|
| L0 Data | 取数、缓存、校验、换算成内部 Schema | 外部 API | `DailyBar` / `AdjustFactor` / `TradeCalendar` / `StockStatus` / `DailyLimit` | 知道任何交易概念；自动修正数据 |
| L1 Market Rules | 判定某日某股的交易制度约束 | symbol + date + board + status + prev_close | `LimitRuleResult` / `TradabilityStatus` / `earliest_sell_date` | 写死 10%；猜规则 |
| L2 Features | 计算单 bar 与 bar 间的**客观**量 | `DailyBar` 序列（qfq 域） | `BarFeatures` | 输出买卖判断；解释一字板语义（那是 L4 的事） |
| L3 Structure | 识别 swing / leg / pullback / range | `BarFeatures` + qfq OHLC | `StructureState` | 知道任何 Setup 名称 |
| L4 Market State | 合成趋势、压力、突破、高潮 | `StructureState` + `BarFeatures` | 各 state 组件 | 出现 Level C 词汇 |
| L5 Snapshot | 组装、冻结、打版本号与质量标记 | 以上全部 | `MarketStateSnapshot`（不可变） | 任何可变状态 |
| L6 Filters | 上下文过滤 | Snapshot | bool + reason | 修改 Snapshot |
| L7 Setups | 识别交易概念 | Snapshot | `SetupSignal \| None` | 重算 Core 指标；读 L0 原始数据 |
| L8 Entry/Stop/Exit | 产出价格计划 | Snapshot + Signal + Position | `OrderSpec` / `StopSpec` | 认定"一定成交" |
| L9 Execution | 判定能否成交、何时成交、以何价 | `OrderSpec` + **raw** bar + limit | `FillResult` | 假设排队位置；当日买当日卖 |
| L10 Portfolio/BT | 记账、绩效 | `FillResult` | 报告 | 反向影响 Snapshot |

---

## 2. 四条边界

### 2.1 Core / Setup 边界（`MarketStateSnapshot`）

Snapshot 是**唯一的**穿越边界的物体。

- Setup **只能**读 Snapshot。禁止接触 `DailyBar`、原始 DataFrame、provider。
- Snapshot **不可变**（frozen dataclass），生成后任何人都改不了。
- Snapshot 自带 `provenance`（算法版本 + 配置哈希），保证回测可复现。

**为什么这样切**：如果 Setup 能碰到原始 K 线，每个 Setup 都会长出自己的一套 swing/ATR 实现，指标口径会悄悄漂移，最终无法判断"到底是 Setup 无效还是指标实现不一致"。单一出口把这个风险一次性关掉。

**Level C 词汇污染防护**：Core 代码中出现 `H1 / H2 / L1 / L2 / FVG / MSS / OTE / ICT / Wedge / MTR / buy / sell / signal` 等词即测试失败。

### 2.2 Market Rules 边界

交易制度是**一级模块**，不是策略的辅助函数。

- 涨跌停幅度、T+1、停牌、ST、新股、退市整理——全部在 L1。
- L7 Setup 完全不知道涨跌停的存在（它只看 Snapshot 里的 `tradability_score`）。
- L9 Execution 读 L1 的判定结果，但**不重新推导规则**。

**为什么这样切**：A 股制度按日期变化（见 [A_SHARE_MARKET_RULES.md](A_SHARE_MARKET_RULES.md) 的规则版本表，ST 涨跌幅正在从 5% 走向 10%）。制度一旦散落在策略里，规则一变就要改 N 处，而且改不干净。

### 2.3 Execution 边界（Trigger / Fill 分离）

三个问题必须分三次回答，由三个不同的模块回答：

| 问题 | 谁回答 | 输出 |
|---|---|---|
| 有没有信号？ | Setup | `SetupSignal` |
| 触发价是多少？ | EntryModel | `OrderSpec`（一个价格 + 一个有效期） |
| 实际成交了吗？以什么价？ | ExecutionEngine | `FillResult` |

Setup **不得**认定"第二天一定成交"。Execution **不得**修改触发价。

**订单默认只存活 1 根 bar。** T 日产生信号 → T+1 未成交 → 订单失效；T+2 必须重新读 Snapshot 判断。这保留了 Brooks 的"逐 bar 重新判断"，也避免了一字涨停后订单悬挂造成的假成交。

### 2.4 Data 边界

上层禁止出现 `tushare.*`、`mootdx.*`、任何第三方数据函数名。一律经 `MarketDataProvider`。

---

## 3. 时间与因果模型（防未来函数的结构保证）

这是本项目最重要的一处架构设计。**未来函数不是靠"小心"避免的，是靠类型结构消灭的。**

每个 Snapshot 携带三个时间字段：

| 字段 | 含义 |
|---|---|
| `bar_date` | 数据日 **T** |
| `as_of` | 观察时点，**固定为 T 日收盘** |
| `valid_for_date` | 该快照**允许驱动**的执行日，恒等于 **T+1 交易日** |

配套规则：

1. Snapshot 构造时**强制**校验：所有输入数据的 `trade_date <= bar_date`。构造期就抛错，不等到回测崩。
2. `valid_for_date` 由交易日历计算，不是 `bar_date + 1 天`（要跳过周末、节假日、临时休市）。
3. 任何滚动窗口统计（ATR、分位数、均值、极值）**只能用 `<= bar_date` 的数据**。全样本归一化是未来函数——它让 2010 年的分数依赖 2026 年的数据分布。
4. Setup 与 Execution 只能拿到 `valid_for_date == 当前执行日` 的 Snapshot。执行引擎在入口处断言这一点。

**为什么值得这么较真**：在日 K 研究里，最容易骗人的回测结果几乎都来自"收盘前已经知道信号"。把因果写进类型，它就没法悄悄溜进来。

---

## 4. 复权三域与 `PriceBridge`

A 股跨除权日做价格分析，复权选错等于全盘皆错。本项目显式区分三个域：

| 域 | 用在哪 | 理由 |
|---|---|---|
| **raw**（不复权） | 成交判定、PnL、止损价、涨跌停判定 | 唯一对应真实成交的口径。交易所按 raw 撮合 |
| **qfq**（前复权） | 结构识别、形态、所有 Core 指标、Gap 的 Price Action 语义 | 消除除权造成的价格断层，图形连续可比 |
| **hfq**（后复权） | 收益率、复权净值 | 收益连续 |

### 4.1 Gap 必须双算（关键）

- 除权除息日在 **raw** 上表现为向下跳空，在 **qfq** 上不表现。→ 用 raw 算 Gap 会把每一次分红送股都误判成"看跌跳空"。
- 真实隔夜跳空在 raw 和 qfq 上**都**表现。

因此：

```
gap_qfq  = 用于 Price Action 语义（主）
gap_raw  = 事实跳空（含除权）
is_ex_dividend_date = adj_factor[t] != adj_factor[t-1]
```

Core 用 `gap_qfq`；`is_ex_dividend_date` 仅作为质量标记与过滤条件，**绝不用它去"修正"任何价格**。

### 4.2 `PriceBridge`：唯一的换算入口

```
raw(t) = qfq(t) × adj_factor(latest) / adj_factor(t)
```

**为什么必须有一个专用模块**：

Setup 在 **qfq 域**算出"信号 bar 高点 + 1 tick = 12.34"，但这个 12.34 是复权价。Execution 拿它与 T+1 的 **raw** K 线比较——不换算就会与真实价格差出一个复权倍数（茅台这种 adj_factor 上百的标的会差出几个数量级）。

这是回测里最典型、也最安静的一类错误：不报错，只是收益曲线一直很漂亮。

约束：**任何地方都不得自行乘除 `adj_factor`，必须调 `PriceBridge`。** 该约束由代码扫描强制（见 §5）。

---

## 5. 分层强制机制

架构不靠文档自觉，靠两个可运行的检查。

| 检查 | 文件 | 做什么 |
|---|---|---|
| 依赖方向 | `tests/test_layering.py` | AST 扫描全部 import，断言不存在向上的跨层引用（如 `state/` 引用 `setups/`，`structure/` 引用 `state/`） |
| 词汇与纪律 | `tests/test_layering.py` | ① Core 目录内扫描 Level C 禁用词；② 全仓库（除 `PriceBridge`）扫描裸的 `adj_factor` 乘除表达式；③ 扫描 `tushare.` / `mootdx.` 在非 `providers/` 目录的出现 |

只有两个小文件，不引入测试框架的 fixture 体系。违反即在 CI / 本地失败。

---

## 6. 第一阶段目录树

原则：**只为已经确认的调用方建文件。** 以下是 V0.1 真正需要创建的；凡是没有实际调用方的目录一律不建（`setups/`、`entries/`、`stops/`、`exits/`、`execution/`、`portfolio/`、`backtest/`、`evaluation/`、`filters/` 在 V0.1 **一个都不建**）。

```
price-action-engine/
├── AGENTS.md                      # ponytail 规则集 + 项目硬约束
├── README.md
├── .gitignore                     # 含 .env，脱敏关键
├── .env.example                   # 只有变量名，无真实值
│
├── docs/                          # 本目录：设计文档（V0.1 主要内容）
│   ├── ARCHITECTURE.md
│   ├── DATA_LAYER.md
│   ├── MARKET_STATE_SNAPSHOT.md
│   ├── A_SHARE_MARKET_RULES.md
│   ├── INTERFACES.md
│   └── DESIGN_CONFLICTS.md
│
├── configs/                       # 声明式参数，不进代码
│   ├── features.yaml              # ATR 周期、cap_atr、gap 阈值等
│   └── limit_rules.yaml           # 涨跌停规则版本表（V0.2，随 L1 落地）
│
└── price_action_engine/
    ├── data/
    │   ├── schema.py              # DailyBar / AdjustFactor / StockStatus / DailyLimit
    │   ├── providers/
    │   │   ├── base.py            # MarketDataProvider Protocol
    │   │   └── tushare_provider.py
    │   ├── validators.py          # L0–L4 分级校验
    │   ├── cache.py               # 本地 parquet 缓存 + manifest
    │   └── bridge.py              # PriceBridge：raw ↔ qfq ↔ hfq 唯一换算口
    │
    ├── market/
    │   └── china_a/
    │       ├── calendar.py        # 交易日历 → trading_day_index
    │       ├── limit_rules.py     # LimitRuleEngine
    │       ├── status.py          # ST / 停牌 / 退市整理 / 新股 → TradabilityStatus
    │       └── t_plus_one.py      # earliest_sell_date（规则定义；执行在 L9）
    │
    ├── features/
    │   ├── bar.py                 # body_ratio / wick / close_location / range_atr_ratio
    │   ├── volatility.py          # TR / ATR(Wilder)
    │   ├── overlap.py
    │   └── gap.py                 # gap_qfq / gap_raw / is_ex_dividend_date
    │
    ├── structure/
    │   ├── swing.py               # SwingDetector Protocol + FractalSwingDetector
    │   ├── leg.py
    │   ├── pullback.py
    │   └── range.py
    │
    ├── state/
    │   ├── trend.py               # TrendModel Protocol + StructureTrendModel
    │   ├── pressure.py
    │   ├── breakout.py            # breakout + follow_through + failure（三者同源，合并）
    │   ├── climax.py
    │   └── snapshot.py            # MarketStateSnapshot + Builder + 因果校验
    │
    └── tests/
        ├── test_layering.py       # §5 的两个强制检查
        └── test_bar_features.py   # 原子特征自检（含一字板 None 语义）
```

**共 21 个 Python 文件。** 相比原始提案（20+ 顶层目录、含 `adjustments/`、`adapters/`、`metadata/`、`t_plus_one.py` 等）做了以下删减，理由如下：

| 删减 | 理由 |
|---|---|
| `adapters/` | 与 `providers/` 职责重叠。每个 Provider 自己负责转成内部 Schema，无需独立适配层 |
| `adjustments/` | 复权逻辑全部收敛到 `bridge.py` + `schema.py`，单文件足够 |
| `metadata/` | 股票列表/板块/状态即 `StockStatus`，并入 `schema.py` |
| `state/failure.py` | breakout 的 follow_through 与 failure 是同一次计算的产物，拆开会产生两套突破定义 |
| `market/rules.py` | 与 `status.py` 重叠，合并 |
| `state/snapshot.py` 之外的 `state/` 拆分 | 保留，因为 trend/pressure/breakout/climax 各自独立可替换 |
| 所有 L6–L10 目录 | V0.1 无调用方 |

---

## 7. V0.1 明确不做

写下来是为了防止它们悄悄混进来。

- ❌ 任何完整策略（H2 / ICT 2022 / Wedge / MTR）
- ❌ 回测、组合、绩效评估
- ❌ Entry / Stop / Exit / Execution 的实现（只定义接口）
- ❌ 分钟级 / Tick / Level-2 / 实盘
- ❌ 涨停板排队、封单位置、成交概率模型
- ❌ 机器学习、参数优化、多因子选股
- ❌ Redis / 数据库集群（缓存用本地 parquet）
- ❌ 自动修正数据（只告警）

---

## 8. 判断标准（验收 V0.1 是否合格）

1. **可解释** —— 每个 Snapshot 字段都能追溯到一句"市场在发生什么"。
2. **可替换** —— SwingDetector、TrendModel 换实现，上层零改动。
3. **可回测** —— 同输入 + 同配置 + 同版本 ⇒ 字节级相同的 Snapshot。
4. **无未来函数** —— 由 §3 的因果结构 + `test_layering.py` 保证。
5. **尊重 A 股约束** —— 一字板、涨跌停、T+1、停牌都有明确且保守的处理。
6. **贴近 Brooks** —— 变量是连续的市场测量，不是形态名。
7. **不被 Setup 绑死** —— Core 里找不到任何一个 Setup 的名字。
8. **可逐步验证** —— 每个 Level B 变量都能单独拎出来做预测力检验。

---

## 9. 与原始需求文档的差异（需您裁决）

| # | 原方案 | 本草案 | 理由 |
|---|---|---|---|
| A1 | 未提因果字段 | 新增 `as_of` / `valid_for_date` | 未来函数靠结构消灭，不靠自觉 |
| A2 | 未提复权域 | 新增三域 + `PriceBridge` | qfq 信号价与 raw 成交价混用是回测最隐蔽的错误源 |
| A3 | `t_plus_one_status` 在 Snapshot 里 | 移出 Snapshot | T+1 是**持仓属性**不是市场属性；同一天同一个股，能否卖出取决于你的入场日 |
| A4 | `range_probability` | 改为可观测频率，不叫 probability | 未校准的"概率"会误导下游 |
| A5 | `exhaustion_score` / `compression_score` | 推迟到 V2 | 缺客观定义，容易退化成主观拟合（详见 DESIGN_CONFLICTS） |
| A6 | Snapshot 无质量/版本字段 | 新增 `quality` / `provenance` | 否则脏数据静默产信号，且回测不可复现 |
| A7 | `get_board_info()` 返回当前板块 | 改为返回**板块时间段序列** | 涨跌停规则按日期版本化，单点板块信息不够用 |
