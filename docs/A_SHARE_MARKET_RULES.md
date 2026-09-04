# A_SHARE_MARKET_RULES.md — A 股制度层 V0.1

> 状态：**草案，待审核**。
> 所有规则日期为 2026-09-04 查证结果。**规则正在变化中**，接入前必须复验。

---

## 0. 设计立场

A 股制度不是"策略的边界条件"，它是一等公民。三条铁律：

1. **涨跌停幅度不得硬编码。** 不写 `if pct >= 10%`。
2. **规则按日期版本化。** 同一只股票 2019 年和 2026 年的涨停幅度可能不同。
3. **数据优先于推导，推导不出来就承认不知道。** 返回 `has_price_limit=False, confidence=0`，绝不静默猜 10%。

---

## 1. `LimitRuleEngine`

```python
@dataclass(frozen=True)
class LimitRuleInput:
    symbol: str
    trade_date: date
    board: str                    # SH_MAIN / SZ_MAIN / GEM / STAR / BSE
    stock_status: StockStatus     # NORMAL / ST / STAR_ST / NEW / DELISTING / DELISTED
    prev_close: Decimal           # 除权后前收
    bars_since_listing: int       # 新股判定
    days_in_delisting: int        # 退市整理期判定

@dataclass(frozen=True)
class LimitRuleResult:
    has_price_limit: bool
    up_limit: Decimal | None
    down_limit: Decimal | None
    rule_id: str                  # 追溯用，如 "SZ_GEM_20200824_20PCT"
    source: LimitSource           # PROVIDER | DERIVED | UNKNOWN
    confidence: float             # 0–100
```

### 1.1 三级解析顺序

```
① PROVIDER：Tushare stk_limit 有实测值？
     是 → 直接采用，confidence = 95，source = PROVIDER
     否 ↓
② DERIVED：按 (board, stock_status, trade_date) 查 configs/limit_rules.yaml 规则表
     命中 → 按规则计算，confidence = 70，source = DERIVED
     未命中 ↓
③ UNKNOWN：has_price_limit = False, up_limit = down_limit = None
     confidence = 0，quality_flags += ("LIMIT_UNKNOWN",)
```

**为什么 PROVIDER 优先**：交易所口径的涨跌停价是事实，规则推导是猜测。规则表覆盖不全、变更滞后、边界情形（如退市整理期首日）都会出错。有实测值就永远别猜。

**③ UNKNOWN 的含义**：不是"无涨跌幅限制"，是"**我不知道**"。这两者必须区分——

```python
@dataclass(frozen=True)
class DailyLimit:
    has_price_limit: bool      # False 可能是"确实无限制"，也可能是"未知"
    up_limit: Decimal | None   # 二者区分靠 up_limit 是 None 还是一个值
    ...
    source: LimitSource        # ← 真正的区分依据在这里
```

下游逻辑必须检查 `source`，不能只看 `has_price_limit`。

### 1.2 哨兵值处理（必读）

**Tushare `stk_limit` 用 `-1` 表示"当日无涨跌幅限制"。**

实测证据：2020-08-24 创业板注册制首日，`N康泰`（当日 +1061.42%）的 `up_limit` 与 `down_limit` 均为 `-1.00`。

```python
# 必须这样处理
if up_limit == -1 or down_limit == -1:
    has_price_limit = False
    up_limit = down_limit = None
# 绝不能把 -1 当成"涨停价是 -1 元"
```

忽略这一点，所有新股前 5 日、退市整理期首日都会被判成跌停，产生大量假信号。

### 1.3 价格计算精度

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
| 沪深主板 | ST / *ST | **±5%** | 2012 | **见下方警告** | **中（正在变）** |
| 沪深主板 | 新股（核准制） | +44% / −36% | 2014 | 2023-02（?） | 中 |
| 沪深主板 | 新股前 5 日 | **不限** | 2023 全面注册制（?） | — | **低（需实测）** |
| 沪深主板 | 退市整理期 | ±10%，**首日不限** | — | — | 中 |
| 创业板 | 正常 / ST / 退市整理 | **±20%** | 2020-08-24 | — | 高 |
| 创业板 | 新股前 5 日 | **不限** | 2020-08-24 | — | 高 |
| 科创板 | 正常 / 风险警示 / 退市整理 | **±20%** | 2019-07-22 | — | 高 |
| 科创板 | 新股前 5 日 | **不限** | 2019-07-22 | — | 高 |
| 科创板 | 退市整理首日 | **不限** | — | — | 中 |
| 北交所 | 全部 | **±30%** | 2021-11-15 | — | 高 |
| 北交所 | 新股首日 | **不限** | 2021-11-15 | — | 高 |

