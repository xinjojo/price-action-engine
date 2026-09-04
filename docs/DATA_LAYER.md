# DATA_LAYER.md — 数据层设计 V0.1

> 状态：**草案，待审核**。
> 本文件所有第三方接口名、字段名均为 2026-09-04 查证结果。接入前需复验。

---

## 0. 设计立场

数据层要解决的不是"怎么把数据拉下来"，而是三件事：

1. **上层永远不知道数据从哪来**（Provider 抽象）
2. **上层永远不需要操心复权**（三域 + `PriceBridge`）
3. **脏数据不会静默变成干净信号**（分级校验 + 质量标记，只告警不修正）

---

## 1. Provider 统一接口

上层禁止出现 `tushare.*` / `mootdx.*` 或任何第三方函数名。一律经此 Protocol。

```python
class MarketDataProvider(Protocol):
    name: str                    # "tushare" | "astock_data" | ...
    schema_version: str          # 本 provider 输出的内部 Schema 版本

    def get_daily_price(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[DailyBar]:
        """一律返回【不复权】原始 OHLCV + 当日 adj_factor。
        复权价不在此返回，由 PriceBridge 在读取时派生。"""

    def get_adjust_factor(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[AdjustFactor]: ...

    def get_trade_calendar(self, start: date, end: date) -> list[TradeDay]: ...

    def get_stock_list(self, as_of: date) -> list[StockInfo]:
        """含 list_date / delist_date / market / board。"""

    def get_stock_status(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[StockStatus]:
        """ST / *ST / 停牌 / 退市整理 / 新股 / 退市 —— 全部带起止日期的【时间段】。"""

    def get_board_history(self, symbol: str) -> list[BoardPeriod]:
        """板块归属时间段序列，不是单点。涨跌停规则按日期版本化，必须用时间段。"""

    def get_daily_limit(
        self, symbols: list[str], start: date, end: date
    ) -> Iterator[DailyLimit]:
        """交易所口径的每日涨跌停价。查不到返回 up_limit=None, source=UNKNOWN。"""
```

### 1.1 关键设计点

**`get_stock_status` 返回时间段，不是当日快照。**
ST 戴帽/摘帽、停牌/复牌都是有起止日的事件。返回单点会在事件边界处产生 off-by-one，是典型的未来函数/前视偏差来源（"这只票今天已经不是 ST 了"——但当天早上你还不知道）。

**`get_board_history` 返回 `BoardPeriod(effective_from, effective_to, board)`。**
原因见 §4：涨跌停幅度按板块 + 日期变化，单个当前板块值不够用。

**`get_daily_price` 只返回 raw。**
理由见 §3.3——前复权是"以最新为基准"的，缓存 qfq 等于缓存一个会过期的结论。

---

## 2. Primary Provider：Tushare

### 2.1 接口清单

| 用途 | 接口 | 关键字段 | 备注 |
|---|---|---|---|
| 日 K（不复权） | `daily` | `ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount` | `pre_close` 已是**除权后**前收，可直接用于涨跌停计算 |
| 复权因子 | `adj_factor` | `ts_code, trade_date, adj_factor` | Tushare 自主计算 |
| **每日涨跌停价** | `stk_limit` | `trade_date, ts_code, pre_close, up_limit, down_limit` | ⚠️ `pre_close` 默认不返回，需显式 `fields=` |
| 交易日历 | `trade_cal` | `exchange, cal_date, is_open, pretrade_date` | |
| 股票列表 | `stock_basic` | `ts_code, name, market, list_date, delist_date, list_status` | |
| **ST 状态时间序列** | `namechange` | `ts_code, name, start_date, end_date, change_reason` | ST 判定的主来源 |
| 停复牌 | `suspend_d` | `ts_code, trade_date, suspend_timing, suspend_type` | `S`=停牌 / `R`=复牌；覆盖停牌到复牌的**连续日期** |

### 2.2 必须注意的坑

**① `stk_limit` 用 `-1` 表示"当日无涨跌幅限制"。**
实测证据：2020-08-24 创业板注册制首日，`N康泰` 等新股的 `up_limit` / `down_limit` 均为 `-1.00`，而当日涨幅达 1061%。

