# A_SHARE_MARKET_RULES.md — A 股制度层 V0.1.1

> 状态：**V0.1.1 收尾。设计收敛完成，已实现最小 Core。**
> 最近一次官方规则变更复核：2026-09-04。**接入生产前必须再复核一次**。

---

## 0. 设计立场

A 股制度不是"策略的边界条件"，它是一等公民。三条铁律：

1. **涨跌停幅度不得硬编码。** 不写 `if pct >= 10%`。
2. **规则按日期版本化。** 同一只股票 2019 年和 2026 年的涨停幅度可能不同。
3. **数据优先于推导，推导不出来就承认不知道。** 返回 `has_price_limit=False, confidence=0`，绝不静默猜 10%。

---

## 1. `LimitRuleEngine`

### 1.1 涨跌停规则的对象：显式三态（V0.1.1 裁决）

```python
class PriceLimitMode(Enum):
    LIMITED    = "limited"     # 有涨跌幅限制
    UNLIMITED  = "unlimited"   # 当日无涨跌幅限制（新股前 5 日 / 退市整理期首日）
    UNKNOWN    = "unknown"     # 规则未知
```

```python
@dataclass(frozen=True)
class DailyLimit:
    mode: PriceLimitMode
    up_limit: Decimal | None        # LIMITED 时必存在；UNLIMITED / UNKNOWN 时必 None
    down_limit: Decimal | None      # LIMITED 时必存在；UNLIMITED / UNKNOWN 时必 None
    rule_id: str | None
    source: LimitSource             # PROVIDER | DERIVED | UNKNOWN
```

**三态必须互斥且可区分**。V0.1 的 `has_price_limit: bool` 被否定——`False` 同时
表示"真无限制"和"不知道"会引诱下游把"缺数据"当成"可以自由涨跌"。这是非常安静的一类错误。

UNLIMITED 与 UNKNOWN 的区别由 tests/test_limit_detection.py::TestUnknownModeIsNotUnlimited 守护。

### 1.2 三级解析顺序

```
① PROVIDER：Tushare stk_limit 有实测值？
     是 → 直接采用，source = PROVIDER
     否 ↓
② DERIVED：按 (board, stock_status, trade_date) 查 configs/limit_rules.yaml 规则表
     命中 → 按规则计算，source = DERIVED
     未命中 ↓
③ UNKNOWN：mode = UNKNOWN，up_limit = down_limit = None
     source = UNKNOWN
```

**为什么 PROVIDER 优先**：交易所口径的涨跌停价是事实，规则推导是猜测。规则表覆盖不全、变更滞后、边界情形（退市整理期首日）都会出错。有实测值就永远别猜。

### 1.3 Provider 哨兵值契约（V0.1.1 实证更新，必读）

**核心原则**：哨兵值是 **Provider 数据源编码**，不是交易所价格。
Core 永远不知道哨兵值长什么样，只看到 `PriceLimitMode.UNLIMITED`。

#### 1.3.1 Tushare `stk_limit` 的真实哨兵行为（2026-09-04 实测）

**实测结论**：Tushare 用 `up_limit=999999.999 / down_limit=0.01`（创业板注册制首批
部分样例为 `up_limit=1000000.0`）表示"当日无涨跌幅限制"。

⚠️ V0.1 文档曾写作 `-1`，那是第三方数据表的表示，**不是 Tushare 契约**。本轮已修正。

| 字段 | 实测值 | 出处样本 |
|---|---|---|
| up_limit | `999999.999` 或 `1000000.0` | 001248.SZ 2026-07-06 / 300860.SZ 2020-08-24 |
| down_limit | `0.01` | 同上 |

**多重交叉验证**：

- 2020-08-24 创业板注册制首批共 18 只新股，全部命中哨兵。
  "首批 18 家"与公开事实完全吻合。
- 2026-07-06 / 2025-08-15 / 2021-12-01 各命中 1 只，均为新股。
- 与 `daily.pct_chg` 交叉：`300869.SZ` 2020-08-24 当日实际涨幅 **+1061.42%**，
  若存在 20% 限制不可能实现。
- 全部扫描日期（含历史）中 `-1` **零命中**。

#### 1.3.2 adapter 内集中识别

