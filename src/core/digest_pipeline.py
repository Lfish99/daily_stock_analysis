# -*- coding: utf-8 -*-
"""
===================================
每日选股雷达 - 轻量级全量筛选流水线
===================================

职责：
1. 对自选股列表进行全量技术指标计算（RSI/MACD/趋势/信号分）
2. 识别 RSI 超买（>75）/ 超卖（<30）预警股票
3. 为预警股票获取近期新闻
4. 注入 FMP 财报日历与宏观事件
5. 生成一份汇总 Markdown 报告并推送

与主流程（pipeline.py）的区别：
- 不调用 LLM 逐股深度分析（无 AI 费用）
- 覆盖自选股全量，而非仅少数股票
- 适合每日定时快速扫描，运行时间通常 < 5 分钟
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from data_provider import DataFetcherManager
from data_provider.base import normalize_stock_code
from data_provider.fmp_fetcher import FmpFetcher
from src.config import get_config, Config
from src.notification import NotificationService
from src.search_service import SearchService, SearchResponse
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult

logger = logging.getLogger(__name__)

# RSI 预警阈值（用户偏好: 超卖 <30, 超买 >75）
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 75

# 最多为多少只预警股票拉新闻（节省 API 配额）
MAX_NEWS_STOCKS = 8

# 获取多少天的历史 K 线（MA60 至少需要 ~90 天）
HISTORY_DAYS = 90

# 并发抓取数据的最大线程数
MAX_FETCH_WORKERS = 8

# 信号优先级排序（强烈买入 → 买入 → 持有 → 观望 → 卖出 → 强烈卖出）
_SIGNAL_ORDER = {
    "强烈买入": 0,
    "买入":     1,
    "持有":     2,
    "观望":     3,
    "卖出":     4,
    "强烈卖出": 5,
}

_SIGNAL_EMOJI = {
    "强烈买入": "🔥",
    "买入":     "✅",
    "持有":     "➡️",
    "观望":     "⏳",
    "卖出":     "⚠️",
    "强烈卖出": "🚨",
}


@dataclass
class StockScreenResult:
    """单只股票的技术筛选结果"""
    code: str
    name: str
    price: float = 0.0
    price_change_pct: float = 0.0   # 日涨跌幅 %
    rsi_6: float = 0.0
    rsi_12: float = 0.0
    rsi_status: str = ""
    macd_status: str = ""
    trend_status: str = ""
    signal_score: int = 0
    buy_signal: str = ""
    is_oversold: bool = False       # RSI(6) < RSI_OVERSOLD
    is_overbought: bool = False     # RSI(6) > RSI_OVERBOUGHT
    realtime_used: bool = False     # 是否已用实时价格增强
    error: Optional[str] = None
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    resistance_levels: List[float] = field(default_factory=list)

    @property
    def is_flagged(self) -> bool:
        return self.is_oversold or self.is_overbought

    @property
    def rsi_alert_label(self) -> str:
        if self.is_oversold:
            return "超卖"
        if self.is_overbought:
            return "超买"
        return ""


class DigestPipeline:
    """
    每日选股雷达流水线

    调用方式::

        pipeline = DigestPipeline(config)
        content = pipeline.run()           # 返回 Markdown 文本
        pipeline.send(content)             # 推送通知

    或直接：``pipeline.run_and_send()``
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.fetcher_manager = DataFetcherManager()
        self.trend_analyzer = StockTrendAnalyzer()
        self.notifier = NotificationService()
        self.fmp = FmpFetcher(api_key=getattr(self.config, 'fmp_api_key', None) or "")

        try:
            self.search_service: Optional[SearchService] = SearchService(
                bocha_keys=self.config.bocha_api_keys,
                tavily_keys=self.config.tavily_api_keys,
                anspire_keys=self.config.anspire_api_keys,
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                minimax_keys=self.config.minimax_api_keys,
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                news_max_age_days=self.config.news_max_age_days,
                news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
            )
        except Exception as exc:
            logger.warning("搜索服务初始化失败，新闻将不可用: %s", exc)
            self.search_service = None

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def run(self, stock_codes: Optional[List[str]] = None, dry_run: bool = False) -> str:
        """
        执行完整筛选流程，返回 Markdown 格式报告。

        Args:
            stock_codes: 股票列表（None 则使用配置里的 STOCK_LIST）
            dry_run: 仅计算技术指标，不拉新闻/FMP（调试用）
        """
        codes = stock_codes or self.config.stock_list
        if not codes:
            logger.warning("[Digest] 股票列表为空，退出")
            return ""

        logger.info("[Digest] 开始扫描 %d 只股票...", len(codes))
        t0 = time.time()

        # 1. 全量技术指标计算（并发）
        screen_results = self._screen_all(codes)

        # 2. 分离成功/失败，成功的按信号优先级排序（所有股票一视同仁）
        ok_results = [r for r in screen_results if not r.error]
        failed = [r for r in screen_results if r.error]

        # 按信号优先级排序（强烈买入 → 买入 → 持有 → 观望 → 卖出 → 强烈卖出），同级内按评分降序
        ok_results.sort(key=lambda r: (_SIGNAL_ORDER.get(r.buy_signal, 3), -r.signal_score))

        logger.info(
            "[Digest] 技术扫描完成: 共 %d 只, 成功 %d 只, 失败 %d 只, 耗时 %.1fs",
            len(screen_results), len(ok_results), len(failed), time.time() - t0,
        )

        # 3. 高优先级股票拉新闻（可选，受 dry_run 控制）
        news_map: Dict[str, List[str]] = {}
        if not dry_run and self.search_service and self.search_service.is_available:
            # 优先选择强烈买入/买入的股票拉新闻
            buy_signals = [r for r in ok_results if r.buy_signal in ("强烈买入", "买入")]
            news_targets = buy_signals[:MAX_NEWS_STOCKS]
            if news_targets:
                logger.info("[Digest] 拉取 %d 只买入信号股票的新闻...", len(news_targets))
                news_map = self._fetch_news_for_stocks(news_targets)

        # 4. FMP 财报 & 宏观事件 + yfinance 补充
        earnings: List[Dict] = []
        economic: List[Dict] = []
        # 自选股中所有美股代码（无论 screen 是否出错都应检查财报）
        us_codes_all = [c for c in codes if _looks_like_us_stock(c)]
        if not dry_run:
            if self.fmp.is_available:
                try:
                    earnings = self.fmp.get_earnings_calendar(symbols=us_codes_all or None, days_ahead=7)
                    economic = self.fmp.get_economic_events(days_ahead=7, country="US")
                    logger.info("[Digest] FMP: %d 条财报, %d 条宏观事件", len(earnings), len(economic))
                except Exception as exc:
                    logger.warning("[Digest] FMP 获取失败: %s", exc)
            # yfinance 补充财报日历（FMP 免费套餐覆盖率低，只含少量热门股）
            earnings = _supplement_earnings_yfinance(earnings, us_codes_all, days_ahead=7)

        # 5. 拼装报告
        report = self._format_report(
            results=ok_results,
            failed=failed,
            news_map=news_map,
            earnings=earnings,
            economic=economic,
        )

        logger.info("[Digest] 报告生成完毕，总耗时 %.1fs", time.time() - t0)
        return report

    def send(self, content: str) -> bool:
        """推送 Markdown 报告，返回是否成功"""
        if not content:
            logger.warning("[Digest] 报告内容为空，跳过推送")
            return False
        if not self.notifier.is_available():
            logger.warning("[Digest] 通知服务未配置，无法推送")
            return False
        ok = self.notifier.send(content)
        if ok:
            logger.info("[Digest] 报告推送成功")
        else:
            logger.warning("[Digest] 报告推送失败")
        return ok

    def run_and_send(
        self,
        stock_codes: Optional[List[str]] = None,
        dry_run: bool = False,
        no_notify: bool = False,
    ) -> str:
        """执行筛选 + 推送（快捷入口）"""
        content = self.run(stock_codes=stock_codes, dry_run=dry_run)
        if content and not no_notify:
            self.send(content)
        return content

    # ------------------------------------------------------------------
    # 内部：技术指标批量计算
    # ------------------------------------------------------------------

    def _screen_one(self, code: str) -> StockScreenResult:
        """抓取历史数据并计算技术指标，返回单只股票的筛选结果。
        
        实时价格增强逻辑：
        - 如果 enable_realtime_quote=True 且市场正在交易，用当前价格替换/追加今日 K 线。
        - RSI 因此反映"此刻"而不是"昨日收盘"，让日内超买/超卖信号更及时。
        """
        result = StockScreenResult(code=code, name=code)
        try:
            name = self.fetcher_manager.get_stock_name(code, allow_realtime=False) or code
            result.name = name

            df, _src = self.fetcher_manager.get_daily_data(code, days=HISTORY_DAYS)
            if df is None or df.empty or len(df) < 20:
                result.error = f"数据不足（{len(df) if df is not None else 0} 行）"
                return result

            # 尝试用实时价格增强 df（让 RSI 反映当前行情）
            realtime_quote = None
            if getattr(self.config, 'enable_realtime_quote', True):
                try:
                    realtime_quote = self.fetcher_manager.get_realtime_quote(
                        code, log_final_failure=False
                    )
                except Exception:
                    pass

            if realtime_quote is not None:
                df_aug = self._augment_df_with_realtime(df, realtime_quote, code)
                if df_aug is not df:
                    df = df_aug
                    result.realtime_used = True

            trend: TrendAnalysisResult = self.trend_analyzer.analyze(df, code)

            result.price = trend.current_price
            result.rsi_6 = round(trend.rsi_6, 1)
            result.rsi_12 = round(trend.rsi_12, 1)
            result.rsi_status = trend.rsi_status.value
            result.macd_status = trend.macd_status.value
            result.trend_status = trend.trend_status.value
            result.signal_score = trend.signal_score
            result.buy_signal = trend.buy_signal.value
            result.signal_reasons = list(trend.signal_reasons or [])
            result.risk_factors = list(trend.risk_factors or [])
            result.ma5 = round(float(trend.ma5 or 0), 2)
            result.ma10 = round(float(trend.ma10 or 0), 2)
            result.ma20 = round(float(trend.ma20 or 0), 2)
            result.resistance_levels = [round(float(x), 2) for x in (trend.resistance_levels or [])]
            result.is_oversold = trend.rsi_6 < RSI_OVERSOLD
            result.is_overbought = trend.rsi_6 > RSI_OVERBOUGHT

            # 日涨跌幅（最后两根 K 线之差；实时增强后最后一行即当前价格）
            df_sorted = df.sort_values('date').reset_index(drop=True)
            if len(df_sorted) >= 2:
                prev_close = float(df_sorted.iloc[-2]['close'])
                curr_close = float(df_sorted.iloc[-1]['close'])
                if prev_close > 0:
                    result.price_change_pct = round((curr_close - prev_close) / prev_close * 100, 2)

        except Exception as exc:
            logger.warning("[Digest] %s 计算失败: %s", code, exc)
            result.error = str(exc)
        return result

    # ------------------------------------------------------------------
    # 内部：实时价格增强（参考 pipeline.py _augment_historical_with_realtime）
    # ------------------------------------------------------------------

    def _augment_df_with_realtime(
        self, df: pd.DataFrame, realtime_quote: Any, code: str
    ) -> pd.DataFrame:
        """
        用实时行情价格更新/追加今日 K 线，让 RSI 等指标基于当前价格计算。

        规则：
        - 若最后一行日期 == 今日 → 更新 close/open/high/low/volume
        - 若最后一行日期 < 今日（盘中数据未写入）→ 追加今日新行
        - 如果 price 无效或解析失败 → 原样返回 df（fail-open）
        """
        if df is None or df.empty:
            return df
        price = getattr(realtime_quote, 'price', None)
        if not (isinstance(price, (int, float)) and price > 0):
            return df

        try:
            from src.core.trading_calendar import (
                get_market_for_stock,
                get_market_now,
                is_market_open,
            )
            market = get_market_for_stock(code)
            today = get_market_now(market).date()
            # 只在交易时段增强（市场闭市时实时价格没有意义）
            if market and not is_market_open(market, today):
                return df
        except Exception:
            # 无法判断市场状态时直接跳过增强
            return df

        try:
            last_val = df['date'].max()
            last_date: date = (
                last_val.date() if hasattr(last_val, 'date') else
                (last_val if isinstance(last_val, date) else pd.Timestamp(last_val).date())
            )
        except Exception:
            return df

        yesterday_close = float(df.iloc[-1]['close'])
        open_p = getattr(realtime_quote, 'open_price', None) or yesterday_close
        high_p = getattr(realtime_quote, 'high', None) or price
        low_p = getattr(realtime_quote, 'low', None) or price
        vol = getattr(realtime_quote, 'volume', None) or 0

        df = df.copy()
        if last_date >= today:
            # 更新已有今日行
            idx = df.index[-1]
            df.loc[idx, 'close'] = price
            df.loc[idx, 'open'] = open_p
            df.loc[idx, 'high'] = max(float(high_p), price)
            df.loc[idx, 'low'] = min(float(low_p), price)
            if vol:
                df.loc[idx, 'volume'] = vol
        else:
            # 追加今日行
            new_row = {c: None for c in df.columns}
            new_row['date'] = pd.Timestamp(today)
            new_row['open'] = open_p
            new_row['high'] = max(float(high_p), price)
            new_row['low'] = min(float(low_p), price)
            new_row['close'] = price
            new_row['volume'] = vol
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

        return df

    def _screen_all(self, codes: List[str]) -> List[StockScreenResult]:
        """并发抓取所有股票的技术指标"""
        results: List[StockScreenResult] = []
        workers = min(MAX_FETCH_WORKERS, len(codes))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self._screen_one, code): code for code in codes}
            for future in as_completed(futures):
                results.append(future.result())
        # 保持与输入列表一致的顺序
        order = {code: i for i, code in enumerate(codes)}
        results.sort(key=lambda r: order.get(r.code, 9999))
        return results

    # ------------------------------------------------------------------
    # 内部：新闻获取
    # ------------------------------------------------------------------

    def _fetch_news_for_stocks(
        self, stocks: List[StockScreenResult]
    ) -> Dict[str, List[str]]:
        """
        为每只股票获取最新新闻标题列表（最多 3 条），返回 {code: [标题, ...]}
        """
        news_map: Dict[str, List[str]] = {}
        for stock in stocks:
            try:
                resp: SearchResponse = self.search_service.search_stock_news(  # type: ignore[union-attr]
                    stock_code=stock.code,
                    stock_name=stock.name,
                    max_results=3,
                )
                if resp.success and resp.results:
                    titles = []
                    for item in resp.results:
                        date_part = f" ({item.published_date})" if item.published_date else ""
                        titles.append(f"{item.title}{date_part}")
                    news_map[stock.code] = titles
                    logger.info("[Digest] %s 新闻: %d 条", stock.code, len(titles))
                else:
                    logger.debug("[Digest] %s 未获取到新闻", stock.code)
            except Exception as exc:
                logger.warning("[Digest] %s 新闻拉取失败: %s", stock.code, exc)
        return news_map

    # ------------------------------------------------------------------
    # 内部：报告格式化
    # ------------------------------------------------------------------

    def _format_report(
        self,
        results: List[StockScreenResult],
        failed: List[StockScreenResult],
        news_map: Dict[str, List[str]],
        earnings: List[Dict],
        economic: List[Dict],
    ) -> str:
        tz_cn = timezone(timedelta(hours=8))
        now = datetime.now(tz_cn)
        lines: List[str] = []

        # 结果已经按信号优先级排序（强烈买入 → 买入 → 持有 → 观望 → 卖出 → 强烈卖出）
        any_realtime = any(r.realtime_used for r in results)
        price_label = "价格(实时)" if any_realtime else "价格(收盘)"

        lines.append(f"# 每日选股雷达 {now.strftime('%Y-%m-%d %H:%M')} (CST)")
        if any_realtime:
            lines.append("")
            lines.append("> RSI 已基于实时价格计算")
        lines.append("")

        # --- 概览表格（按信号排序）---
        if results:
            lines.append(f"## 全量指标（{len(results)} 只，按信号优先级排序）")
            lines.append("")
            lines.append(f"| 代码 | {price_label} | 日涨跌 | RSI(6) | RSI(12) | 预警 | MACD | 趋势 | 信号 | 评分 |")
            lines.append("|------|------|--------|--------|---------|------|------|------|------|------|") 
            for r in results:
                change_str = _fmt_change(r.price_change_pct)
                alert = f"**{r.rsi_alert_label}**" if r.is_flagged else ""
                rsi6_str = f"**{r.rsi_6}**" if r.is_flagged else str(r.rsi_6)
                lines.append(
                    f"| {r.code} | {r.price:.2f} | {change_str} "
                    f"| {rsi6_str} | {r.rsi_12} | {alert} "
                    f"| {r.macd_status} | {r.trend_status} | {r.buy_signal} | {r.signal_score} |"
                )
            lines.append("")

        # --- 失败股票（简短提示）---
        if failed:
            codes_str = ", ".join(r.code for r in failed[:10])
            if len(failed) > 10:
                codes_str += f" 等 {len(failed)} 只"
            lines.append(f"> 数据获取失败（已跳过）: {codes_str}")
            lines.append("")

        # --- 个股买卖参考 ---
        if results:
            lines.append("## 个股买卖参考")
            lines.append("")
            for r in results:
                emoji = _SIGNAL_EMOJI.get(r.buy_signal, "")
                name_part = f"（{r.name}）" if r.name and r.name != r.code else ""
                lines.append(f"**{r.code}**{name_part} {emoji} {r.buy_signal} · {r.signal_score}分")
                entry = _entry_point_text(r)
                if entry:
                    lines.append(f"　{entry}")
                reasons = r.signal_reasons or []
                risks = r.risk_factors or []
                if reasons:
                    lines.append("　" + " · ".join(reasons))
                if risks:
                    lines.append("　⚠ " + " · ".join(risks))
                lines.append("")

        # --- 重点新闻 ---
        if news_map:
            lines.append("## 重点新闻（买入信号股票）")
            lines.append("")
            for r in results:
                titles = news_map.get(r.code)
                if not titles:
                    continue
                lines.append(f"**{r.code}** ({r.name})")
                for title in titles:
                    lines.append(f"- {title}")
                lines.append("")

        # --- 财报日历 ---
        if earnings:
            lines.append("## 近期财报日历（未来 7 天）")
            lines.append("")
            lines.append("| 股票 | 日期 | EPS 预期 | 营收预期 |")
            lines.append("|------|------|----------|----------|")
            for e in earnings[:20]:
                symbol = e.get('symbol', '')
                date_str = e.get('date', '')
                eps = e.get('eps_estimate') or e.get('epsEstimated', '')
                rev = e.get('revenue_estimate') or e.get('revenueEstimated', '')
                eps_str = f"${eps:.3f}" if isinstance(eps, (int, float)) else str(eps or '-')
                rev_str = _fmt_revenue(rev)
                lines.append(f"| {symbol} | {date_str} | {eps_str} | {rev_str} |")
            lines.append("")

        # --- 宏观事件 ---
        if economic:
            high_impact = [e for e in economic if (e.get('impact') or '').lower() == 'high']
            show = high_impact[:15] if high_impact else economic[:10]
            label = "高影响力宏观事件" if high_impact else "近期宏观事件"
            lines.append(f"## {label}（未来 7 天）")
            lines.append("")
            lines.append("| 日期 | 时间 | 事件 | 影响 |")
            lines.append("|------|------|------|------|")
            for e in show:
                date_str = e.get('date', '')
                time_str = e.get('time', '')
                event_name = (e.get('event') or e.get('name') or '')[:50]
                impact = e.get('impact', '')
                lines.append(f"| {date_str} | {time_str} | {event_name} | {impact} |")
            lines.append("")

        return "\n".join(lines)


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def _looks_like_us_stock(code: str) -> bool:
    """粗略判断是否为美股代码（1-5 位字母，可含点）"""
    import re
    return bool(re.match(r'^[A-Za-z]{1,5}(\.[A-Za-z])?$', (code or '').strip()))