这是一个**哨兵值**，不是价格。代码里必须：

```python
if up_limit == -1 or down_limit == -1:
    has_price_limit = False   # 绝不是 "涨停价是 -1 元"
```

忽略这一点会让所有新股前 5 日、退市整理期首日被判成"跌停"，产生大量假信号。

**② `adj_factor` 不能跨源混用。**
Tushare 官方说明原文：*"由于复权因子在计算的过程中各个数据源对现金分红、送股、配股、税务等不同事件采用了不同的处理逻辑，故而不同数据源的复权因子也会有差异。"*

推论：**Tushare 的 `adj_factor` 只能配 Tushare 的 `daily`，禁止配新浪/通达信的价格。** 这是一条硬约束，由 `PriceBridge` 记录 factor 来源并在跨源时抛错。

**③ 限流与配额。**
`stk_limit` 单次最多 7800 行（部分文档写 5800，以实际返回为准），2000 积分档 200 次/分钟。全市场历史涨跌停价需按 `trade_date` 循环拉取，**必须走缓存**。

**④ 数据更新时点。**
`stk_limit` 每交易日 08:40 左右更新当日涨跌停价。这意味着盘中/盘前取当日数据是合理的，但回测中取"当日"要小心：回测只用历史日期，不受影响。

### 2.3 尚待实测确认的点（不要假设）

- `stk_limit` 的历史起始年份（不同板块可能不同）
- `namechange` 的 `change_reason` 枚举值全集，以及 ST 相关取值是否稳定
- `suspend_d` 对"盘中临时停牌"与"全天停牌"的区分（`suspend_timing` 有值 = 日内停牌，但这对日 K 回测意味着当日**有成交**，不应记为停牌日）
- 付费套餐对各接口的积分上限

> 这四项在 V0.2 接入时用一个 `scripts/probe_tushare.py` 一次性探明并记录进本文件。**不要凭记忆写进代码。**

---

## 3. Secondary Provider：a-stock-data

### 3.1 形态调研结论（**重要，与原假设不符**）

**a-stock-data 不是一个可 `pip install` 的 Python 库。**

| 项 | 实测结果 |
|---|---|
| 仓库内容 | 仅 `SKILL.md`（**208 KB**）+ `README.md` + `CHANGELOG.md` + LICENSE |
| 形态 | Markdown 文档 + 内嵌 Python 代码片段，**无 `setup.py` / `pyproject.toml` / `__init__.py`** |
| 无 import 入口 | 不能 `import a_stock_data`。代码是给 AI Agent 读的，不是给程序调的 |
| 作者立场 | README 明确："单文件自包含是本项目的**有意产品决策**……这个形态会长期保持，不做目录化拆分（#21 / #22 / #29）" |

**这直接决定了接入方式**：不存在"安装包 → 调用 API"这条路。只有一条路——

> **人工从 `SKILL.md` 中摘取所需函数源码，复制进本仓库 `providers/astock_data/`，自行维护，并遵守 Apache-2.0 的署名要求。**

代价：我们承担这些代码的维护责任，且无法自动跟随上游升级。收益：零鉴权、不封 IP 的多个数据源。

### 3.2 版本与活跃度（接入档案）

| 项 | 值 |
|---|---|
| 仓库 | `simonlin1212/a-stock-data` |
| License | Apache-2.0（摘取代码需注明出处） |
| Stars / Open Issues | 9,539 / 1 |
| 当前版本 | **v3.7.2** |
| HEAD commit | `3a599d09`（2026-09-02） |
| 提交频率 | 高（近 30 天 3 次，含 bugfix） |
| 架构自述 | 11 层 · 54 端点 · 19 数据源 · 零鉴权 |

**上游近期修复记录（说明质量态度与风险面）**

