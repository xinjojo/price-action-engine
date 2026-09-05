# MEMORY.md — Price Action Engine 项目长期约定

## 项目定位

基于 A 股日 K 的客观 Price Action 研究平台。参考 Al Brooks，但核心引擎不绑定任何单一理论。

> **Core 描述市场，Setup 利用市场。**
> **Brooks 决定是否想交易，A 股 Execution 决定是否真的能成交。**

## 用户偏好 / 工作方式

- **架构正确 > 代码数量 > 快速实现。** 允许提前设计接口与 Schema，禁止提前实现未验证的策略。
- 明确要求「第一轮不要编码」——交付设计后立即停止，等审核。
- 要求主动指出设计矛盾与无法客观量化的字段，不要偷偷做假设。
- 产出需推送 GitHub 且**必须脱敏**：任何 token 都不得进入版本库。
- 决策风格：会明确选择推荐项，接受基于调研的方案修正。

## 硬约束（违反即错）

1. **依赖只能向下**：Data → Market Rules → Features → Structure → State → Snapshot → Setup → Entry/Stop/Exit → Execution。
2. **Core 禁现 Level C 词汇**：H1/H2/L1/L2/FVG/MSS/OTE/ICT/Wedge/MTR/buy/sell/signal。
3. **Setup 只能读 Snapshot**，禁止重算 Core 指标、禁止接触原始 K 线。
4. **无未来函数**：T 日收盘生成 Snapshot，只驱动 T+1 执行；滚动统计只能用 `<= T` 的数据。
5. **Raw OHLC 永不伪造**。一字板就是 O=H=L=C，不编造虚拟价格。
6. **涨跌停不硬编码 10%**，走 LimitRuleEngine，数据优先，推不出就承认不知道。
7. **T+1 的执行在 Execution 层**，不在 Setup。
8. **订单默认只存活 1 根 bar**。
9. **禁止跨源混用 adj_factor**（Tushare 官方声明各源口径不同）。
10. **只告警不自动修正数据**。

## 数据源

- **主源 Tushare**（用户有付费套餐）。token 走 `TUSHARE_TOKEN` 环境变量。
- **a-stock-data 是交叉校验源，不是日 K 备用源**（它是单文件 SKILL.md，不可 import；缺交易日历与历史涨跌停价）。摘取代价高，仅在确实需要校验 ST/停牌/复权因子时才做。

## 已知待办

- V0.2 前必须裁决：DESIGN_CONFLICTS.md 的 C1（Setup 读 Snapshot 序列）、C18（先做变量预测力检验）、C14（同 bar 先止损）。
- V0.2：TushareProvider + probe 脚本探明 stk_limit 起始年份 / namechange 枚举 / suspend_timing 语义 / 积分上限。

## 仓库

- 远端：`https://github.com/xinjojo/price-action-engine`（公开）
- 本地：`/Users/mouha/WorkBuddy/价格行为交易方法探索`
- 推送前必检：`grep -rEn "[0-9a-f]{40}"` + 确认 `.env` 未被跟踪
