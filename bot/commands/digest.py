# -*- coding: utf-8 -*-
"""
===================================
每日选股雷达命令
===================================

触发 DigestPipeline 轻量级全量筛选，生成并推送每日选股雷达报告。

用法：
    /digest           - 扫描配置中的全量自选股
    /digest TSLA NVDA - 扫描指定股票（空格分隔）

别名：digest / d / 雷达 / 选股
"""

import logging
import threading
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


class DigestCommand(BotCommand):
    """
    每日选股雷达命令

    轻量级全量技术扫描（RSI/MACD/趋势/信号），
    无 LLM 调用，通常 3 分钟内完成并推送结果。
    """

    @property
    def name(self) -> str:
        return "digest"

    @property
    def aliases(self) -> List[str]:
        return ["d", "雷达", "选股", "扫描"]

    @property
    def description(self) -> str:
        return "每日选股雷达（RSI/MACD/信号全量扫描）"

    @property
    def usage(self) -> str:
        return "/digest [股票代码...]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """
        启动后台选股雷达扫描。

        Args:
            args: 可选的股票代码列表；为空则使用配置中的 STOCK_LIST。
        """
        stock_codes = [a.upper() for a in args if a.strip()] or None

        scope_str = (
            f"{len(stock_codes)} 只指定股票" if stock_codes
            else "全量自选股"
        )

        thread = threading.Thread(
            target=self._run_digest,
            args=(message, stock_codes),
            daemon=True,
        )
        thread.start()

        return BotResponse.markdown_response(
            f"✅ **选股雷达已启动**\n\n"
            f"扫描范围：{scope_str}\n"
            f"正在计算 RSI / MACD / 趋势信号...\n\n"
            f"完成后将自动推送报告。"
        )

    def _run_digest(self, message: BotMessage, stock_codes: List[str]) -> None:
        """后台执行选股雷达。"""
        try:
            from src.config import get_config
            from src.core.digest_pipeline import DigestPipeline

            config = get_config()
            pipeline = DigestPipeline(config)
            pipeline.run_and_send(stock_codes=stock_codes, dry_run=False, no_notify=False)
        except Exception as exc:
            logger.error("[DigestCommand] 选股雷达执行失败: %s", exc, exc_info=True)