| 版本 | 修了什么 | 对我们的启示 |
|---|---|---|
| v3.7.2 | `_natural_market()` 与 `get_prefix()` 北交所号段判定不一致 | 同一份文件里存在两套号段规则 |
| v3.7.1 | `get_prefix()` 不认 `.SH` 后缀，`000016.SH` 被**静默路由成深康佳A**（错票） | **静默返回另一只标的的数据，比返空危险得多** |
| v3.6.0 | 北交所 920 迁移；腾讯对废码返回"僵尸报价"（成交量 0，价差 17%~100%+） | 老码不报错，只返回错的 |
| v3.2.x | 东财系封 IP；mootdx BESTIP 空串崩溃 | 依赖库已烂尾 |

### 3.3 与我们需求相关的端点

| 需求 | 端点 | 能力 | 局限 |
|---|---|---|---|
| 日 K | §1.1 `tdx_client().bars(symbol, frequency=9)` | 通达信 TCP，不封 IP | **不复权**；mootdx 库烂尾（末次 commit 2024-07），需绕开 BESTIP bug；`httpx<0.26` 依赖冲突 |
| 日 K（备选） | §1.3 `baidu_kline_with_ma()` | 带 MA5/10/20 | 第三方聚合，口径需校验 |
| **复权因子** | §1.4 `sina_adjust_factor(code, kind)` + `apply_adjust()` | 一次 HTTP 约 1.8 KB，零鉴权 | ⚠️ **qfq 是除数、hfq 是乘数**，方向搞反不报错只出错数 |
| ST / 停牌历史 | §6.5 `baostock_valuation_history()` | 日频 `isST` + `tradestatus` + 换手率，可回溯至 2016 | **不支持北交所**（服务端 `10004011`）；baostock 为 TCP 客户端 |
| 上市/退市日 | §6.6 `baostock_stock_basic()` | 唯一零鉴权退市日源 | 同上，不支持北交所 |
| 行业前视偏差 | §6.7 申万行业变迁史 | 12,893 行 / 5,905 只 / 38 个一级行业 | 仅代码无中文名 |
| 当日涨跌停价 | §1.2 `tencent_quote()` | 字段 47/48 = 涨跌停价 | **仅当日快照，无历史** |
| 涨停板池 | §8.x 东财/同花顺 | 封板资金、炸板、连板 | 东财系，封 IP 风险 |

### 3.4 关键缺口

- ❌ **没有交易日历端点。** a-stock-data 无法提供 `trade_cal`，只能从 K 线序列隐式推断。
- ❌ **没有历史涨跌停价。** 只有腾讯的当日快照。历史涨跌停只能靠规则推导。

这两项恰恰是 `Market Rules` 层的地基。**结论：a-stock-data 不能作为主源，甚至不能作为日 K 的完整备用源。**

### 3.5 定位修正（建议）

原方案把它定位为"备用免费数据聚合框架"。基于调研，建议改为：

> **a-stock-data = 交叉校验源 + Tushare 薄弱项的补充，不是日 K 的备用源。**

具体用途，按价值排序：

1. **复权因子交叉校验**（§1.4 sina vs Tushare `adj_factor`）。Tushare 自己都声明不同源因子有差异，这是最需要校验的一项。
2. **ST / 停牌标记交叉校验**（§6.5 baostock vs Tushare `namechange` + `suspend_d`）。ST 起止日错了会直接污染涨跌停判定，是最贵的一类错误。
3. **退市日补充**（§6.6）。退市股在 Tushare 可能取不到完整历史，且退市股是回测幸存者偏差的主要来源。
4. **行业分类前视偏差消除**（§6.7）。V2 做行业中性化时需要。

日 K 本身：**不接**。Tushare 的日 K 质量与稳定性都更好，为它引入 mootdx 的 httpx 冲突和烂尾风险不划算。

### 3.6 接入档案（V0.2 接入时填写，现在留空）

```
接入日期：
上游 commit / hash：
上游版本：
摘取的章节与函数：
  - §___ ____________  → price_action_engine/data/providers/astock_data/______.py
自维护声明：是（Apache-2.0，摘取代码时需按许可要求署名，届时补 NOTICE 文件）
已知风险：
```

### 3.7 升级流程（不得自动追新）

