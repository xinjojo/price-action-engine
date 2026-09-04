# Price Action Engine (PA Engine)

基于 A 股日 K 数据的客观 Price Action 研究与回测平台。

**当前状态：V0.1 设计阶段。仓库内只有设计文档，没有实现代码。**

---

## 这个项目的核心主张

> **Core 描述市场，Setup 利用市场。**
>
> **Brooks 决定是否想交易，A 股 Execution 决定是否真的能成交。**

以 Al Brooks Price Action 为主要参考体系，但核心引擎**不绑定任何单一理论**。Core 输出一个统一的 Market State；Brooks、ICT、自定义模型都只是读取这份状态的上层插件。

依赖方向严格单向：

```
Data → Market Rules → Features → Structure → Market State → Snapshot → Setup → Entry/Stop/Exit → Execution → Portfolio → Backtest
```

---

## 四条不可妥协的约束

1. **无未来函数。** T 日收盘生成快照，只驱动 T+1 执行。任何滚动统计只能用截至 T 日的数据。
2. **原始 OHLC 永不伪造。** 一字涨停就是 `O=H=L=C`，不为了语义去编造虚拟价格。价格行为强弱与可交易性是两个独立维度。
3. **涨跌停规则不硬编码。** 走 `LimitRuleEngine`，数据优先，规则表按日期版本化，查不到就承认不知道。
4. **连续优先于布尔。** 输出 `Trend Strength = 72`，而不是 `Trend = True`。每个分数必须声明归一化基准。

---

## 文档

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 总体架构、依赖方向、Core/Setup/Market Rules/Execution 四条边界、分层强制机制、第一阶段目录树 |
| [docs/DATA_LAYER.md](docs/DATA_LAYER.md) | Provider 统一接口、Tushare 主源、a-stock-data 备用源评估、内部 Schema、三域复权设计、Cache、Validation 分级 |
| [docs/MARKET_STATE_SNAPSHOT.md](docs/MARKET_STATE_SNAPSHOT.md) | Snapshot 逐字段规范，含数学定义、输出范围、未来函数风险、Setup 使用权限 |
| [docs/A_SHARE_MARKET_RULES.md](docs/A_SHARE_MARKET_RULES.md) | T+1、LimitRuleEngine、涨跌停规则版本表、一字板、可交易状态、Trigger/Fill 分离协议 |
| [docs/INTERFACES.md](docs/INTERFACES.md) | MarketDataProvider / SwingDetector / TrendModel / Setup / EntryModel / StopModel / ExitModel / ExecutionEngine 等接口定义 |
| [docs/DESIGN_CONFLICTS.md](docs/DESIGN_CONFLICTS.md) | **已知矛盾、无法客观量化的字段、日 K 无法确知的信息、待您裁决的开放问题** |

建议阅读顺序：ARCHITECTURE → DESIGN_CONFLICTS → 其余。

---

## 配置与密钥

**仓库内不含任何密钥。** Tushare token 通过环境变量注入：

```bash
cp .env.example .env      # .env 已被 .gitignore 忽略
# 编辑 .env 填入 TUSHARE_TOKEN
```

代码里一律通过 `os.environ["TUSHARE_TOKEN"]` 读取。若你发现任何文件里出现了真实 token，请视为事故并立即轮换。

---

## 开发约定

见 [AGENTS.md](AGENTS.md)——包含 ponytail 规则集（[MIT](https://github.com/DietrichGebert/ponytail)）与本项目特有的分层、因果、复权、制度纪律。

---

## 免责声明

本项目是价格行为研究的工程实验，不构成任何投资建议。所有结论需自行验证。
