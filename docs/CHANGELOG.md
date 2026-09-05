# CHANGELOG — Price Action Engine

记录所有面向决策的改动。仅文件级 + 设计级变更入历史；逐行修复不入。

---

## V0.1.1 — 2026-09-04 收尾

### 修正

- **A 股制度层**：V0.1 把主板 ST 涨跌幅变更误判为"截至 2026-09-04 未确认"。经查 SSE 官方公告
  （《上海证券交易所交易规则（2026年修订）》，2026-04-24 发布），**沪深北三所同步于 2026-07-06 起
  正式实施**，主板风险警示股涨跌幅由 5% 调整为 10%。本轮：
  - 修正 `A_SHARE_MARKET_RULES.md` 章节 2.1 与 9.1，实证 `000010.SZ` 2026-06-30 vs 2026-07-06
    的真实涨跌停价（5.29% → 10.16%）。
  - 修正 V0.1 哨兵值错误：Tushare `stk_limit` 用 `up_limit=999999.999 / down_limit=0.01`
    标识"无涨跌幅限制"，**不是 V0.1 文档里写的 `-1`**（第三方数据表的误传）。
  - V0.1 文档明确标注 ⚠️ "V0.1 阶段未确认"的位置，加修正说明与实测依据。

### V0.1.1 裁决（来自用户指令）

| # | 主题 | 裁决 | 落位 |
|---|---|---|---|
| 1 | C1 Setup 读 Snapshot 序列 | 引入 `SnapshotWindow`（`Sequence[MarketStateSnapshot]`） | `INTERFACES.md` |
| 2 | C18 变量验证 | 四层（数学、分布、单变量、Conditional） | `DESIGN_CONFLICTS.md` |
| 3 | C14 同 bar 模糊 | 拆分：T+1 lock vs worst-case conservative | `A_SHARE_MARKET_RULES.md §6.1` |
| 4 | ATR 不跳过 TR=0 | 保持标准 Wilder ATR | `features/volatility.py` + `test_volatility.py` |
| 5 | 删除 tradability_score | 客观事实化 + Execution 方向判定 | `schema.py` + 文档 |
| 6 | 一字板语义 | 保留 bar_bias ±100 覆盖；几何字段 None | `features/bar.py` |
| 7 | Gap 独立特征 | 保持；qfq 主 + raw 记录 | `features/gap.py` |
| 8 | 主板 ST 5%→10% | 写明 2026-07-06 实施 | 文档 §2.1 |
| 9 | 规则资料优先级 | SSE/SZSE/BSE > CSRC > Tushare > 券商 > 百科 | 文档 §9 |
| 10 | LimitMode 显式三态 | `LIMITED / UNLIMITED / UNKNOWN` | `schema.py` |
| 11 | 哨兵仅 provider 内部 | 集中识别 + 回归测试 | `tushare_provider.py` |
| 12 | PriceBridge 简单化 | 仅换算函数 + 跨源防护 | `data/bridge.py` |
| 13 | trend.confidence 改 stability | 暂时不实现，避免假分数 | `MARKET_STATE_SNAPSHOT.md` |
| 14 | exhaustion/compression 不实现 | V0.1.1 占位移除 | — |
| 15 | climax 不复杂化 | V0.1.1 不实现 | — |
| 16 | Snapshot 最小 Block | 共识 9 块 | `MARKET_STATE_SNAPSHOT.md` |
| 17 | valid_for_date 移走 | Signal/OrderSpec 自带 | `INTERFACES.md §` |
| 18 | Order 默认 1 bar 有效 | `valid_bars=1` | `A_SHARE_MARKET_RULES.md §7.6` |
| 19 | Trigger ≠ Fill 保持 | 不变 | 既有 + 文档加固 |
| 20 | 第一阶段仅实现 | Data + Market Rules + Primitive + Limit Detection | — |
| 21 | Swing / Trend / Setup 不实现 | V0.2 起步 | 路线图 |
| 22 | 测试覆盖 | 11 类 fixture 全部通过 | `tests/` |
| 23 | 完成后停止 | ✅ | — |
| 24 | 报告格式 | 本文档 | — |

### 本轮新增功能（V0.1 → V0.1.1）

#### 1. Provider 契约回归守卫

新增 `tests/test_tushare_adapter.py::TestSentinelContractGuard`，四项守卫：

- 当前哨兵值文档断言（fixtures == 999999.999 / 0.01）。
- 哨兵被识别为 UNLIMITED；Core 视图里 up_limit/down_limit 均为 None。
- 极端正常价（如 up=1000000 配合 down=10000）不误判。
- **-1 永不静默归类**：必须上抛 ValueError。

> 未来 Tushare 改了哨兵编码，测试会立即失败——不会静默把极端值当正常涨跌停价。

#### 2. MarketStateSnapshot 收敛到 9 个 Block

已删除：tradability_score / exhaustion_score / compression_score / confidence 等占位字段。
保留：identity / bar / structure / trend / range / pressure / breakout / market_constraint / provenance。

#### 3. Signal / OrderSpec 与 Snapshot 解耦

`valid_for_date` 从 Snapshot 移到 Signal/OrderSpec；Snapshot 现在是 `bar_date = T, as_of = T_CLOSE` 的客观事实，
不绑定 T+1 执行。

### 已知风险

- **`TushareProvider.get_daily_price` 仍未实现**，因为需要 `board / stock_status`（依赖 V0.2 的 stock_basic + namechange）。
- **a-stock-data 仍未编码**——作为交叉校验源（仅 ST/停牌/复权因子），V0.2 才接。
- **LimitRuleEngine 仅接受 PROVIDER 来源**，尚无规则表推导。需要 V0.2 实现 `configs/limit_rules.yaml`。

---

## V0.1 — 2026-09-04 设计文档

完整 6 份设计文档已推送至 `https://github.com/xinjojo/price-action-engine`。
涵盖了 ARCHITECTURE / DATA_LAYER / MARKET_STATE_SNAPSHOT / A_SHARE_MARKET_RULES / INTERFACES / DESIGN_CONFLICTS。

V0.1 阶段仅设计，不实现。