```python
# 仅在 TushareProvider 内可见
_NO_LIMIT_UP_THRESHOLD = CONFIG.data.tushare_no_limit_up   # 默认 999999
_NO_LIMIT_DOWN_THRESHOLD = CONFIG.data.tushare_no_limit_down  # 默认 0.01

if up >= _NO_LIMIT_UP_THRESHOLD and down <= _NO_LIMIT_DOWN_THRESHOLD:
    return DailyLimit(mode=PriceLimitMode.UNLIMITED, source=LimitSource.PROVIDER)
```

两条边条件**必须同时**满足，单边不构成哨兵（防回归测试守护）。

#### 1.3.3 契约回归测试

`tests/test_tushare_adapter.py::TestSentinelContractGuard` 包含四项契约守卫：

1. **当前哨兵值文档**：断言 fixtures 等于实测值。若变化 → RED，强制人工审查 Tushare 当前 API。
2. **哨兵被识别为 UNLIMITED**：up/down_limit 在 Core 视图里必须为 None。
3. **极端正常价不误判**：1000000.0 这样的正常涨跌停价不会被错误归类。
4. **-1 不被静默归类**：任何 -1 都应被 `DailyLimit.__post_init__` 拒绝或上抛，不归类为 UNLIMITED。

任何一项变红 ⇒ 不要默默改 adapter，而是先复测 Tushare 当前行为。

### 1.4 价格计算精度

- `tick = Decimal("0.01")`
- 舍入用 `ROUND_HALF_UP`（四舍五入），**不用** Python 默认的银行家舍入。交易所口径是四舍五入。
- 自算结果必须与 `stk_limit` 实测值交叉校验：`|自算 − 实测| > 0.005` ⇒ 告警。
- 所有涨跌停比较在 `Decimal` 域完成，容差 `tick / 2`。

---

## 2. 涨跌停规则版本表

> 这是 **DERIVED 路径**的规则表（V0.2 落地为 `configs/limit_rules.yaml`）。
> ⚠️ **置信度中/低的条目必须先用 `stk_limit` 实测反推确认，不得直接写进代码。**

| 板块 | 状态 | 涨跌幅 | 生效起 | 生效止 | 置信度 |
|---|---|---|---|---|---|
| 沪深主板 | 正常 | **±10%** | 1996-12-16 | — | 高 |
| 沪深主板 | 风险警示（ST / *ST） | ±5% | 2012 | **2026-07-05** | 高 |
| 沪深主板 | 风险警示（ST / *ST） | **±10%（已生效）** | **2026-07-06** | — | **高（实证）** |
| 沪深主板 | 新股（核准制） | +44% / −36% | 2014 | 2023-02（?） | 中 |
| 沪深主板 | 新股前 5 日 | **不限** | 2023 全面注册制（?） | — | **中（需更多实测）** |
| 沪深主板 | 退市整理期 | ±10%，**首日不限** | — | — | 中 |
| 创业板 | 正常 / ST / 退市整理 | **±20%** | 2020-08-24 | — | 高 |
| 创业板 | 新股前 5 日 | **不限** | 2020-08-24 | — | 高 |
| 科创板 | 正常 / 风险警示 / 退市整理 | **±20%** | 2019-07-22 | — | 高 |
| 科创板 | 新股前 5 日 | **不限** | 2019-07-22 | — | 高 |
| 科创板 | 退市整理首日 | **不限** | — | — | 中 |
| 北交所 | 全部 | **±30%** | 2021-11-15 | — | 高 |
| 北交所 | 新股首日 | **不限** | 2021-11-15 | — | 高 |

### 2.1 主板 ST 涨跌幅变更（2026-07-06 正式生效，已实证）

这是"为什么必须版本化"的现实例证：

| 时间 | 事件 |
|---|---|
| 2012 | 沪深交易所修订上市规则，ST / *ST 涨跌幅由 10% 调整为 **5%** |
| 2025-06-27 | 沪深交易所同步公告，就**拟将主板风险警示股票涨跌幅由 5% 调整为 10%** 公开征求意见 |
| **2026-04-24** | **上交所发布《上海证券交易所交易规则（2026年修订）》公告，主板风险警示股票涨跌幅由 5% 调整为 10%** |
| **2026-07-06** | **沪深北三所同步正式实施** |

