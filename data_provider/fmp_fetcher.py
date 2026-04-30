# -*- coding: utf-8 -*-
"""
===================================
FmpFetcher - Financial Modeling Prep 经济日历 & 财报日历
===================================

数据来源：https://financialmodelingprep.com/
注册免费 key：https://financialmodelingprep.com/developer/docs/

免费额度：250 次/天
主要用途：
  - get_economic_events()    — 宏观经济日历（FOMC、CPI、非农、PCE 等）
  - get_earnings_calendar()  — 个股/全市场财报日历

时间范围约定：
  "未来 N 个交易日" 从明日起算，跳过周末，
  避免"周五查当周"时漏掉下周事件的问题。
"""

import logging
import os
from datetime import date, timedelta
from typing import List, Dict, Any, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import json

logger = logging.getLogger(__name__)

# FMP 基础 URL（免费套餐使用 /stable/ 端点，/api/v3/ 已对免费用户返回 403）
_FMP_BASE = "https://financialmodelingprep.com/stable"

# 高影响力宏观事件关键词（用于过滤 / 排序）
_HIGH_IMPACT_KEYWORDS = {
    "fomc", "federal reserve", "interest rate", "cpi", "pce",
    "nonfarm", "non-farm", "gdp", "unemployment", "retail sales",
    "pmi", "ism", "fed", "powell", "consumer price",
}

# 财报日历影响程度映射
_IMPACT_LABEL = {
    "High": "🔴 高影响",
    "Medium": "🟡 中影响",
    "Low": "⚪ 低影响",
}


def _next_n_trading_days(n: int = 5) -> tuple[date, date]:
    """
    从明日起（跳过周末），返回 (start_date, end_date) 覆盖接下来 n 个交易日。

    示例（今天 = 周五 2026-04-25）：
      start = 2026-04-28（下周一）
      end   = 2026-05-04（下周一 + 4 个交易日）

    这样无论今天是周几，都能正确覆盖下一个完整的 5 交易日窗口。
    """
    start = date.today() + timedelta(days=1)
    # 跳过周末，找第一个工作日
    while start.weekday() >= 5:
        start += timedelta(days=1)

    # 再向前数 n 个工作日找 end
    end = start
    counted = 1
    while counted < n:
        end += timedelta(days=1)
        if end.weekday() < 5:
            counted += 1

    return start, end


