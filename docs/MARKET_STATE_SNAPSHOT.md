# MARKET_STATE_SNAPSHOT.md — 字段规范 V0.1

> 状态：**草案，待审核**。
> Snapshot 是整个项目最重要的接口。Core 只产出它，Setup 只消费它。

---

## 0. 四条设计纪律

1. **连续优先。** 能用 `0~100` 或 `-100~+100` 就别用布尔。布尔是给最后一步决策用的，不是给测量用的。
2. **每个分数必须声明归一化基准。** 没有基准的"分数"不可复现，等于没有定义。
3. **不适用返回 `None`，不返回 `0`。** 一字板的 `body_ratio` 是"不适用"，不是"弱"。返回 0 会污染所有下游统计、分位数和阈值。
4. **只用 `<= bar_date` 的数据。** 任何滚动统计（ATR、分位、均值、极值）越界即为未来函数。

---

## 1. 归一化基准（全文共用）

所有 Level B 分数都由下面两个函数之一生成，参数集中在 `configs/features.yaml`：

```python
# 距离 / 强度类 → 0..100
def score_atr(d: float, atr: float, cap_atr: float = 3.0) -> float:
    """d >= 0，ATR 归一化到 0..100。cap_atr=3 表示"3 倍 ATR 距离 = 满分"。"""
    return 100.0 * clamp(d / (cap_atr * atr), 0.0, 1.0)

# 方向类 → -100..+100
def bias_atr(net: float, atr: float, cap_atr: float = 3.0) -> float:
    """net 可正可负。"""
    return 100.0 * clamp(net / (cap_atr * atr), -1.0, 1.0)
```

**为什么统一到 ATR 归一化（绝对型）而不是历史分位数（相对型）：**

| | ATR 归一化（采用） | 滚动分位数（备选） |
|---|---|---|
| 横截面可比 | ✅ 不同标的同一分数含义相同 | ❌ 每只股票的分母不同 |
| 时序可比 | ✅ | ❌ 同一分数在不同时期含义漂移 |
| 未来函数风险 | 无（ATR 只用历史） | **有**——全样本分位数是未来函数 |

滚动分位数**不是不能用**，但必须严格用 `<= T` 的窗口，且必须在字段定义里写明窗口长度。V0.1 全部字段走 ATR 归一化，把变量空间收敛下来。

`ATR` 定义：Wilder 平滑，`n=14`，作用于 **qfq** 的 H/L/C。

**`ATR == 0` 时**（极端一字板/长期停牌导致），所有 ATR 归一化字段返回 `None` 并打 `quality_flags += ("ATR_ZERO",)`。**不返回 0，不返回 100。**

---

## 2. Block 0 — 身份与因果

| 字段 | 类型 | 说明 |
|---|---|---|
| `symbol` | `str` | 内部统一 `000001.SZ` 后缀式 |
| `bar_date` | `date` | 数据日 **T** |
| `as_of` | `str` | 固定 `"T_CLOSE"`。观察时点 |
| `valid_for_date` | `date` | **T+1 交易日**。该快照唯一允许驱动的执行日 |
| `trading_day_index` | `int` | 交易日序号（见 DATA_LAYER §4.4） |
| `provenance` | `Provenance` | 见 §11 |

**为什么要有 `valid_for_date`**：把"T 日收盘确认、T+1 执行"这条规则写进数据结构，而不是写进注释。执行引擎入口断言 `snapshot.valid_for_date == 当前执行日`，未来函数就从"靠自觉"变成"类型不允许"。

---

## 3. Block `bar` — 原子特征（Level A）

> 全部基于 **qfq** OHLC 计算。除注明外，均为**当日截面计算，无窗口，零未来函数风险**。

