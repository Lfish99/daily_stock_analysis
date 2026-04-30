# 美股个股分析全流程说明（以 SNDK 为例）

本文回答 3 个核心问题：
1. 为什么美股筹码数据经常是空（被跳过）
2. `wave_theory` 等 skill 是否被用到、怎么用
3. “量能状态=缩量上涨，缩量上涨，上攻动能不足”是怎么计算出来的

---

## 1. 美股筹码数据为什么会被跳过

当前仓库里，筹码分布主要来自 A 股接口：
- AkShare 的 `stock_cyq_em`
- Tushare 的 `cyq_chips`

这两条在代码里都明确对美股返回 `None`：
- [data_provider/akshare_fetcher.py](data_provider/akshare_fetcher.py#L1464)
- [data_provider/tushare_fetcher.py](data_provider/tushare_fetcher.py#L1125)

统一入口在 [data_provider/base.py](data_provider/base.py#L1380) 的 `get_chip_distribution`：
- 会遍历所有 fetcher
- 只要都拿不到，就返回 `None`
- Pipeline 侧看到 `None` 就记录“筹码分布获取失败或已禁用”，继续分析（降级，不中断）
  - 参考 [src/core/pipeline.py](src/core/pipeline.py#L287)

### 美股可替代的数据思路（实务上）

严格意义上，美股没有 A 股那种官方“筹码分布”同构数据。通常用“代理指标”替代：
1. 成本分布估算（Volume Profile / Anchored VWAP）
2. 期权持仓与偏度（OI、PCR、隐波期限结构）
3. 空头数据（Short Interest、Days-to-Cover、暗池成交占比）
4. 机构持仓变化（13F）

也就是说：
- 你现在看到的“筹码被跳过”，是当前实现的预期行为，不是 bug。
- 如果要支持美股“筹码近似指标”，需要新增一套美股专用特征工程，而不是直接复用 A 股接口。

---

## 2. `wave_theory` 这些 skill 到底有没有被用到

### 2.1 技能从哪里来

技能是 YAML 文件，默认从 `strategies/` 加载：
- [src/agent/skills/base.py](src/agent/skills/base.py#L394)
- [src/agent/skills/base.py](src/agent/skills/base.py#L433)

`wave_theory` 对应文件就是：
- [strategies/wave_theory.yaml](strategies/wave_theory.yaml)

### 2.2 激活逻辑

技能激活在 `resolve_skill_prompt_state`：
- [src/agent/factory.py](src/agent/factory.py#L196)
- [src/agent/factory.py](src/agent/factory.py#L236)

结论：
- 配置 `AGENT_SKILLS` 会影响“技能指令文本”注入。
- `SkillManager.activate(...)` 会把选中的技能设为 enabled。

### 2.3 在你的这条主流程里怎么生效

普通 `main.py` 个股分析流程里，是否走 Agent 模式由这里决定：
- [src/core/pipeline.py](src/core/pipeline.py#L304)

关键点：
1. `AGENT_MODE=true` 才是显式 Agent 模式。
2. 若 `AGENT_SKILLS` 是具体列表（且不等于 `['all']`），会自动开启 Agent。
   - 见 [src/core/pipeline.py](src/core/pipeline.py#L308)
3. 如果 `AGENT_SKILLS=all`，这段“自动开启 Agent”的条件不会触发。

所以常见情况是：
- 你没开 `AGENT_MODE`，且 `AGENT_SKILLS=all`：大概率仍走传统分析链路。
- 但传统链路里的 `GeminiAnalyzer` 仍会把已激活技能的“文本规则”注入系统提示词：
  - [src/analyzer.py](src/analyzer.py#L847)
  - [src/analyzer.py](src/analyzer.py#L884)

这意味着 `wave_theory` 在传统链路中通常是“提示词规则约束”，不是“硬编码公式引擎”。

---

## 3. “缩量上涨，上攻动能不足”怎么得出的

这个结论来自趋势分析器 `StockTrendAnalyzer` 的量能规则，不是 LLM拍脑袋：
- [src/stock_analyzer.py](src/stock_analyzer.py#L413)
- [src/stock_analyzer.py](src/stock_analyzer.py#L428)

### 3.1 计算步骤

1. 计算 5 日均量：`vol_5d_avg = df['volume'].iloc[-6:-1].mean()`
2. 计算量比：`volume_ratio_5d = latest_volume / vol_5d_avg`
3. 计算当日涨跌：
   - `price_change = (latest_close - prev_close) / prev_close * 100`
4. 按阈值判断量能状态：
   - `VOLUME_SHRINK_RATIO = 0.7`
   - `VOLUME_HEAVY_RATIO = 1.5`
   - 常量定义见 [src/stock_analyzer.py](src/stock_analyzer.py#L185)

### 3.2 触发“缩量上涨”条件

当满足：
- `volume_ratio_5d <= 0.7`
- 且 `price_change > 0`

会得到：
- `volume_status = 缩量上涨`
- `volume_trend = 缩量上涨，上攻动能不足`

对应代码：
- [src/stock_analyzer.py](src/stock_analyzer.py#L438)
- [src/stock_analyzer.py](src/stock_analyzer.py#L439)

---

## 4. 美股个股分析完整流程（端到端）

以下是 `main.py --stocks SNDK ...` 的主链路。

### 第 0 步：入口参数

入口在 [main.py](main.py#L204)：
- `--stocks` 指定个股
- `--dry-run` 只跑数据与上下文，不跑个股 LLM 分析
- `--no-market-review` 跳过大盘复盘

### 第 1 步：获取/复用历史日线

Pipeline 先检查数据库是否可复用最新交易日数据：
- [src/core/pipeline.py](src/core/pipeline.py#L180)

若需拉取，会调用 `DataFetcherManager.get_daily_data`，多源自动切换。
美股常见会走 `YfinanceFetcher`（或 Longbridge 兜底）。

### 第 2 步：实时行情

调用：
- [src/core/pipeline.py](src/core/pipeline.py#L248)

写入上下文字段（例如 `price`、`volume_ratio`、`turnover_rate`）：
- [src/core/pipeline.py](src/core/pipeline.py#L551)

### 第 3 步：筹码分布

调用：
- [src/core/pipeline.py](src/core/pipeline.py#L287)

对美股通常返回 `None`，流程继续。

### 第 4 步：趋势分析（MA/量能/MACD/RSI）

调用 `StockTrendAnalyzer.analyze(...)`：
- [src/core/pipeline.py](src/core/pipeline.py#L351)
- [src/stock_analyzer.py](src/stock_analyzer.py#L210)

核心计算：
1. MA5/MA10/MA20/MA60：
   - [src/stock_analyzer.py](src/stock_analyzer.py#L259)
2. 趋势状态与强度：
   - [src/stock_analyzer.py](src/stock_analyzer.py#L332)
3. 乖离率：
   - [src/stock_analyzer.py](src/stock_analyzer.py#L391)
4. 量能状态：
   - [src/stock_analyzer.py](src/stock_analyzer.py#L413)
5. MACD/RSI：
   - [src/stock_analyzer.py](src/stock_analyzer.py#L269)
   - [src/stock_analyzer.py](src/stock_analyzer.py#L293)

### 第 5 步：新闻与情报搜索

调用多维搜索：
- [src/core/pipeline.py](src/core/pipeline.py#L387)
- [src/search_service.py](src/search_service.py#L2960)

美股默认维度一般包括：
- latest_news
- market_analysis
- risk_check
- earnings
- industry

搜索引擎按可用 provider 轮流尝试并过滤时效、语言：
- [src/search_service.py](src/search_service.py#L2720)

### 第 6 步：美股社交情绪（可选）

如果配置了 `SOCIAL_SENTIMENT_API_KEY`，会拼接 Reddit/X/Polymarket 情绪：
- [src/core/pipeline.py](src/core/pipeline.py#L418)
- [src/services/social_sentiment_service.py](src/services/social_sentiment_service.py#L55)

### 第 7 步：组装增强上下文

统一写入：
- realtime
- chip（若有）
- trend_analysis
- fundamental_context

代码：
- [src/core/pipeline.py](src/core/pipeline.py#L535)

### 第 8 步：构建 LLM Prompt 并调用

#### 8.1 系统提示词

`SYSTEM_PROMPT` +（默认策略/激活技能指令）注入：
- [src/analyzer.py](src/analyzer.py#L636)
- [src/analyzer.py](src/analyzer.py#L884)

#### 8.2 用户侧结构化输入

`_format_prompt` 会把技术面、实时行情、筹码、趋势、新闻等拼成仪表盘请求：
- [src/analyzer.py](src/analyzer.py#L1460)

### 第 9 步：结果后处理与落库

分析成功才会保存分析历史：
- [src/core/pipeline.py](src/core/pipeline.py#L486)

非 dry-run 且有结果时会保存本地报告：
- [src/core/pipeline.py](src/core/pipeline.py#L1435)

---

## 5. 你当前现象的解释（结合前面问题）

1. 美股筹码为空：当前实现就是跳过（预期行为）。
2. `wave_theory` 可能“参与了提示词”，但不一定在 Agent 工具链里执行。
3. “缩量上涨，上攻动能不足”来自明确阈值规则，不是 LLM自由发挥。

如果你愿意，我下一步可以在这个文档后面加一节“如何把美股筹码代理指标接入现有上下文（字段设计 + 最小改造点）”。