**官方公告（V0.1.1 收尾重核）**：

- 上交所公告（sse.com.cn / c_20260424_10816474）：《上海证券交易所交易规则（2026年修订）》
- 深交所同步实施
- 北交所同步实施

> ⚠️ V0.1 文档曾标记"截至 2026-09-04 正式生效日期未能确认"，那是 V0.1 阶段
> 还没去查证交易所官方原文。本轮已纠正，详见 §9 资料来源。

### 2.1.1 实证（来自 Tushare `stk_limit` 实测涨跌停价）

| 标的 | 状态 | 日期 | pre_close | up_limit | 测算涨跌幅 |
|---|---|---|---|---|---|
| 000010.SZ（*ST美丽） | 主板风险警示 | **2026-06-30**（变更前） | 1.70 | 1.79 | **5.29%** ≈ 5% |
| 000010.SZ（*ST美丽） | 主板风险警示 | **2026-07-06**（变更首日） | 1.87 | 2.06 | **10.16%** ≈ 10% |
| 688022.SH（科创板风险警示） | 科创板 | 2026-08-03 | 7.90 | 9.48 | **20.00%** |
| 300010.SZ（创业板风险警示） | 创业板 | 2026-08-03 | 2.20 | 2.64 | **20.00%** |
| 920023.BJ（北交所风险警示） | 北交所 | 2026-08-03 | 1.82 | 2.36 | **29.67%** ≈ 30% |
| 000001.SZ（平安银行） | 主板正常 | 2026-08-21 | 11.40 | 12.54 | **10.00%** |

**`tests/test_limit_detection.py::TestOfficialRuleFacts`** 用上述涨跌停价作不变量检查：

```python
0.05 (before) → 0.10 (after)  # 2026-07-06 翻转已被证实
0.20          # 创业板 / 科创板 风险警示
0.30          # 北交所
```

**为什么这一项必须实测**：如果代码里写 `if is_st: rate = 0.05`，规则一变就会全错，
而且**错得很安静**——不会崩溃，只会让一段时期的回测结果失真。

### 2.2 已发现的资料冲突（需实测裁决）

**沪深主板新股前 5 日是否不设涨跌幅？**

- 来源 A（2025-08 券商答复）："沪深主板新股上市后的前 5 个交易日不设涨跌幅限制，第 6 日起 10%"
- 来源 B（百科词条）："主板股票上市首日涨跌幅限制为 −36% 至 +44%，从第二个交易日起 10%"

两者矛盾，合理推断是 **2023 年全面注册制前后规则不同**。V0.2 必须用 `stk_limit` 按日期实测确认，不能选一边相信。

### 2.3 尚未覆盖（V0.1 明确不处理）

- 老三板 / 退市后转让板
- B 股
- 可转债、ETF 涨跌幅规则
- 盘中临时停牌（新股首日临停机制）
- **价格笼子**（有效申报价格范围：买入 ≤ 基准价 102%，卖出 ≥ 98%）

价格笼子会影响可成交性，但 V0.1 按需求 §28「只做保守规则」不建模。

---

## 3. 涨跌停状态判定（V0.1.1）

```python
class LimitStatus(Enum):
    NONE        = "none"          # 未触及（可能当日无涨跌停限制）
    UP_TOUCH    = "up_touch"      # 盘中触及涨停但未封板（收盘回落）
    UP_LOCKED   = "up_locked"     # 收盘封涨停
    DOWN_TOUCH  = "down_touch"    # 盘中触及跌停但未封板
    DOWN_LOCKED = "down_locked"   # 收盘封跌停
```

**关键澄清**：

- `LimitStatus.NONE` 表示"确未触及"，适用【规则已知且价格未触及】。
- `UP_TOUCH` / `UP_LOCKED` 都意味着【当日向上的波动被制度硬性终止】——这是客观事实，与交易方向无关。
- 规则**未知**时（mode=UNKNOWN），`status=None`（Python `None`，不是枚举 NONE）。
  这与 NONE 严格区分，下游必须能区分"不知道"与"确未触及"。

判定（**在 raw 域，Decimal，容差 `tick/2`**）：