| 字段 | 类型 | 输入 | 数学定义 | 范围 | 含义 | 为什么存在 | Setup | 未来函数 |
|---|---|---|---|---|---|---|---|---|
| `body_ratio` | `float \| None` | O,H,L,C | `\|C-O\| / (H-L)` | 0–1 | 实体占全幅比例 | Brooks 判断趋势 bar 的第一测量；实体越大方向越明确 | ✓ | 无 |
| `upper_wick_ratio` | `float \| None` | O,H,L,C | `(H - max(O,C)) / (H-L)` | 0–1 | 上影占比 | 上方拒绝/卖压 | ✓ | 无 |
| `lower_wick_ratio` | `float \| None` | O,H,L,C | `(min(O,C) - L) / (H-L)` | 0–1 | 下影占比 | 下方承接/买盘 | ✓ | 无 |
| `close_location` | `float \| None` | O,H,L,C | `(C - L) / (H-L)` | 0–1 | 收盘在当日区间的位置 | Brooks 最倚重的单 bar 特征；收盘位置决定 bar 的多空性质 | ✓ | 无 |
| `range_atr_ratio` | `float \| None` | H,L,ATR | `(H-L) / ATR` | 0–~10 | 当日波动相对常态倍数 | 判断"是不是一根大 bar"，是所有强度分数的分母 | ✓ | 低（ATR 限 T 内） |
| `overlap_ratio` | `float \| None` | H,L, H₋₁,L₋₁ | `max(0, min(H,H₋₁) - max(L,L₋₁)) / (H-L)` | 0–1 | 与前一 bar 的价格重叠度 | Brooks 用重叠度判断趋势纯度：强趋势 bar 之间重叠少，震荡区重叠多 | ✓ | 无（用 t−1） |
| `gap_qfq` | `float \| None` | qfq Oₜ, Cₜ₋₁ | `O_t - C_{t-1}` | ℝ | **Price Action 语义跳空** | 除权日已由 qfq 消除，这是"真跳空" | ✓ | 无 |
| `gap_percent` | `float \| None` | 同上 + Cₜ₋₁ | `gap_qfq / C_{t-1}` | ℝ | 相对跳空 | 跨价格水平可比 | ✓ | 无 |
| `gap_atr` | `float \| None` | 同上 + ATR | `gap_qfq / ATR` | ℝ | ATR 归一化跳空 | 跨标的/跨时期可比，可直接作为分数量级 | ✓ | 低 |
| `gap_direction` | `int` | 派生 | `sign(gap_percent)` | −1/0/1 | 跳空方向 | 只读派生视图，便于人类阅读 | ◐ | 派生 |
| `is_single_price_bar` | `bool` | O,H,L,C | `O == H == L == C` | — | **纯几何事实**：全天只有一个价格 | 与"制度事件"分离。冷门股也可能全天一笔成交，那不是一字板 | ✓ | 无 |
| `bar_bias` | `float \| None` | 见 §3.1 | 见 §3.1 | −100…+100 | 单 bar 方向控制强度（带符号） | 把"这根 bar 谁说了算"连续化，而不是先判多空 | ✓ | 无/低 |
| `bar_control_score` | `float \| None` | 见 §3.1 | 见 §3.1 | 0–100 | `\|bar_bias\|`，方向控制强度 | 同上，去符号版 | ✓ | 无/低 |
| `bar_interpretation_source` | `enum` | — | `GEOMETRY \| LIMIT_EVENT` | — | **本 bar 的特征来自几何还是制度事件** | 审计用。下游必须知道某个分数是被制度覆盖过的 | ✓ | 无 |
| `tradability_score` | `float` | 见 A_SHARE §5 | 见 A_SHARE §5 | 0–100 | **可交易性** | 与"价格行为强弱"正交的第二个维度 | ✓ | 无 |

### 3.1 一字板的特殊解释（对应需求 §21–§24）

这是本项目最容易做错的一处。核心矛盾：

> `O=H=L=C=11`（一字涨停）在普通几何算法下得到 `range=0, body=0` ⇒ **被判成十字星（弱）**。
> 但 A 股语义下它是**极强买方控制 + 几乎买不到**。

**处理方案：几何为 `None`，语义走覆盖，可交易性单独一个轴。**