### 2.1 ⚠️ 活规则警告：主板 ST 涨跌幅正在从 5% 走向 10%

这是"为什么必须版本化"的现实例证：

| 时间 | 事件 |
|---|---|
| 2012 | 沪深交易所修订上市规则，ST / *ST 涨跌幅由 10% 调整为 **5%** |
| 2025-06-27 | 沪深交易所同步公告，就**拟将主板风险警示股票涨跌幅由 5% 调整为 10%** 公开征求意见 |
| **2026-04-10** | **上交所正式发布《上海证券交易所交易规则（征求意见稿）》，拟将主板 ST / *ST 涨跌幅由 5% 放宽至 10%** |

**状态：截至 2026-09-04，正式生效日期未能确认。**

这直接证明需求 §13 的判断是对的。如果代码里写着 `if is_st: rate = 0.05`，规则一变就会全错，而且**错得很安静**——它不会崩溃，只会让一段时期的回测结果失真。

**处理**：规则表条目带 `effective_from` / `effective_to` / `confidence`。ST 条目标注 `confidence=中`，并在 V0.2 接入时**用 `stk_limit` 实测反推真实生效日**后回填。

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

## 3. 涨跌停状态判定

```python
class LimitStatus(Enum):
    NONE        = 0   # 未触及（可能当日无涨跌停限制）
    UP_TOUCH    = 1   # 触及涨停但未封板（收盘回落）
    UP_LOCKED   = 2   # 收盘封涨停
    DOWN_TOUCH  = 3
    DOWN_LOCKED = 4
```

判定（**在 raw 域，Decimal，容差 `tick/2`**）：

```python
if not has_price_limit:
    LimitStatus.NONE
elif close >= up_limit - tol:      LimitStatus.UP_LOCKED
elif high  >= up_limit - tol:      LimitStatus.UP_TOUCH
elif close <= down_limit + tol:    LimitStatus.DOWN_LOCKED
elif low   <= down_limit + tol:    LimitStatus.DOWN_TOUCH
else:                              LimitStatus.NONE
```

`limit_locked = status in (UP_LOCKED, DOWN_LOCKED)`

---

## 4. 一字板：几何事实与制度事件的分离

这是需求 §21–§23 的落地。核心矛盾：

> `O = H = L = C = 11`（一字涨停）在普通 OHLC 算法下得到 `range = 0, body = 0` ⇒ 被判成**十字星（弱）**。
> A 股语义下它是**极强买方控制 + 几乎买不到**。

### 4.1 分离为两个概念

```python
is_single_price_bar    = (O == H == L == C)                       # 纯几何事实
is_one_word_limit_up   = is_single_price_bar
                         and has_price_limit
                         and abs(close - up_limit) <= tol         # 制度事件
is_one_word_limit_down = ... 同理
```

**为什么分开**：冷门股可能全天只有一笔成交，`O=H=L=C` 成立但不是涨停。那是"单点 bar"，不是一字板。把两者混为一谈会污染一字板统计。

- `is_single_price_bar` 且不触及限价 ⇒ 打 `quality_flags += ("SINGLE_PRICE_NON_LIMIT",)`，`bar_bias = None`。
- `is_one_word_limit_up` ⇒ 走 §4.2 的语义覆盖。

### 4.2 语义覆盖（对应需求 §23）

| 字段 | 一字涨停 | 一字跌停 |
|---|---|---|
| `body_ratio` / `close_location` / 上下影比 | **`None`**（不适用） | **`None`** |
| `bar_bias` | **+100** | **−100** |
| `bar_control_score` | **100** | **100** |
| `bar_interpretation_source` | `LIMIT_EVENT` | `LIMIT_EVENT` |
| `tradability_score` | **≈0**（对买入方） | **≈0**（对卖出方） |

**禁止伪造 OHLC。** 不把 `O=H=L=C=11` 改成 `O=10,H=11,L=10,C=11`。虚拟价格绝不得进入 Execution、PnL、ATR、raw 结构。

