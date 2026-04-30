# 重要事件提醒方案选型指南

## 背景与需求

当你跑 `main.py --stocks GOOG` 时，系统会做新闻搜索，但目前搜到的是分析师评分，而不是"本周 GOOG 要发财报"这类**日历型事件提醒**。

本文回答：
1. 有哪些接口可以**直接拿到结构化事件数据**（不需要 LLM）？
2. 哪些场景必须用 **LLM 搜索 + 总结**？
3. 这个 repo 里**当前已有什么、缺什么**？

---

## 一、可以直接拿到结构化事件数据的接口

### 1. yfinance（已集成）

**无需新依赖，直接可用。**

#### 1.1 `ticker.calendar` — 下次财报预期

```python
import yfinance as yf
t = yf.Ticker('GOOG')
print(t.calendar)
```

实际返回（GOOG，2026-04-28 测试）：

```python
{
  'Earnings Date': [datetime.date(2026, 4, 30)],   # 下次财报日
  'Earnings High': 2.87,    # 分析师预期 EPS 上限
  'Earnings Low':  2.34,
  'Earnings Average': 2.63,
  'Revenue High': 110_440_000_000,
  'Revenue Low':  103_450_000_000,
  'Revenue Average': 107_033_437_750,
  'Dividend Date': datetime.date(2026, 3, 16),
  'Ex-Dividend Date': datetime.date(2026, 3, 9),
}
```

→ **直接拿到财报日期，不需要 LLM**。

#### 1.2 `ticker.earnings_dates` — 历史 + 未来财报日历

```python
print(t.earnings_dates.head(5))
```

```
                           EPS Estimate  Reported EPS  Surprise(%)
Earnings Date
2026-04-29 16:00:00-04:00          2.63           NaN          NaN   ← 未发布
2026-02-04 16:00:00-05:00          2.64          2.82         6.78   ← 历史
2025-10-29 16:00:00-04:00          2.26          2.87        26.88
```

可用于：判断"距离财报还有几天"、历史 beat/miss 记录。

#### 1.3 `ticker.news` — Yahoo Finance 关联新闻

```python
news = t.news  # 返回列表，每项是 {'id': ..., 'content': {...}}
for item in news[:5]:
    c = item['content']
    print(c['title'], c['pubDate'], c['canonicalUrl']['url'])
```

字段结构：`content.title` / `content.pubDate` / `content.summary` / `content.canonicalUrl.url`

注意：`ticker.info.get('title')` 等老字段在新版 yfinance (0.2.x) 已移入 `content` 嵌套字典。

---

### 2. 财经日历专用 API（免费/低成本）

| 接口 | 免费额度 | 数据内容 | 备注 |
|---|---|---|---|
| **Alpha Vantage** `EARNINGS_CALENDAR` | 25次/天（免费 key） | 未来 3 个月所有美股财报日 | `https://www.alphavantage.co/query?function=EARNINGS_CALENDAR` |
| **Financial Modeling Prep (FMP)** | 250次/天（免费 key） | 财报日历 + 经济日历（含美联储会议） | `/api/v3/earning_calendar` `/api/v3/economic_calendar` |
| **Nasdaq 官方日历** | 无需 key | 财报日历（HTML/CSV） | `https://www.nasdaq.com/market-activity/earnings` |
| **Yahoo Finance（无 key）** | — | 同 yfinance，见上 | 已在 repo 里 |

**FMP 经济日历**是目前免费接口里覆盖最全的：包含美联储 FOMC 会议日期、CPI/PCE 发布日、非农数据日等宏观事件。

FMP 示例（免费 key 可申请）：
```
GET https://financialmodelingprep.com/api/v3/economic_calendar
    ?from=2026-04-28&to=2026-05-10&apikey=YOUR_KEY
```

---

### 3. Stooq / Alpha Vantage 财报日历（无 key）

```bash
# Alpha Vantage 无 key 版（3 个月财报日历 CSV）
https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=3month&apikey=demo
```

