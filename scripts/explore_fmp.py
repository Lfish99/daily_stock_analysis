#!/usr/bin/env python3
"""
FMP API 探索脚本 — Financial Modeling Prep

用法:
    python scripts/explore_fmp.py                         # 经济日历 + 财报日历（默认 5 天）
    python scripts/explore_fmp.py --section economic      # 仅经济日历
    python scripts/explore_fmp.py --section earnings      # 仅财报日历
    python scripts/explore_fmp.py --section all           # 两者
    python scripts/explore_fmp.py --days 7                # 未来 7 个交易日
    python scripts/explore_fmp.py --symbols AAPL,GOOG     # 指定股票财报
    python scripts/explore_fmp.py --impact high           # 仅高影响力经济事件

环境变量:
    FMP_API_KEY   — 必须提前设置，或通过 .env 文件加载
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 允许从项目根目录运行
sys.path.insert(0, str(Path(__file__).parent.parent))

# 尝试加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from data_provider.fmp_fetcher import FmpFetcher, _next_n_trading_days


def print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def explore_economic(fmp: FmpFetcher, days: int, impact: str | None) -> None:
    print_section(f"经济日历（未来 {days} 个交易日）")

    start, end = _next_n_trading_days(days)
    print(f"时间窗口: {start} → {end}\n")

    events = fmp.get_economic_events(days_ahead=days, min_impact=impact or None)
    if not events:
        print("(无事件或 API 返回为空)")
        return

    print(f"共 {len(events)} 条事件:\n")
    for e in events:
        impact_tag = f"[{e['impact'].upper()}]" if e.get('impact') else ""
        print(
            f"  {e['date']} {e.get('time',''):<8} {impact_tag:<8} "
            f"{e['country']:<4} {e['event']}"
        )
        if e.get('estimate') is not None:
            print(f"            预期={e['estimate']}  前值={e.get('previous','N/A')}")

    print("\n--- 原始 JSON 样本（前 2 条） ---")
    print(json.dumps(events[:2], ensure_ascii=False, indent=2))


def explore_earnings(fmp: FmpFetcher, days: int, symbols: list[str]) -> None:
    print_section(f"财报日历（未来 {days} 个交易日）")

    start, end = _next_n_trading_days(days)
    print(f"时间窗口: {start} → {end}")
    if symbols:
        print(f"过滤股票: {', '.join(symbols)}\n")
    else:
        print("（不过滤股票，返回全部）\n")

    earnings = fmp.get_earnings_calendar(symbols=symbols or None, days_ahead=days)
    if not earnings:
        print("(无财报事件或 API 返回为空)")
        return

    print(f"共 {len(earnings)} 条财报:\n")
    for e in earnings:
        bmo_amc = "(BMO)" if e.get('time') == 'bmo' else "(AMC)" if e.get('time') == 'amc' else ""
        print(
            f"  {e['date']} {bmo_amc:<6} {e['symbol']:<8} "
            f"EPS预期={e.get('eps_estimate','N/A')}  "
            f"收入预期={e.get('revenue_estimate','N/A')}"
        )

    print("\n--- 原始 JSON 样本（前 2 条） ---")
    print(json.dumps(earnings[:2], ensure_ascii=False, indent=2))


def explore_formatted(fmp: FmpFetcher, days: int, symbols: list[str], lang: str = "zh") -> None:
    print_section(f"格式化 Prompt 块（lang={lang}）")
    economic = fmp.get_economic_events(days_ahead=days)
    earnings = fmp.get_earnings_calendar(symbols=symbols or None, days_ahead=days)
    block = fmp.format_events_for_prompt(economic, earnings, lang=lang)
    print(block)


def main() -> None:
    parser = argparse.ArgumentParser(description="FMP API 探索脚本")
    parser.add_argument(
        "--section",
        choices=["economic", "earnings", "all", "formatted"],
        default="all",
        help="要查看的数据部分",
    )
    parser.add_argument("--days", type=int, default=5, help="未来多少个交易日（默认5）")
    parser.add_argument("--symbols", default="", help="逗号分隔的股票代码，用于过滤财报日历")
    parser.add_argument("--impact", choices=["high", "medium", "low"], default=None, help="过滤最低影响力级别")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh", help="格式化输出语言（仅 formatted 模式）")
    args = parser.parse_args()

    api_key = os.getenv("FMP_API_KEY", "")
    fmp = FmpFetcher(api_key=api_key)

    if not fmp.is_available:
        print("⚠️  未检测到 FMP_API_KEY，将使用空 key 请求（可能返回错误）")
        print("   请在 .env 中设置: FMP_API_KEY=your_key_here\n")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.section in ("economic", "all"):
        explore_economic(fmp, args.days, args.impact)

    if args.section in ("earnings", "all"):
        explore_earnings(fmp, args.days, symbols)

    if args.section == "formatted":
        explore_formatted(fmp, args.days, symbols, lang=args.lang)

    print("\nDone.")


if __name__ == "__main__":
    main()