def _fmt_change(pct: float) -> str:
    if pct > 0:
        return f"+{pct:.2f}%"
    if pct < 0:
        return f"{pct:.2f}%"
    return "0.00%"


def _entry_point_text(r: "StockScreenResult") -> str:
    """生成简短的买入点或卖出点提示文本。"""
    sig = r.buy_signal
    if sig in ("强烈买入", "买入"):
        parts = []
        if r.ma5 > 0:
            parts.append(f"MA5≈{r.ma5:.2f}")
        if r.ma10 > 0:
            parts.append(f"MA10≈{r.ma10:.2f}")
        buy_str = " / ".join(parts) if parts else "-"
        stop = f"MA20×0.97≈{r.ma20 * 0.97:.2f}" if r.ma20 > 0 else "-"
        return f"买点：{buy_str} | 止损：{stop}"
    if sig in ("卖出", "强烈卖出"):
        stop = f"MA5≈{r.ma5:.2f}" if r.ma5 > 0 else "-"
        resist = f"近期高点≈{r.resistance_levels[0]:.2f}" if r.resistance_levels else "-"
        return f"止损：{stop} | 压力：{resist}"
    if sig == "持有":
        stop = f"MA10≈{r.ma10:.2f}" if r.ma10 > 0 else "-"
        return f"持仓止损参考：{stop}"
    return ""