```python
if mode == UNLIMITED:           LimitStatus.NONE          # 不可能有制度事件
if mode == UNKNOWN:             status = None             # 无法判定
elif close >= up_limit - tol:   LimitStatus.UP_LOCKED
elif high  >= up_limit - tol:   LimitStatus.UP_TOUCH
elif close <= down_limit + tol: LimitStatus.DOWN_LOCKED
elif low   <= down_limit + tol: LimitStatus.DOWN_TOUCH
else:                           LimitStatus.NONE
```

`limit_locked = status in (UP_LOCKED, DOWN_LOCKED)`

### 3.1 制度截断标记（LIMIT_CENSORED）与 LimitStatus 的关系

V0.1.1 明确：LIMIT_CENSORED 是一个**独立的辅助 flag**，不混入 LimitStatus。

客观触发条件：窗口内任一 bar 的 `status ∈ {UP_TOUCH, UP_LOCKED, DOWN_TOUCH, DOWN_LOCKED}`
或 `is_one_word_limit`。该 bar 的【观测波动区间】必然被涨跌停制度截断。

**目的**：让下游知道这段 ATR 是制度压缩后的观测值，但 ATR 本身**必须**保持标准
Wilder 平滑（V0.1.1 裁决四：禁止跳过 TR=0）。

如果需要剔除制度影响，应新增独立变量 `constraint_adjusted_volatility`，而不是
把标准 ATR 改成自定义指标。V0.1.1 不实现该变量。

---

## 4. 一字板：几何事实与制度事件的分离

这是需求 §21–§23 的落地。核心矛盾：

> `O = H = L = C = 11`（一字涨停）在普通 OHLC 算法下得到 `range = 0, body = 0` ⇒ 被判成**十字星（弱）**。
> A 股语义下它是**极强买方控制 + 几乎买不到**。

### 4.1 分离为两个概念

```python
is_single_price_bar    = (O == H == L == C)                       # 纯几何事实
is_one_word_limit_up   = is_single_price_bar
                         and limit.mode == LIMITED
                         and abs(close - up_limit) <= tol         # 制度事件
is_one_word_limit_down = ... 同理
```

**为什么分开**：冷门股可能全天只有一笔成交，`O=H=L=C` 成立但不是涨停。那是"单点 bar"，不是一字板。把两者混为一谈会污染一字板统计。

- `is_single_price_bar` 且不触及限价 ⇒ 打 `SINGLE_PRICE_NON_LIMIT` flag，bar 几何字段返回 None。
- `is_one_word_limit_up` ⇒ 走 §4.2 的语义覆盖。

### 4.2 语义覆盖（V0.1.1）

| 字段 | 一字涨停 | 一字跌停 | 普通涨跌停 |
|---|---|---|---|
| `body_ratio` / `close_location` / 上下影比 | **`None`**（不适用） | **`None`** | 几何计算 |
| `bar_bias` | **+100** | **−100** | ATR 归一 × 实体占比 |
| `bar_control_score` | **100** | **100** | abs(bar_bias) |
| `bar_interpretation_source` | `LIMIT_EVENT` | `LIMIT_EVENT` | `GEOMETRY` |
| `is_one_word_limit_up` | True | False | False |

**禁止伪造 OHLC。** 不把 `O=H=L=C=11` 改成 `O=10,H=11,L=10,C=11`。虚拟价格绝不得进入 Execution、PnL、ATR、raw 结构。

**Gap 必须保留。** 一字涨停的 Snapshot 上同时存在三个独立维度：

```
bar_bias                 = +100    # 强买方控制
gap_qfq                  > 0       # 向上跳空
is_one_word_limit_up     = True    # 锁死（能否成交由 Execution 判定）
```

不合并成一个数。Brooks 模型自己决定怎么用。

---

## 5. 可交易状态（V0.1.1：不再使用 tradability_score）

**V0.1 设计的 `tradability_score` 在 V0.1.1 已被删除**。原因：

1. 原始定义（UP_TOUCH=60 / UP_LOCKED=20 / 流动性系数）含大量人为权重，无客观依据。
2. "可交易性"是 **Order-Side Specific**：
   - 一字涨停：BUY 很可能无法成交，SELL 可以成交。
   - 一字跌停：SELL 很可能无法成交，BUY 可以成交。