| 条件 | `body_ratio` | `close_location` | `upper/lower_wick_ratio` | `bar_bias` | `bar_control_score` | `bar_interpretation_source` |
|---|---|---|---|---|---|---|
| 普通 bar | 几何计算 | 几何计算 | 几何计算 | 几何计算 | 几何计算 | `GEOMETRY` |
| 一字涨停 | **`None`** | **`None`** | **`None`** | **+100** | **100** | `LIMIT_EVENT` |
| 一字跌停 | **`None`** | **`None`** | **`None`** | **−100** | **100** | `LIMIT_EVENT` |
| 单点 bar（非涨跌停） | `None` | `None` | `None` | `None` | `None` | `GEOMETRY` + flag |

普通 bar 的几何式：

```python
bar_bias = bias_atr((C - O), ATR) * body_ratio      # 方向 × 实体置信
bar_control_score = abs(bar_bias)
```

**关键：绝不伪造 OHLC。** 不会把 `O=H=L=C=11` 改成 `O=10,H=11,L=10,C=11`。Raw OHLC 永远是真实数据，任何虚拟价格都不得进入 Execution、PnL、ATR、raw 结构。

**同时保留 Gap 维度。** 一字涨停的 Snapshot 上会同时出现：

- `bar_bias = +100`（强买方控制）
- `gap_qfq > 0`（向上跳空）
- `tradability_score ≈ 0`（锁死，买不到）

三个维度并存，Brooks 模型自己决定怎么用。**不合并成一个数。**

---

## 4. Block `structure` — 结构（Level B）

`SwingPoint = (date, trading_day_index, price, kind: HIGH\|LOW, strength: float 0–100, detector_id: str)`

| 字段 | 类型 | 输入 | 数学定义 | 范围 | 含义 | 为什么存在 | Setup | 未来函数 |
|---|---|---|---|---|---|---|---|---|
| `last_swing_high` | `SwingPoint \| None` | SwingDetector | — | — | 最近一个已确认的摆动高点 | 突破、止损、leg 的锚点 | ✓ | **见 §4.1** |
| `last_swing_low` | `SwingPoint \| None` | SwingDetector | — | — | 最近一个已确认的摆动低点 | 同上 | ✓ | 同上 |
| `structure_bias` | `float` | 最近 k 个 swing | 见 §4.2 | −100…+100 | 短周期结构倾向 | 与 `trend.bias` 尺度不同（短 vs 长），不是冗余 | ✓ | 无 |
| `current_leg` | `LegState` | swing 序列 | 见 §4.3 | — | 当前推进段 | 量度"这一腿走了多远、多有力" | ✓ | 无 |
| `current_pullback` | `PullbackState \| None` | leg + 价格 | 见 §4.4 | — | 当前回撤 | Brooks 的入场窗口 | ✓ | 无 |

### 4.1 Swing 确认延迟 —— 必须显式声明

**摆动点在事后 N 根 bar 才能确认。** 一个分形高点需要右侧 N 根 bar 都不超过它，才能确认它是高点。

因此 `last_swing_high` 在 T 日能给出的，只能是**在 T 日之前已经确认**的 swing，而不是"事后看 T 附近的最高点"。

**这是整个项目最大的未来函数风险点。** 强制要求：

- `SwingDetector` 接口必须暴露 `confirmation_lag: int` 属性。
- Snapshot 必须记录 `last_swing_high.date + confirmation_lag <= bar_date`，否则构造期抛错。
- 任何"看起来像最高点但尚未确认"的价格都不得写入 `last_swing_high`。

> 这一条如果做错，回测会漂亮得不像话，然后实盘归零。

### 4.2 `structure_bias`

```
取最近 k 个已确认 swing（默认 k=4），按时间排序：
  rise  = Σ max(swing[i].price - swing[i-1].price, 0)
  fall  = Σ max(swing[i-1].price - swing[i].price, 0)
  structure_bias = bias_atr(rise - fall, ATR, cap_atr=3.0)
```