升级前必须完成 **CHANGELOG REVIEW** 并书面回答五问：

1. 更新了什么？
2. API 是否变化？（摘取的函数签名/字段名）
3. 数据源是否变化？（源被替换或下线）
4. 是否影响当前已摘取的代码？
5. 是否值得升级？

**升级触发条件**：只有当 Tushare 侧出现缺口且该缺口已被 a-stock-data 覆盖时才动手。不得因为"上游发版了"就升级。

---

## 4. 内部统一 Schema

### 4.1 `DailyBar`

```python
@dataclass(frozen=True)
class DailyBar:
    # ---- 身份 ----
    symbol: str                      # "000001.SZ"，内部统一后缀式
    trade_date: date
    trading_day_index: int           # 交易日序号（非自然日），duration 计算的唯一依据

    # ---- 原始未复权（唯一真实成交口径）----
    raw_open:  Decimal
    raw_high:  Decimal
    raw_low:   Decimal
    raw_close: Decimal
    raw_pre_close: Decimal           # 除权后前收

    # ---- 后复权（收益计算用）----
    hfq_open / hfq_high / hfq_low / hfq_close: Decimal

    # ---- 成交量额（事实，永不复权）----
    volume: float                    # 股
    amount: float                    # 元

    # ---- 复权 ----
    adj_factor: float
    adj_factor_source: str           # "tushare" —— 禁止跨源混用

    # ---- 制度与质量 ----
    board: str
    stock_status: StockStatus
    is_suspended: bool
    is_ex_dividend_date: bool        # adj_factor[t] != adj_factor[t-1]
    quality_score: float             # 0–100
    quality_flags: tuple[str, ...]

    source: str                      # "tushare"
    schema_version: str
```

**注意：没有 `qfq_*` 字段。**

qfq 不存储，由 `PriceBridge` 在读取时派生。理由见 §3.3 与 §6.1。

### 4.2 `DailyLimit`

```python
@dataclass(frozen=True)
class DailyLimit:
    symbol: str
    trade_date: date
    has_price_limit: bool            # False = 当日无涨跌幅限制
    up_limit: Decimal | None         # None = 未知；-1（哨兵）已在此层被转换掉
    down_limit: Decimal | None
    rule_id: str                     # 规则追溯，如 "SZ_GEM_20200824_20PCT"
    source: LimitSource              # PROVIDER | DERIVED | UNKNOWN
    confidence: float                # 0–100
```

### 4.3 为什么价格用 `Decimal`

A 股涨跌停价是交易所按**四舍五入到分**计算的。用 float：

```python
>>> 10.0 * 1.1
11.000000000000002
>>> round(3.145 * 1.1, 2)   # 银行家舍入，与交易所"四舍五入"规则不同
3.46                        # 交易所口径可能是 3.46，但边界情形会分叉
```

后果：涨停价差 1 分 ⇒ 收盘价"触及涨停"判定翻转 ⇒ 一字板判定翻转 ⇒ 可交易性判定翻转。这是一条会一路放大到 Snapshot 的误差链。

规则：

- **价格字段（OHLC、limit、trigger、fill）一律 `Decimal`。**
- **指标计算管线内部转 `float`**（numpy 友好），但所有与"价格比较、成交判定"有关的比较必须在 `Decimal` 域完成。
- 最小价格单位 `tick = Decimal("0.01")` 作为常量，不散落各处。

### 4.4 duration 必须用 `trading_day_index`

**不能用列表索引差。** 停牌日不产生 bar，但它是交易日。用列表索引算 `leg_duration`，一次停牌就会让所有 duration 少算。

```
trading_day_index = trade_calendar.index_of(trade_date)
duration = idx[t2] - idx[t1]          # 交易日数，正确
```

---

## 5. 复权三域与 `PriceBridge`

### 5.1 三域分工

| 域 | 用途 |
|---|---|
| `raw` | 成交判定、PnL、止损价、涨跌停判定、真实跳空 |
| `qfq` | Core 全部计算：结构、形态、指标、Price Action 语义的 Gap |
| `hfq` | 收益率、复权净值 |