3. 不存在一个对买卖两侧同时适用的 `tradability_score`，前者会掩盖方向差异。

`MarketStateSnapshot` 第一版只保留**客观事实**：

```python
@dataclass(frozen=True)
class MarketConstraint:
    trade_date: date
    board: str
    stock_status: StockStatus
    market_status: MarketStatus
    limit: DailyLimit
    # ★ 没有 tradability_score 字段
```

**最终的成交判定必须发生在 ExecutionEngine.can_fill()**：

```python
def can_fill(order, raw_bar, market_constraint) -> FillResult:
    # 综合考虑：
    #   - BUY / SELL
    #   - 涨跌停状态（含一字板）
    #   - T+1 持仓锁定
    #   - 停牌 / market_status
    return ...
```

Execution 在拿到订单方向后才决定能不能成交。"能否交易"是订单行为，不是市场状态。

### 5.1 可交易状态枚举

```python
class MarketStatus(Enum):
    TRADABLE       # 正常
    SUSPENDED      # 全天停牌
    HALTED         # 盘中临时停牌（当日有成交，仍算可交易）
    PRE_LISTING    # 未上市
    DELISTED       # 已退市
```

**关键**：

- `HALTED` 与 `SUSPENDED` 必须区分：盘中临停当日有成交、有 K 线，按正常 bar 处理；全天停牌才是 gap bar。Tushare `suspend_d` 的 `suspend_timing` 字段有值即为日内停牌。
- `HALTED` 日算**可交易**（`is_tradable=True`），与 `SUSPENDED` 不同。`tests/test_schema.py::TestMarketStatusSemantics` 守护。
- 停牌日必须生成 gap bar。停牌日没有 bar，但它是交易日。不在序列里补占位记录，所有 `duration_bars` 都会少算。

---

## 6. T+1 与同 bar 模糊（V0.1.1 裁决三）

**T+1：当日买入，次一交易日方可卖出。** 规则定义在 Market Rules 层，执行在 Execution 层。这是刻意的切分：规则是市场事实，执行是持仓行为。

```python
def earliest_sell_date(entry_date: date, calendar) -> date:
    """A 股 T+1：当日买入，下一交易日方可卖出。"""
    return calendar.next_trading_day(entry_date)

def can_sell(entry_date: date, current_date: date, calendar) -> bool:
    return current_date >= earliest_sell_date(entry_date, calendar)
```

执行约束：

- Execution 引擎在收到任何 SELL 订单时，必须断言 `can_sell(position.entry_date, current_date)`。
- 违反即**抛错**，不是静默忽略。
- 禁止"当日买入、当日止损卖出"。

### 6.1 日 K 同 bar 模糊（V0.1.1 新增）

V0.1 曾提"同 bar 入场和止损同时触发 ⇒ 先止损（worst-case）"。
该简化不适合 A 股股票 T+1。必须按两种情况拆解：

#### 情况 A：当天新买入仓位（T+1 锁定）

```
T+1：Buy Stop 入场
同一根 bar Low 触及 Stop
```

T+1 锁阻止了当日卖出。**禁止模拟当天止损成交。** 记录：

```python
entry_filled               = True
same_day_stop_touched      = True
stop_filled                = False
reason                     = T_PLUS_ONE_LOCK
# 可记录 MAE，但不卖出
# 最早 T+2 才能执行卖出
```

#### 情况 B：此前已持有的仓位

同一根 bar 内可能 High 命中 Target、Low 命中 Stop，但日 K 不知道先后顺序。
标记 `INTRABAR_ORDER_AMBIGUOUS`，**默认采用 worst-case conservative fill**：

- 同时触及 target 和 stop ⇒ 选对策略最不利者。
- 绝不要假装知道真实发生顺序。

**该规则必须在 ExecutionEngine.can_fill() 中实现**，不在 Setup 中。V0.1.1 暂未实现 Execution 模块，但规则已在 V0.2 占位。

---

## 7. Trigger / Fill 分离协议

三个问题由三个模块分别回答，**不得合并**：

| 问题 | 谁回答 | 输出 |
|---|---|---|
| 有没有信号？ | Setup | `SetupSignal` |
| 触发价是多少？ | EntryModel | `OrderSpec` |
| 成交了吗？什么价？ | **ExecutionEngine** | `FillResult` |