### 4.3 `LegState`

| 子字段 | 类型 | 定义 | 范围 |
|---|---|---|---|
| `direction` | `int` | `+1` 上升腿 / `−1` 下降腿 / `0` 无 | −1/0/1 |
| `start_date` | `date` | 腿起点（上一个反向 swing） | — |
| `duration_bars` | `int` | `trading_day_index[t] - trading_day_index[start]` | ≥0 |
| `return_pct` | `float` | `(close - start_price) / start_price` | ℝ |
| `atr_distance` | `float` | `(close - start_price) / ATR`，带符号 | ℝ |
| `strength` | `float` | `score_atr(\|close - start_price\|, ATR)` | 0–100 |

**`duration_bars` 必须用交易日序号差，不能用列表索引差。** 停牌日不产生 bar 但它是交易日，用索引会系统性少算。

### 4.4 `PullbackState`

> ⚠️ **命名歧义警告（需裁决）**：原方案写作 `pullback.strength`。这个词有两个相反的解释——"回撤很深"还是"趋势扛住了回撤"？两者对交易的含义完全相反。
> **本草案改为 `depth_score`，语义唯一：越大 = 回撤越深（对趋势越不利）。** 不使用 `strength`。

| 子字段 | 类型 | 定义 | 范围 |
|---|---|---|---|
| `exists` | `bool` | 当前是否处于逆势回撤中 | — |
| `duration_bars` | `int` | 回撤已持续的**交易日数** | ≥0 |
| `depth_atr` | `float` | `(leg_extreme - current_extreme) / ATR`，带符号 | ℝ |
| `retrace_ratio` | `float` | `\|回撤幅度\| / \|leg 幅度\|` | 0–~1.5 |
| `depth_score` | `float` | `score_atr(\|回撤幅度\|, ATR)` | 0–100 |

`retrace_ratio` 是直接可解释的（Brooks 用 1/3、1/2、2/3 回撤）；`depth_score` 用于跨标的比较。**两个都留，因为它们回答不同问题。**

---

## 5. Block `trend` — 趋势（Level B）

`TrendModel` 可替换（见 INTERFACES.md）。V0.1 只实现 `StructureTrendModel`。

| 字段 | 类型 | 定义 | 范围 | 含义 | Setup | 未来函数 |
|---|---|---|---|---|---|---|
| `bias` | `float` | 见下 | −100…+100 | 趋势倾向：**主输出**。符号即方向，绝对值即强度 | ✓ | 无 |
| `strength` | `float` | `\|bias\|` | 0–100 | 趋势强度（去符号） | ✓ | 派生 |
| `confidence` | `float` | 见下 | 0–100 | **可信度**，与强度正交 | ✓ | 无 |
| `direction` | `int` | `sign(bias)` | −1/0/1 | **只读派生视图**，供人类阅读与调试 | ◐ | 派生 |
| `model_id` | `str` | — | — | 可复现性 | ◐ | — |

**`StructureTrendModel` 的 `bias`**：

```
n = trend_lookback（默认 20 交易日）
net   = close[t] - close[t-n]
bias  = bias_atr(net, ATR, cap_atr=3.0)
```

**`confidence`（强度之外的第二个维度）**：

```
warmup    = clamp(n_effective_bars / warmup_bars, 0, 1)     # 样本充足度
chop_ratio= Σ|bar 逆向位移| / Σ|bar 位移|                     # 路径曲折度
confidence= 100 × warmup × (1 - chop_ratio)
```

> V1 启发式，待验证。它回答的问题是"这个强度值我信几分"，与"强度有多大"是不同的问题——一根直线拉升和一段反复拉锯可能净位移相同，但可信度天差地别。

**采不采用 `direction` 这个字段**：它与需求 §15「不要先判断 Trend = True」的精神有张力。保留它，但**定位为调试视图**，Setup 应主要消费 `bias` / `strength` / `confidence`。这样既满足可读性，又不让布尔污染测量层。

---

## 6. Block `range` — 震荡区间（Level B）