def _fetch_json(url: str, timeout: int = 10) -> Any:
    """发起 GET 请求，返回解析后的 JSON，失败抛出异常。"""
    req = Request(url, headers={"User-Agent": "daily-stock-analysis/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class FmpFetcher:
    """
    Financial Modeling Prep 数据适配器。

    职责：
    1. 宏观经济日历（get_economic_events）
    2. 个股 / 全市场财报日历（get_earnings_calendar）

    使用方式：
        fetcher = FmpFetcher(api_key="YOUR_KEY")
        events = fetcher.get_economic_events()
        earnings = fetcher.get_earnings_calendar(["GOOG", "META"])
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: FMP API key。若不传，则从环境变量 FMP_API_KEY 读取。
        """
        self.api_key = api_key or os.getenv("FMP_API_KEY", "")
        if not self.api_key:
            logger.debug("[FMP] 未配置 FMP_API_KEY，接口调用将失败")

    @property
    def is_available(self) -> bool:
        """是否有可用的 API key。"""
        return bool(self.api_key)

    # ──────────────────────────────────────────────
    # 宏观经济日历
    # ──────────────────────────────────────────────

    def get_economic_events(
        self,
        days_ahead: int = 5,
        country: str = "US",
        min_impact: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取未来 N 个交易日的宏观经济事件。

        时间窗口从明日起算（跳过周末），覆盖下一个 days_ahead 个交易日，
        避免"今天是周五"时只拿当天剩余时间的问题。

        Args:
            days_ahead: 覆盖几个交易日，默认 5（即一整个交易周）。
            country:    国家代码，默认 "US"；可传空字符串拿全球事件。
            min_impact: 最低影响级别过滤，"High" / "Medium" / None（不过滤）。

        Returns:
            标准化的事件列表，每项字典包含：
              date        str   "2026-04-30"
              time        str   "14:00"（美东时间）
              event       str   事件名称
              impact      str   "High" / "Medium" / "Low"
              actual      str   实际值（发布后才有）
              estimate    str   预期值
              previous    str   上期值
              country     str   国家代码
              is_high_impact  bool  是否高影响（含 FOMC / CPI 等关键词）
        """
        if not self.is_available:
            logger.warning("[FMP] 未配置 API key，跳过经济日历获取")
            return []

        start, end = _next_n_trading_days(days_ahead)
        # 注意：FMP 免费套餐不支持 country 查询参数（会返回 403），改为本地过滤。
        url = (
            f"{_FMP_BASE}/economic-calendar"
            f"?from={start}&to={end}&apikey={self.api_key}"
        )

        logger.info(f"[FMP] 获取经济日历: {start} → {end}  country_filter={country or 'ALL'}")

        try:
            raw = _fetch_json(url)
        except HTTPError as e:
            logger.warning(f"[FMP] 经济日历 HTTP 错误: {e.code} {e.reason}")
            return []
        except (URLError, OSError) as e:
            logger.warning(f"[FMP] 经济日历网络错误: {e}")
            return []
        except Exception as e:
            logger.warning(f"[FMP] 经济日历解析失败: {e}")
            return []

        if not isinstance(raw, list):
            logger.warning(f"[FMP] 经济日历返回格式异常: {type(raw)}")
            return []

        events = []
        for item in raw:
            impact = item.get("impact", "")
            # 本地按国家过滤（免费套餐无法在 URL 里传 country 参数）
            if country and item.get("country", "").upper() != country.upper():
                continue
            # 影响级别过滤
            if min_impact == "High" and impact != "High":
                continue
            if min_impact == "Medium" and impact not in ("High", "Medium"):
                continue

            event_name = item.get("event", "")
            is_high = any(kw in event_name.lower() for kw in _HIGH_IMPACT_KEYWORDS)

            events.append({
                "date":           item.get("date", "")[:10],
                "time":           item.get("date", "")[11:16] if len(item.get("date", "")) > 10 else "",
                "event":          event_name,
                "impact":         impact,
                "actual":         str(item.get("actual", "") or ""),
                "estimate":       str(item.get("estimate", "") or ""),
                "previous":       str(item.get("previous", "") or ""),
                "country":        item.get("country", ""),
                "is_high_impact": is_high,
            })

        # 高影响优先排序
        events.sort(key=lambda x: (x["date"], 0 if x["impact"] == "High" else 1))
        logger.info(f"[FMP] 经济日历获取 {len(events)} 条事件（{start} ~ {end}）")
        return events

    # ──────────────────────────────────────────────
    # 财报日历
    # ──────────────────────────────────────────────

    def get_earnings_calendar(
        self,
        symbols: Optional[List[str]] = None,
        days_ahead: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        获取未来 N 个交易日的财报日历。

        Args:
            symbols:    股票代码列表，如 ["GOOG", "META"]。
                        传 None 或空列表时返回全市场结果（数据量较大）。
            days_ahead: 覆盖几个交易日，默认 5。

        Returns:
            标准化的财报事件列表，每项字典包含：
              symbol      str   股票代码
              date        str   财报日期
              time        str   "bmo"（盘前）/ "amc"（盘后）/ "dmh"（盘中）
              eps_estimate float   分析师预期 EPS
              revenue_estimate float  分析师预期营收
              fiscal_year str   财年
              fiscal_quarter str 财季
        """
        if not self.is_available:
            logger.warning("[FMP] 未配置 API key，跳过财报日历获取")
            return []

        start, end = _next_n_trading_days(days_ahead)
        url = (
            f"{_FMP_BASE}/earnings-calendar"
            f"?from={start}&to={end}&apikey={self.api_key}"
        )

        logger.info(f"[FMP] 获取财报日历: {start} → {end}")

        try:
            raw = _fetch_json(url)
        except HTTPError as e:
            logger.warning(f"[FMP] 财报日历 HTTP 错误: {e.code} {e.reason}")
            return []
        except (URLError, OSError) as e:
            logger.warning(f"[FMP] 财报日历网络错误: {e}")
            return []
        except Exception as e:
            logger.warning(f"[FMP] 财报日历解析失败: {e}")
            return []

        if not isinstance(raw, list):
            logger.warning(f"[FMP] 财报日历返回格式异常: {type(raw)}")
            return []

        # 规范化 symbols 集合（大写）
        filter_set = {s.strip().upper() for s in (symbols or [])} if symbols else None

        results = []
        for item in raw:
            sym = (item.get("symbol") or "").strip().upper()
            if filter_set and sym not in filter_set:
                continue

            def _safe_float(v) -> Optional[float]:
                try:
                    return float(v) if v not in (None, "", "None") else None
                except (ValueError, TypeError):
                    return None

            results.append({
                "symbol":            sym,
                "date":              item.get("date", ""),
                "time":              item.get("time", ""),      # bmo / amc / dmh
                "eps_estimate":      _safe_float(item.get("epsEstimated")),
                "revenue_estimate":  _safe_float(item.get("revenueEstimated")),
                "fiscal_year":       str(item.get("fiscalDateEnding", "")),
                "fiscal_quarter":    str(item.get("quarter", "")),
            })

        results.sort(key=lambda x: x["date"])
        logger.info(f"[FMP] 财报日历获取 {len(results)} 条")
        return results

    # ──────────────────────────────────────────────
    # 格式化辅助（供 market_analyzer 直接调用）
    # ──────────────────────────────────────────────

    def format_events_for_prompt(
        self,
        economic_events: List[Dict[str, Any]],
        earnings: List[Dict[str, Any]],
        lang: str = "zh",
    ) -> str:
        """
        将经济日历 + 财报日历格式化为适合注入 LLM prompt 的文本块。

        Args:
            economic_events: get_economic_events() 的返回值
            earnings:        get_earnings_calendar() 的返回值
            lang:            "zh"（中文）或 "en"（英文）

        Returns:
            Markdown 格式的文本块，可直接拼入 prompt。
        """
        lines: List[str] = []

        # —— 宏观经济日历 ——
        if economic_events:
            if lang == "en":
                lines.append("## Upcoming Macro Events (Next 5 Trading Days)")
            else:
                lines.append("## 未来 5 个交易日宏观事件")

            # 按日期分组
            by_date: Dict[str, List[Dict]] = {}
            for ev in economic_events:
                by_date.setdefault(ev["date"], []).append(ev)

            for dt in sorted(by_date):
                lines.append(f"\n**{dt}**")
                for ev in by_date[dt]:
                    impact_tag = _IMPACT_LABEL.get(ev["impact"], ev["impact"])
                    estimate = f"预期: {ev['estimate']}" if ev.get("estimate") else ""
                    previous = f"前值: {ev['previous']}" if ev.get("previous") else ""
                    meta = "  ".join(filter(None, [estimate, previous]))
                    time_str = f" {ev['time']}" if ev.get("time") else ""
                    lines.append(
                        f"- {impact_tag} {ev['event']}{time_str}"
                        + (f"  （{meta}）" if meta else "")
                    )
        else:
            if lang == "en":
                lines.append("## Upcoming Macro Events\n_(No FMP_API_KEY configured or no events found)_")
            else:
                lines.append("## 未来 5 个交易日宏观事件\n_（未配置 FMP_API_KEY 或无事件）_")

        # —— 财报日历 ——
        if earnings:
            lines.append("")
            if lang == "en":
                lines.append("## Upcoming Earnings")
            else:
                lines.append("## 近期财报")

            by_date2: Dict[str, List[Dict]] = {}
            for e in earnings:
                by_date2.setdefault(e["date"], []).append(e)

            for dt in sorted(by_date2):
                lines.append(f"\n**{dt}**")
                for e in by_date2[dt]:
                    time_label = {"bmo": "盘前", "amc": "盘后", "dmh": "盘中"}.get(
                        e.get("time", ""), e.get("time", "")
                    )
                    eps_str = f"预期EPS {e['eps_estimate']:.2f}" if e.get("eps_estimate") is not None else ""
                    rev_str = ""
                    if e.get("revenue_estimate") is not None:
                        rev = e["revenue_estimate"]
                        if rev >= 1e9:
                            rev_str = f"预期营收 {rev/1e9:.1f}B"
                        else:
                            rev_str = f"预期营收 {rev/1e6:.0f}M"
                    meta = "  ".join(filter(None, [eps_str, rev_str]))
                    lines.append(
                        f"- **{e['symbol']}** {time_label}"
                        + (f"  （{meta}）" if meta else "")
                    )

        return "\n".join(lines)