---

## 二、需要 LLM 搜索 + 总结的场景

结构化接口**覆盖不到**的内容：

| 场景 | 原因 | 推荐方案 |
|---|---|---|
| 行业监管风险（反垄断调查、FDA 审批） | 无固定日期，靠新闻突发 | Tavily/Bing 搜索 + LLM 总结 |
| 竞争对手事件（对手发布产品影响你持仓的股） | 跨股票关联，结构化源无法感知 | 同上 |
| 地缘政治/宏观叙事变化 | 无结构化数据库 | 同上 |
| 美联储官员非正式讲话（非 FOMC 会议） | 无日历预排 | 同上 |

---

## 三、这个 repo 当前的状况

| 能力 | 状态 |
|---|---|
| 个股财报日期 | ❌ **未接入**（yfinance `calendar` 已可用但未读取） |
| 分析师预期 EPS/Revenue | ❌ **未接入** |
| 关联新闻（Yahoo Finance） | ❌ **未接入**（yfinance `news` 已可用但未读取） |
| Tavily/Bing 网络搜索 | ✅ 已有，但维度是"分析师观点"，不是"日历事件" |
| 美联储/宏观经济日历 | ❌ 无 |

---

## 四、最小改造方案（优先级排序）

### 方案 A：只用 yfinance，零新增依赖 ★★★

在 `YfinanceFetcher` 里加一个 `get_upcoming_events(stock_code)` 方法，读取：
- `ticker.calendar`：财报日 + 分析师预期
- `ticker.earnings_dates.head(3)`：下次财报预期 EPS
- `ticker.news[:5]`：最新 5 条新闻标题 + 链接

返回结构化字典，注入到 pipeline 的上下文，LLM 就能在分析里提"距离财报还有 X 天"。

**改动面**：`YfinanceFetcher` + `pipeline.py` 上下文组装 + `analyzer.py` prompt 模板。

### 方案 B：接入 FMP 经济日历（宏观事件）★★

在 `.env` 加 `FMP_API_KEY`（可选），新增 `fmp_fetcher.py` 或在 `base.py` 里加 `get_economic_events(start, end)`，返回当周宏观事件列表（FOMC、CPI 等）。

这些宏观事件可以在**大盘复盘**（`--market-review`）里作为背景信息注入。

**改动面**：新 fetcher + `.env.example` + `market_analyzer.py`。

### 方案 C：LLM 搜索维度扩展 ★

在 `src/search_service.py` 的美股搜索维度里，加一条 `upcoming_events`，query 模板类似：

```
{stock_name} earnings date 2026 Q2 upcoming catalyst
```

不需要新接口，Tavily 会返回含财报日期的新闻，LLM 再从中提炼"事件摘要"。

**改动面**：`search_service.py` 维度配置。

---

## 五、推荐的落地路径

```
第 1 步（本周可做）：
  用方案 A 在 YfinanceFetcher 里加 get_upcoming_events()
  把财报日期 + 分析师预期 EPS 注入个股分析上下文
  → LLM 分析报告里自动出现"距离财报还有 N 天，预期 EPS=2.63"

第 2 步（可选）：
  用方案 C 在搜索维度里加 upcoming_events
  → 得到更多财报前的市场预期、机构观点新闻

第 3 步（可选，需要 FMP key）：
  用方案 B 接入经济日历
  → 大盘复盘时自动加入"本周有 FOMC 会议（周三）、非农（周五）"等提醒
```

---

## 六、快速验证当前 GOOG 财报信息

运行探索脚本（见 [scripts/explore_yfinance.py](../scripts/explore_yfinance.py)）：

```powershell
# 看财报日历
C:/Users/songl/miniconda3/python.exe -c "
import yfinance as yf
t = yf.Ticker('GOOG')
print(t.calendar)
print(t.earnings_dates.head(3))
"
```

或者在探索脚本里加 `--section info`，在 `info` 字段里找 `earningsTimestamp` / `earningsCallTimestampStart`。