| 字段 | 类型 | 定义 | 范围 | 含义 | Setup | 未来函数 |
|---|---|---|---|---|---|---|
| `score` | `float` | `100 × (1 − ER)`，见下 | 0–100 | 震荡程度（越高越像区间） | ✓ | 无 |
| `upper` | `float` | `max(high[t-n+1..t])` | — | 区间上沿 | ✓ | 无 |
| `lower` | `float` | `min(low[t-n+1..t])` | — | 区间下沿 | ✓ | 无 |
| `position` | `float` | `(close − lower) / (upper − lower)` | 0–1 | 收盘在区间中的位置 | ✓ | 无 |
| `width_atr` | `float` | `(upper − lower) / ATR` | >0 | 区间宽度（ATR 单位） | ✓ | 低 |
| `upper_touches` | `int` | 触及上沿次数 | ≥0 | score 的可信度支撑 | ◐ | 无 |
| `lower_touches` | `int` | 触及下沿次数 | ≥0 | 同上 | ◐ | 无 |
| `bars_in_range` | `int` | 区间已持续的 bar 数 | ≥0 | 同上 | ◐ | 无 |

**`score` 用 Kaufman 效率比（ER），客观且连续：**

```
ER    = |close[t] - close[t-n]| / Σ_{i=t-n+1..t} |close[i] - close[i-1]|
score = 100 × (1 - ER)
```

- `ER → 1`：直线推进 ⇒ `score → 0`（纯趋势）
- `ER → 0`：来回拉锯 ⇒ `score → 100`（纯震荡）

> **原方案的 `range_probability` 已删除。** 它不是统计概率，也未做过校准。叫 "probability" 会诱导下游把它当概率用。`score` 是一个可观测、可复现的量。

`upper` / `lower` 是**滚动窗口极值**，只用 `<= T` 的数据。

---

## 7. Block `pressure` — 多空压力（Level B）

| 字段 | 类型 | 定义 | 范围 | 含义 | Setup | 未来函数 |
|---|---|---|---|---|---|---|
| `bull` | `float` | 见下 | 0–100 | 买方压力 | ✓ | 无 |
| `bear` | `float` | 见下 | 0–100 | 卖方压力 | ✓ | 无 |
| `delta` | `float` | `clamp(bull − bear, −100, 100)` | −100…+100 | 净压力 | ✓ | 派生 |

```
窗口 n（默认 10 交易日），全部用 qfq：
  bull_raw = Σ max(C−O, 0) + Σ lower_wick      # 实体上涨 + 下影（买方承接）
  bear_raw = Σ max(O−C, 0) + Σ upper_wick      # 实体下跌 + 上影（卖方拒绝）
  bull = score_atr(bull_raw, n × ATR)
  bear = score_atr(bear_raw, n × ATR)
```

下影线计入买方、上影线计入卖方，是 Brooks 的"影线 = 拒绝"读法。

> ⚠️ **`bull + bear ≠ 100`。** 它们不是互补份额，是两个独立的强度测量。剧烈震荡时两者**都**高；无量横盘时两者**都**低。任何把它们当份额使用的代码都是错的。

---

## 8. Block `breakout` — 突破（Level B）

| 字段 | 类型 | 定义 | 范围 | 含义 | Setup | 未来函数 |
|---|---|---|---|---|---|---|
| `bias` | `float` | `sign × strength` | −100…+100 | 突破方向 + 力度 | ✓ | 无 |
| `strength` | `float` | `score_atr(\|close − reference_level\|, ATR)` | 0–100 | 突破幅度 | ✓ | 低 |
| `follow_through` | `float` | 见下 | 0–100 | 突破后延续程度 | ✓ | 无 |
| `failure_score` | `float` | 见下 | 0–100 | 突破失败程度 | ✓ | 无 |
| `reference_level` | `float` | 被突破的 swing / 区间边界 | — | 突破参照位 | ✓ | 无 |
| `bars_since` | `int` | 突破至今的**交易日数** | ≥0 | 新鲜度 | ✓ | 无 |
| `window_bars` | `int` | 统计窗口（默认 5） | — | 配置透出 | ◐ | — |
| `is_active` | `bool` | `bars_since <= window_bars` | — | 突破是否仍在观察窗内 | ✓ | 无 |