def _fmt_revenue(val) -> str:
    if val is None or val == '':
        return '-'
    try:
        v = float(val)
        if v >= 1e9:
            return f"${v/1e9:.2f}B"
        if v >= 1e6:
            return f"${v/1e6:.1f}M"
        return f"${v:.0f}"
    except (TypeError, ValueError):
        return str(val)


def _supplement_earnings_yfinance(
    existing: List[Dict],
    symbols: List[str],
    days_ahead: int = 7,
) -> List[Dict]:
    """
    用 yfinance 补充 FMP 财报日历未覆盖的股票。

    FMP 免费套餐仅返回少量热门股票的财报日历；对于自选股中未被 FMP 覆盖的
    股票，通过 yfinance Ticker.calendar 补充其下次财报日期。

    Args:
        existing:   已有的 FMP 财报列表（可为空）
        symbols:    需要检查的美股代码列表（watchlist 中的所有美股）
        days_ahead: 前向查看的交易日数（与 FMP 保持一致）

    Returns:
        合并后的财报列表（已去重，按日期排序）
    """
    from data_provider.fmp_fetcher import _next_n_trading_days
    try:
        import yfinance as yf
    except ImportError:
        logger.debug("[Digest] yfinance 未安装，跳过财报补充")
        return existing

    if not symbols:
        return existing

    start, end = _next_n_trading_days(days_ahead)
    covered = {e.get("symbol", "").upper() for e in existing}
    to_fetch = [s for s in symbols if s.upper() not in covered]

    if not to_fetch:
        return existing

    extra: List[Dict] = []

    def _fetch_one(sym: str) -> Optional[Dict]:
        try:
            cal = yf.Ticker(sym).calendar
            if not cal or "Earnings Date" not in cal:
                return None
            dates = cal["Earnings Date"]
            if not isinstance(dates, list):
                dates = [dates]
            for d in dates:
                if not isinstance(d, date):
                    try:
                        d = date.fromisoformat(str(d)[:10])
                    except Exception:
                        continue
                if start <= d <= end:
                    return {
                        "symbol":           sym.upper(),
                        "date":             str(d),
                        "time":             "",
                        "eps_estimate":     cal.get("Earnings Average"),
                        "revenue_estimate": cal.get("Revenue Average"),
                        "fiscal_year":      "",
                        "fiscal_quarter":   "",
                    }
        except Exception as exc:
            logger.debug("[Digest] yfinance 财报补充 %s 失败: %s", sym, exc)
        return None

    with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as executor:
        futures = {executor.submit(_fetch_one, s): s for s in to_fetch}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                extra.append(result)

    if extra:
        logger.info("[Digest] yfinance 补充财报 %d 条: %s",
                    len(extra), [e["symbol"] for e in extra])
        combined = existing + extra
        combined.sort(key=lambda x: x.get("date", ""))
        return combined

    return existing