Setup **不得**认定"第二天一定成交"。Execution **不得**修改触发价。

### 7.1 订单规格

```python
@dataclass(frozen=True)
class OrderSpec:
    direction: Direction          # BUY | SELL
    order_type: OrderType         # STOP（V0.1 只做 stop）
    trigger_price: Decimal        # 必须是 RAW 域
    price_domain: PriceDomain     # RAW —— 显式声明，防止 qfq/raw 混用
    valid_bars: int = 1           # 默认只存活一根 bar
    created_date: date            # 信号日 T
```

**`price_domain` 字段是强制的。** EntryModel 在 qfq 域算出触发价后，必须经 `PriceBridge` 转成 RAW 并显式标注。这样"忘记换算"会变成类型错误，而不是一个安静的错误收益曲线。

### 7.2 订单有效期（需求 §29）

> T 日产生 Setup → 计划 T+1 执行 → T+1 未成交 ⇒ **订单失效**。
> T+2 必须重新读取新的 Snapshot 重新判断。

这保留了 Brooks 的"逐 bar 重新判断"。也避免了一字涨停后订单悬挂造成的假成交。

### 7.3 成交判定（V0.1 保守规则）

```python
def resolve_fill(order: OrderSpec, bar: DailyBar, limit: DailyLimit) -> FillResult:
```

**Step 0 — 可交易性**

```
bar.tradable_status != TRADABLE
    → triggered=False, filled=False, reason=NOT_TRADABLE，订单失效
```

**Step 1 — 触发判定**

```
BUY  (buy stop,  上破)：triggered = bar.high >= trigger_price
SELL (sell stop, 下破)：triggered = bar.low  <= trigger_price
```

若触发价落在当日涨跌停范围之外（`trigger > up_limit` 或 `trigger < down_limit`，当 `has_price_limit=True`），则 `triggered=False, reason=TRIGGER_OUTSIDE_LIMIT_RANGE`。

**Step 2 — 限价锁定否决（方向相关）**

| 订单方向 | 当日状态 | 结果 |
|---|---|---|
| **BUY** | 一字涨停 | ❌ **不成交**（供不应求，买不到） |
| **BUY** | 一字跌停 | ✅ 可成交（供过于求，想买就能买） |
| **SELL** | 一字跌停 | ❌ **不成交**（卖不出去） |
| **SELL** | 一字涨停 | ✅ 可成交（想卖就能卖） |

> 这是需求 §27 情况 C 的推广。原文只覆盖"买入遇一字涨停"，但**限价锁定的成交性是方向相关的**——一字跌停时买入反而容易成交。只写一半会在跌停侧产生系统性错误。

非一字但收盘封板（`UP_LOCKED` / `DOWN_LOCKED`）：V0.1 假设**可以成交**（盘中确实有过成交）。

**Step 3 — 成交价**

```
BUY:
    open >= trigger  → filled=True, fill_price = open       # 跳空穿越
    open <  trigger  → filled=True, fill_price = trigger    # 盘中触及（前提 high >= trigger）

SELL:
    open <= trigger  → filled=True, fill_price = open       # 跳空穿越（低开）
    open >  trigger  → filled=True, fill_price = trigger    # 盘中触及（前提 low <= trigger）
```

**跳空穿越时按 `open` 成交，不得假设能按更优的 `trigger` 成交。** 这是回测里最爱被"优化"掉的一处，也是收益虚高的主要来源。

### 7.4 `FillReason` 完整枚举

| reason | triggered | filled | 说明 |
|---|---|---|---|
| `NOT_TRADABLE` | ✗ | ✗ | 停牌 / 未上市 / 已退市 |
| `NOT_TRIGGERED` | ✗ | ✗ | 价格未触及 |
| `TRIGGER_OUTSIDE_LIMIT_RANGE` | ✗ | ✗ | 触发价在当日涨跌停范围外 |
| `TOUCHED` | ✓ | ✓ | 盘中触及，按 trigger 成交 |
| `GAP_THROUGH` | ✓ | ✓ | 跳空穿越，按 open 成交（更差价） |
| `BUY_BLOCKED_BY_LIMIT_UP_LOCK` | ✓ | ✗ | 一字涨停买不到 |
| `SELL_BLOCKED_BY_LIMIT_DOWN_LOCK` | ✓ | ✗ | 一字跌停卖不出 |
| `T_PLUS_ONE_LOCK` | ✓（针对入场） / ✗（针对出场） | ✗ | 当日新买入锁，禁止卖出（T+1） |