```
follow_through = score_atr(突破后最大有利位移, ATR)
failure_score  = min(100,
                     score_atr(突破后回穿参考位的深度, ATR)
                     + (50 if close 已回到参考位错误一侧 else 0))
```

`follow_through` 与 `failure_score` **不是互补的**：一次突破可以同时"延续了一段"又"最后失败了"。两者独立测量，这是刻意的。

`bars_since > window_bars` 时 `is_active=False`，但 `strength` 仍保留（供历史分析）。

---

## 9. Block `behavior` — 市场行为（Level B）

| 字段 | 类型 | 定义 | 范围 | 含义 | Setup | 未来函数 |
|---|---|---|---|---|---|---|
| `climax_score` | `float` | 见下 | 0–100 | 高潮（耗尽式推进）程度 | ✓ | 无 |

```
climax_score = 100 × clamp(
    w1 × clamp((H−L) / (2×ATR) / 3, 0, 1)          # 波动异常放大
  + w2 × clamp(volume / median_volume_20d / 3, 0, 1) # 成交量异常放大
  + w3 × clamp(|close − EMA20| / (3×ATR), 0, 1)      # 偏离均线过远
, 0, 1)
权重默认 w1=w2=w3=1/3，进 configs/features.yaml
```

三个分量都只用 `<= T` 的数据。`median_volume_20d` 是滚动窗口中位数。

> **原方案的 `exhaustion_score` 与 `compression_score` 推迟到 V2。** 理由见 [DESIGN_CONFLICTS.md](DESIGN_CONFLICTS.md) §3：这两个概念目前缺客观定义，先实现只会变成可任意调参的主观拟合项。

---

## 10. Block `market_constraint` — A 股制度约束

> 详细定义见 [A_SHARE_MARKET_RULES.md](A_SHARE_MARKET_RULES.md)。

| 字段 | 类型 | 范围 | 含义 | Setup 使用 | 未来函数 |
|---|---|---|---|---|---|
| `board` | `str` | — | 板块（决定涨跌幅规则） | ◐ 只读 | 无 |
| `stock_status` | `enum` | — | `NORMAL / ST / STAR_ST / NEW / DELISTING / DELISTED` | ✓ | 无 |
| `has_price_limit` | `bool` | — | 当日是否有涨跌幅限制 | ✓ | 无 |
| `up_limit` | `Decimal \| None` | — | 涨停价。`None` = 未知（**不得猜**） | ◐ | 无 |
| `down_limit` | `Decimal \| None` | — | 跌停价 | ◐ | 无 |
| `limit_status` | `enum` | — | `NONE / UP_TOUCH / UP_LOCKED / DOWN_TOUCH / DOWN_LOCKED` | ✓ | 无 |
| `limit_locked` | `bool` | — | 收盘是否封板 | ✓ | 无 |
| `is_one_word_limit_up` | `bool` | — | 一字涨停 | ✓ | 无 |
| `is_one_word_limit_down` | `bool` | — | 一字跌停 | ✓ | 无 |
| `tradable_status` | `enum` | — | `TRADABLE / SUSPENDED / PRE_LISTING / DELISTING_PERIOD / HALTED` | ✓ | 无 |
| `tradability_score` | `float` | 0–100 | **可交易性**（与强弱正交） | ✓ | 无 |
| `bars_since_listing` | `int` | ≥0 | 上市以来交易日数（新股规则判定） | ◐ | 无 |

**Setup 不得用 `up_limit` / `down_limit` 自行推导涨跌停。** 它应该直接消费 `tradability_score` 与 `limit_status`。把价格字段暴露出来只是为了调试与审计。

---

## 11. Block `quality` / `provenance`