### 5.2 Gap 双算

```
is_ex_dividend_date = adj_factor[t] != adj_factor[t-1]
gap_qfq = qfq_open[t] - qfq_close[t-1]        # Price Action 语义（主）
gap_raw = raw_open[t] - raw_close[t-1]        # 事实跳空（含除权）
```

除权日在 raw 上制造向下假跳空，在 qfq 上不出现。真实隔夜跳空两者都出现。**Core 只用 `gap_qfq`。**

### 5.3 `PriceBridge`：唯一换算入口

```python
class PriceBridge:
    """raw ↔ qfq ↔ hfq 的唯一换算入口。禁止任何其他地方自行乘除 adj_factor。"""
    def to_qfq(self, raw_price, date, symbol) -> Decimal
    def to_raw(self, qfq_price, date, symbol) -> Decimal
    def to_hfq(self, raw_price, date, symbol) -> Decimal
```

换算式（qfq 定义为以最新为基准）：

```
qfq(t) = raw(t) × adj_factor(t) / adj_factor(latest)
raw(t) = qfq(t) × adj_factor(latest) / adj_factor(t)
hfq(t) = raw(t) × adj_factor(t)
```

**为什么值得单独一个模块**：Setup 在 qfq 域算出触发价 12.34，Execution 要拿它与 T+1 的 raw K 线比。不换算，茅台这类 `adj_factor` 上百的标的会差几个数量级——**不报错，只是回测曲线一直很漂亮**。

**强制手段**：`test_layering.py` 扫描全仓库，除 `bridge.py` 外任何文件出现 `adj_factor` 参与 `*` 或 `/` 运算即失败。

---

## 6. 缓存

### 6.1 缓存 raw + factor，不缓存 qfq

**前复权以"最新"为基准。每次除权，全部历史 qfq 都会变。**

推论：**缓存 qfq = 缓存一个会过期的结论。** 第一次除权后它就错了，而且错得很安静。

所以缓存里只有不变的东西：

```
raw OHLCV（事实，永不变）
adj_factor（事实，永不变）
→ qfq / hfq 在读取时由 PriceBridge 派生
```

### 6.2 结构

```
data/cache/
├── manifest.json                        # {dataset, provider, params_hash, schema_version, fetched_at}
├── daily/000001.SZ/2024.parquet         # 按 (symbol, year) 分区
├── adj_factor/000001.SZ.parquet
├── stk_limit/2024.parquet               # 按年，全市场
├── trade_calendar.parquet
└── stock_status.parquet
```

- 格式：**parquet**（列式、压缩、schema 自带）。
- **不可变**：写新文件不覆盖旧文件；`manifest.json` 记录 `fetched_at` 与 `schema_version`。
- **缓存键包含 `schema_version`**：Schema 升级后旧缓存自动失效，不会静默混用。
- 命中判定：`(provider, dataset, symbol, date_range ⊇ 请求范围, schema_version 相同)`。

### 6.3 明确不做

- ❌ Redis、数据库、分布式缓存
- ❌ 增量更新（V0.1 全量重取，数据量可接受）
- ❌ 缓存自动过期（研究需要数据版本稳定，宁可手动清）

---

## 7. Validation

**核心原则：只告警，不自动修正。**

自动修正是研究平台上最危险的功能——它让"数据有问题"这个信号消失，然后把错误固化成结论。

### 7.1 分级

| 级 | 检查项 | 失败处理 |
|---|---|---|
| **L0 结构** | 类型、必填字段、非空 | `ERROR` — 拒绝入库 |
| **L1 单 bar 内部一致性** | `high >= max(open, close)`；`low <= min(open, close)`；`high >= low`；`volume >= 0`；`amount >= 0`；`open/close ∈ [low, high]` | `ERROR` — 该 bar 标记不可用，禁止进入 Core |
| **L2 涨跌停一致性** | `high <= up_limit + tick`；`low >= down_limit - tick`（当 `has_price_limit=True`）；`raw_pre_close` 与上一交易日 `raw_close` 在**非除权日**应一致 | `ERROR` / `WARN` |
| **L3 序列** | 日期严格递增；无重复日期；无缺失交易日（对照日历）；停牌日已用 gap bar 补齐 | `WARN` |
| **L4 复权自洽** | `adj_factor > 0`；`is_ex_dividend_date` 与 factor 跳变一致；qfq 派生日连续性（除权日不应出现跳空） | `WARN` |
| **L5 跨源** | Tushare vs 校验源抽样比对 | `WARN`（记为 `DataQualityWarning`） |