### 7.5 明确不做（需求 §28）

- ❌ Level-2 / 逐笔
- ❌ 封单排队位置
- ❌ 订单队列
- ❌ 成交概率模型
- ❌ 涨停板"排到了就能成交"的任何假设

一字板一律按**不成交**处理。这是保守的、可证伪的、且方向自洽的。

### 7.6 订单生命周期 V0.1

V0.1 默认 `valid_bars = 1`：

- T 日收盘产生 Setup/Order
- 下一交易日有效
- 该交易日结束后无论是否成交 ⇒ **订单失效**
- T+2 必须重新读 Snapshot 重新判断

不实现 roll_forward 跨日挂单。保持 Brooks "逐 bar 重新判断"。

---

## 8. V0.1.1 已裁决 / 待办清单

### 8.1 已裁决（V0.1 → V0.1.1）

| # | V0.1 草案 | V0.1.1 裁决 | 落地 |
|---|---|---|---|
| C1 | Setup 只读单 Snapshot | **接受** `SnapshotWindow`（C1） | `INTERFACES.md` 已设计 |
| C5 | 跳过 TR=0 算 ATR | **拒绝**，ATR 保持标准 Wilder | `features/volatility.py` |
| C14 | 同 bar 先止损 | **拆分**：T+1 lock vs. worst-case conservative | §6.1 |
| tradability_score | V0.1 启发式 | **删除**，由 ExecutionEngine 方向判定 | §5 |
| 哨兵值 = -1 | V0.1 文档错误 | **纠正**为实测 999999.999 / 0.01 | §1.3 |
| 主板 ST 5%→10% | V0.1 未确认 | **已实证** 2026-07-06 生效 | §2.1 |
| 三态语义 | bool `has_price_limit` | **三态 enum** `PriceLimitMode` | `schema.py` |
| exhaustion/compression | V0.1 占位 | **删除** | 不实现 |
| trend.confidence | V0.1 占位 | **改名** `stability`，暂时不实现 | `MARKET_STATE_SNAPSHOT.md` |
| valid_for_date in Snapshot | V0.1 | **移至** Signal/OrderSpec | §7.6 |

### 8.2 V0.2 前待裁决（与本文件相关）

| # | 问题 | 备注 |
|---|---|---|
| Q1 | 主板新股（核准制）前 5 日是否不限涨跌幅？2023 全面注册制前后规则不同 | 必须用 `stk_limit` 按日期实测 |
| Q2 | 退市整理期个股的首日涨跌幅规则 | 公开资料不全 |
| Q3 | B 股 / 可转债 / ETF / 老三板的涨跌幅规则 | V0.1 不覆盖 |

---

## 9. 资料来源优先级（V0.1.1 裁决九）

按可信度自高到低：

1. **SSE / SZSE / BSE 官方规则**：sse.com.cn / szse.cn / bse.cn 发布
2. **CSRC**：csrc.gov.cn 部门规章
3. **Tushare `stk_limit` 实测涨跌停价**：交易所口径
4. **券商研究所 / 行情软件公告**
5. **百科、博客、论坛**：只能辅助定位，不能作为最终历史规则证据

### 9.1 本轮 V0.1.1 已核查的官方原文

- **上交所《上海证券交易所交易规则（2026年修订）》公告**（2026-04-24 发布）：
  sse.com.cn 转载链接 `https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml`（页面结构）。
  主板风险警示股票涨跌幅由 5% 调整为 10%，**2026年7月6日起正式实施**。
- **深交所、深交所下属主板同步实施**（公告标题与上交所同期发布）。
- **北交所同步实施**。

### 9.2 启示

V0.1 阶段"未能确认生效日"是因为只查了券商说法和百科。V0.1.1 已纠正。

**规则资料永远先看交易所原文再写进代码。**