**Gap 必须保留。** 一字涨停的 Snapshot 上同时存在三个独立维度：

```
bar_bias           = +100    # 强买方控制
gap_qfq            > 0       # 向上跳空
tradability_score  ≈ 0       # 锁死，买不到
```

不合并成一个数。Brooks 模型自己决定怎么用。

---

## 5. 可交易性 `tradability_score`

**与"价格行为强弱"正交的第二个轴。** 一只票可以 simultaneously 极强且完全买不到。

```python
def tradability_score(status, limit_status, is_one_word_up, is_one_word_down,
                      turnover_ratio) -> float:
    if status != TRADABLE:            return 0.0
    if is_one_word_up or is_one_word_down:  return 0.0
    base = {
        NONE:       100.0,
        UP_TOUCH:    60.0,
        DOWN_TOUCH:  60.0,
        UP_LOCKED:   20.0,
        DOWN_LOCKED: 20.0,
    }[limit_status]
    liquidity = clamp(turnover_ratio, 0.5, 1.0)   # 相对自身 20 日中位换手率
    return base * liquidity
```

> V1 启发式，**待验证**。但它的语义是明确的：**0 = 完全无法成交，100 = 正常**。且它回答的是"能不能交易"，与 `bar_bias` 回答的"强不强"是两个问题。

### 5.1 可交易状态

```python
class TradableStatus(Enum):
    TRADABLE          # 正常
    SUSPENDED         # 停牌（全天）
    HALTED            # 盘中临时停牌（当日有成交，不算停牌日）
    PRE_LISTING       # 未上市
    DELISTING_PERIOD  # 退市整理期（可交易，但规则特殊）
    DELISTED          # 已退市
```

**停牌日必须生成 gap bar。** 停牌日没有 bar，但它是交易日。不在序列里补占位记录，所有 `duration_bars` 都会少算（见 DATA_LAYER §4.4）。

**`HALTED` 与 `SUSPENDED` 必须区分**：盘中临停当日有成交、有 K 线，按正常 bar 处理；全天停牌才是 gap bar。Tushare `suspend_d` 的 `suspend_timing` 字段有值即为日内停牌。

---

## 6. T+1

**规则定义在 Market Rules 层，执行在 Execution 层。** 这是刻意的切分：规则是市场事实，执行是持仓行为。

```python
def earliest_sell_date(entry_date: date, calendar: TradeCalendar) -> date:
    """A 股 T+1：当日买入，次一交易日方可卖出。"""
    return calendar.next_trading_day(entry_date)

def can_sell(entry_date: date, current_date: date, calendar) -> bool:
    return current_date >= earliest_sell_date(entry_date, calendar)
```

执行约束：

- Execution 引擎在收到任何 SELL 订单时，必须断言 `can_sell(position.entry_date, current_date)`。
- 违反即**抛错**，不是静默忽略。
- 禁止"当日买入、当日止损卖出"。
- 当日买入的持仓**可以**设置止损计划，但该止损最早只能在 T+1 触发。

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

### 7.5 明确不做（需求 §28）

- ❌ Level-2 / 逐笔
- ❌ 封单排队位置
- ❌ 订单队列
- ❌ 成交概率模型
- ❌ 涨停板"排到了就能成交"的任何假设

一字板一律按**不成交**处理。这是保守的、可证伪的、且方向自洽的。

---

## 8. 需您裁决的问题

| # | 问题 | 我的倾向 |
|---|---|---|
| R1 | 规则表置信度"中/低"的条目，是否接受 V0.2 用 `stk_limit` 实测反推后再回填，而不是现在就拍板？ | 接受。拍板 = 把一个会变的规则写死 |
| R2 | 非一字但收盘封板（如尾盘封板），V0.1 假设"可成交"，是否接受？ | 接受。保守但不失真——盘中确实有成交 |
| R3 | `tradability_score` 用启发式表格，是否接受其 V1 性质？ | 接受，但必须在 Filter 层可覆盖 |
| R4 | 退市整理期、老三板、B 股、可转债 V0.1 不覆盖，是否接受？ | 接受。先做沪深主板 + 创业板 + 科创板 + 北交所 |
| R5 | 价格笼子（102%/98%）不建模，是否接受？ | 接受。它影响的是"能否以某价成交"，V0.1 的 stop 触发价通常不会触及边界；但需在文档标注为已知偏差 |
