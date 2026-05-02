# -*- coding: utf-8 -*-
"""
===================================
股票速查命令
===================================

快速返回单只股票的技术指标摘要，不触发异步 AI 分析。

用法：
    /stock LITE
    /stock 600519
    /stock hk00700
"""

import logging
import re
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
from data_provider.base import canonical_stock_code
from src.core.digest_pipeline import DigestPipeline, _entry_point_text

logger = logging.getLogger(__name__)


def _fmt_change(pct: float) -> str:
    if pct > 0:
        return f"+{pct:.2f}%"
    if pct < 0:
        return f"{pct:.2f}%"
    return "0.00%"


class StockCommand(BotCommand):
    """查询单只股票的轻量技术指标。"""

    @property
    def name(self) -> str:
        return "stock"

    @property
    def aliases(self) -> List[str]:
        return ["quote", "q", "stockq", "查股", "个股"]

    @property
    def description(self) -> str:
        return "快速查询单只股票技术指标（非 AI 报告）"

    @property
    def usage(self) -> str:
        return "/stock <股票代码>"

    def validate_args(self, args: List[str]) -> Optional[str]:
        if not args:
            return "请输入股票代码"

        code = args[0].upper().strip()
        is_a_stock = re.match(r"^\d{6}$", code)
        is_hk_stock = re.match(r"^HK\d{5}$", code)
        is_us_stock = re.match(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$", code)

        if not (is_a_stock or is_hk_stock or is_us_stock):
            return f"无效的股票代码: {code}（A股6位数字 / 港股HK+5位数字 / 美股1-5个字母）"

        return None

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        code = canonical_stock_code(args[0])
        logger.info("[StockCommand] 查询股票: %s", code)

        try:
            pipeline = DigestPipeline()
            result = pipeline._screen_one(code)

            if result.error:
                return BotResponse.error_response(f"{code} 数据获取失败: {result.error}")

            entry_text = _entry_point_text(result)
            reasons = " · ".join(result.signal_reasons[:4]) if result.signal_reasons else "-"
            risks = " · ".join(result.risk_factors[:3]) if result.risk_factors else "-"
            alert = result.rsi_alert_label if result.is_flagged else "无"

            text = (
                f"## {result.code}（{result.name}）\n"
                f"- 价格: {result.price:.2f} ({_fmt_change(result.price_change_pct)})\n"
                f"- RSI: {result.rsi_6:.1f} / {result.rsi_12:.1f}（预警: {alert}）\n"
                f"- 趋势/MACD: {result.trend_status} / {result.macd_status}\n"
                f"- 信号: {result.buy_signal}（{result.signal_score}分）\n"
                f"- MA: MA5={result.ma5:.2f}, MA10={result.ma10:.2f}, MA20={result.ma20:.2f}\n"
                f"- 买卖参考: {entry_text or '-'}\n"
                f"- 理由: {reasons}\n"
                f"- 风险: {risks}"
            )
            return BotResponse.markdown_response(text)
        except Exception as exc:
            logger.exception("[StockCommand] 执行失败")
            return BotResponse.error_response(f"查询失败: {str(exc)[:120]}")