### 7.2 异常值检测（只标记，不修正）

| 检查 | 阈值 | 说明 |
|---|---|---|
| 极端收益 | `\|pct_chg\| > 30%` 且 `has_price_limit=False` 且 `is_ex_dividend_date=False` | 新股/退市整理首日除外 |
| 成交量异常 | `volume == 0` | 可能是停牌或僵尸报价 |
| 流动性异常 | `amount < 阈值` | 可能是即将退市或长期停牌 |
| 僵尸报价 | `volume == 0 且 close == pre_close` | a-stock-data 明确警告过的模式 |
| 复权因子异常 | 单日 factor 变动 `> 50%` | 可能是数据错误或特殊事件 |

### 7.3 `quality_score`

```
quality_score = 100
              - 30 × has_error_level_1_2
              - 10 × 每条 WARN（封顶 -50）
              - 20 × (source_confidence < 50)
```

- `quality_score < 50` → Snapshot 照常生成，但 `quality_flags` 含 `LOW_QUALITY`，**由 Filter 层决定是否排除**。
- Core **不因质量分低而拒绝计算**。过滤是上层的决定，不是数据层的。

---

## 8. 跨源交叉验证

### 8.1 做法

按 §3.5 的价值排序，做**抽样**比对（不比对全量，成本不划算）：

| 项目 | 主源 | 校验源 | 频率 |
|---|---|---|---|
| 复权因子 | Tushare `adj_factor` | sina `sina_adjust_factor` | 抽样 100 只 × 每年 1 次 |
| ST 起止日 | Tushare `namechange` | baostock `isST` | ST 股全量 |
| 停牌日 | Tushare `suspend_d` | baostock `tradestatus` | 抽样 |
| 日 K OHLC | Tushare `daily` | mootdx / 百度 K 线 | 抽样 50 只 × 近 1 年 |

### 8.2 铁律

> **不要因为免费源和 Tushare 某处不同就自动覆盖主数据。**

不一致时输出 `DataQualityWarning`，包含：symbol、date、字段、双方取值、差异幅度。**由人决定谁对。**

理由：免费源聚合了 19 个数据源，稳定性与口径都弱于付费主源。自动覆盖会让主源的质量被免费源稀释——这是"用更差的源去修更好的源"。

---

## 9. V0.1 / V0.2 分工

| 阶段 | 内容 |
|---|---|
| **V0.1（现在）** | 本文档 + `MarketDataProvider` 接口定义。**不写取数代码。** |
| **V0.2** | 实现 `TushareProvider`；跑 `scripts/probe_tushare.py` 探明 §2.3 的四个未知项并回填本文档；实现 validators + cache + bridge；实现 `Market Rules` 层 |

---

## 10. 需您裁决的问题

| # | 问题 | 我的倾向 |
|---|---|---|
| D1 | a-stock-data 从"日 K 备用源"降级为"交叉校验源"，是否接受？ | 接受。它是单文件 MD，摘取代价高；且缺交易日历与历史涨跌停，做不了日 K 主备 |
| D2 | 是否接受"摘取代价"——把上游函数复制进仓库自维护（Apache-2.0 需署名）？ | 仅在确实需要 ST/停牌/复权因子校验时才做 |
| D3 | 价格用 `Decimal` 会牺牲一部分计算性能，是否接受？ | 接受。只在价格比较与成交判定路径用 Decimal，指标管线用 float |
| D4 | 缓存只存 raw + factor（qfq 每次读取时重算），是否接受？ | 接受。这是正确性问题，不是性能问题 |