| 字段 | 类型 | 说明 |
|---|---|---|
| `quality_score` | `float` | 0–100，来自数据层（DATA_LAYER §7.3） |
| `quality_flags` | `tuple[str, ...]` | 如 `("LIMIT_UNKNOWN", "ATR_ZERO", "GAP_BAR")` |
| `provenance.snapshot_schema_version` | `str` | Snapshot 结构版本 |
| `provenance.feature_version` | `str` | 特征计算版本 |
| `provenance.swing_detector_id` | `str` | 含 `confirmation_lag` |
| `provenance.trend_model_id` | `str` | — |
| `provenance.config_hash` | `str` | `configs/*.yaml` 的内容哈希 |

**为什么必须带 provenance**：换一个 SwingDetector，同一个 (symbol, date) 会产出不同的 Snapshot。没有版本号，回测结果无法复现也无法归因——你永远说不清收益变化是策略变了还是指标实现变了。

**`quality_score` 低不阻止 Snapshot 生成。** 是否排除由 Filter 层决定，Core 不做这个判断。

---

## 12. 未来函数风险总表

| 风险点 | 位置 | 防护机制 |
|---|---|---|
| **Swing 确认延迟** | `last_swing_high/low` | `confirmation_lag` 强制 + 构造期断言 `swing.date + lag <= bar_date` |
| **全样本归一化** | 所有分数 | 一律 ATR 归一化；若用分位数必须限定滚动窗口 |
| **ATR 越界** | 所有 `*_atr` 字段 | ATR 只用 `<= T` |
| **用当日数据假设当日成交** | 全局 | `valid_for_date = T+1` + 执行引擎断言 |
| **除权日假跳空** | `gap_*` | Core 只用 `gap_qfq`；`is_ex_dividend_date` 仅作标记 |
| **停牌导致 duration 少算** | `*_duration_bars` | 一律用 `trading_day_index` 差 |
| **ST 状态前视** | `stock_status` | Provider 返回**时间段**，事件边界不做 off-by-one |
| **退市股缺失** | 全表 | 幸存者偏差，属**组合层**问题，V0.1 不处理（见 DESIGN_CONFLICTS §5） |

---

## 13. 与原方案的差异汇总（需裁决）

| # | 原方案 | 本草案 | 理由 |
|---|---|---|---|
| S1 | 无因果字段 | 新增 `as_of` / `valid_for_date` | 未来函数靠结构消灭 |
| S2 | 无 `provenance` | 新增 | 换 SwingDetector 后回测无法复现/归因 |
| S3 | 无 `quality` | 新增 | 脏数据会静默变成干净信号 |
| S4 | `t_plus_one_status` 在 Snapshot | **移出** | T+1 是持仓属性非市场属性：同日同股，能否卖出取决于你的入场日 |
| S5 | `range_probability` | 删除 | 未校准，称"概率"会误导下游 |
| S6 | `exhaustion_score` / `compression_score` | 推迟 V2 | 缺客观定义 |
| S7 | `last_swing_high` 为价格 | 改为 `SwingPoint`（带日期/strength/detector_id） | 无日期无法算 duration、无法判断是否被突破 |
| S8 | `pullback.strength` | 改名 `depth_score` | `strength` 语义二义（"回撤深" vs "趋势强"），两者交易含义相反 |
| S9 | `structure_direction` 与 `trend.direction` 并存 | 保留但明确尺度差异 | 短周期结构 vs 长周期趋势；若参数相同则确属冗余，需删一 |
| S10 | `breakout.direction` | 改 `bias`（连续） | 同 §15 连续优先 |
| S11 | `bull` / `bear` | 明确 `bull + bear ≠ 100` | 防止下游当份额用 |
| S12 | 一字板未定义 | 几何 `None` + 语义覆盖 + `tradability_score` 三轴分离 | 需求 §21–§24 的落地 |
| S13 | 无 | 新增 `bar_interpretation_source` | 审计：下游需知道某分数被制度覆盖过 |
