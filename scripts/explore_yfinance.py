#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
explore_yfinance.py
===================
探索 yfinance 接口的交互式脚本。
直接打印每个接口的原始返回，方便了解可用字段和数据质量。

用法：
  python scripts/explore_yfinance.py          # 默认用 AAPL
  python scripts/explore_yfinance.py TSLA     # 指定美股代码
  python scripts/explore_yfinance.py TSLA --section all     # 跑所有小节
  python scripts/explore_yfinance.py TSLA --section history # 只跑日线
  python scripts/explore_yfinance.py TSLA --section info    # 只跑基本信息
  python scripts/explore_yfinance.py TSLA --section realtime
  python scripts/explore_yfinance.py TSLA --section financials
  python scripts/explore_yfinance.py TSLA --section holders
  python scripts/explore_yfinance.py TSLA --section options

注意：本脚本会发起真实网络请求，不适合在 CI（not network 测试套件）中运行。
"""

import argparse
import sys
import textwrap
from datetime import date, timedelta

try:
    import yfinance as yf
except ImportError:
    print("请先安装 yfinance:  pip install yfinance")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def section(title: str) -> None:
    """打印带分隔线的小节标题"""
    bar = "─" * 60
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)


def show_df(df, max_rows: int = 5) -> None:
    """打印 DataFrame 概要"""
    if df is None or (hasattr(df, "empty") and df.empty):
        print("  (空 / None)")
        return
    print(f"  shape: {df.shape}   columns: {list(df.columns)}")
    print(df.tail(max_rows).to_string())


def show_dict(d: dict, indent: int = 2) -> None:
    """打印字典，跳过空值"""
    pad = " " * indent
    if not d:
        print(f"{pad}(空字典)")
        return
    for k, v in d.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        if isinstance(v, str) and len(v) > 200:
            v = v[:200] + "…"
        print(f"{pad}{k}: {v}")


# ─────────────────────────────────────────────────────────────
# 各小节的探索函数
# ─────────────────────────────────────────────────────────────

def explore_history(ticker: yf.Ticker, symbol: str) -> None:
    """
    历史日线数据（最常用）
    对应 YfinanceFetcher._fetch_raw_data()
    """
    section("1. 历史日线数据  ticker.history()")

    end = date.today()
    start = end - timedelta(days=30)

    print(f"  请求范围: {start} → {end}")
    hist = ticker.history(start=str(start), end=str(end), auto_adjust=True)

    show_df(hist, max_rows=5)

    if not hist.empty:
        print("\n  字段说明:")
        print("    Open/High/Low/Close  开高低收（已复权）")
        print("    Volume               成交量（股）")
        print("    Dividends            分红")
        print("    Stock Splits         拆股")
        print(f"\n  最新一行:")
        print(f"    {hist.iloc[-1].to_dict()}")


def explore_info(ticker: yf.Ticker, symbol: str) -> None:
    """
    完整基本信息（字段多但较慢）
    ticker.info 返回 200+ 个字段的字典，网络较慢时可能超时
    """
    section("2. 基本信息  ticker.info")

    print("  正在请求（可能稍慢）…")
    try:
        info = ticker.info
        if not info:
            print("  (空，可能该代码已退市或 Yahoo 暂时不可用)")
            return

        # 分类展示最有用的字段
        groups = {
            "公司基本信息": [
                "shortName", "longName", "sector", "industry",
                "country", "exchange", "currency",
            ],
            "估值指标": [
                "trailingPE", "forwardPE", "priceToBook",
                "enterpriseToEbitda", "priceToSalesTrailing12Months",
            ],
            "市场数据": [
                "currentPrice", "previousClose", "open",
                "dayHigh", "dayLow", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                "volume", "averageVolume", "marketCap", "beta",
            ],
            "财务数据": [
                "totalRevenue", "grossProfits", "ebitda",
                "netIncomeToCommon", "earningsGrowth", "revenueGrowth",
                "returnOnEquity", "returnOnAssets", "debtToEquity",
                "totalCash", "totalDebt", "freeCashflow",
            ],
            "分析师评级": [
                "recommendationMean", "recommendationKey",
                "numberOfAnalystOpinions", "targetHighPrice",
                "targetLowPrice", "targetMeanPrice",
            ],
        }
        for group_name, keys in groups.items():
            print(f"\n  [{group_name}]")
            for k in keys:
                v = info.get(k)
                if v is not None:
                    print(f"    {k}: {v}")

        print(f"\n  全部可用字段数量: {len([k for k, v in info.items() if v is not None])}")
        print("  （设置 --section info 并在代码里打印 info 即可看全部字段）")

    except Exception as e:
        print(f"  获取 ticker.info 失败: {e}")


def explore_fast_info(ticker: yf.Ticker, symbol: str) -> None:
    """
    快速行情（字段少但快得多）
    对应 YfinanceFetcher.get_realtime_quote() 中优先使用的路径
    """
    section("3. 快速实时行情  ticker.fast_info")

    try:
        fi = ticker.fast_info
        fields = [
            "lastPrice", "previousClose", "open",
            "dayHigh", "dayLow", "lastVolume",
            "marketCap", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
        ]
        print("  fast_info 常用字段（无需完整 info 请求）:")
        for f in fields:
            # fast_info 用属性访问，不是字典
            val = getattr(fi, f, None)
            if val is None:
                # 有些版本用 snake_case
                snake = ''.join(['_' + c.lower() if c.isupper() else c for c in f]).lstrip('_')
                val = getattr(fi, snake, None)
            print(f"    {f}: {val}")
    except Exception as e:
        print(f"  获取 fast_info 失败: {e}")


def explore_realtime(ticker: yf.Ticker, symbol: str) -> None:
    """综合实时行情：先试 fast_info，再试 history(period='2d')"""
    section("4. 实时行情（YfinanceFetcher.get_realtime_quote 实际路径）")

    # 路径 A：fast_info
    explore_fast_info(ticker, symbol)

    # 路径 B：history 兜底（当 fast_info 字段缺失时）
    print("\n  [路径 B] history(period='2d') 兜底:")
    hist = ticker.history(period="2d")
    show_df(hist, max_rows=2)


def explore_financials(ticker: yf.Ticker, symbol: str) -> None:
    """
    财务报表（季报/年报）
    通常不在实时分析里用，但对基本面分析有价值
    """
    section("5. 财务报表  ticker.financials / quarterly_financials")

    print("\n  [年报 - 利润表] ticker.financials:")
    show_df(ticker.financials, max_rows=3)

    print("\n  [季报 - 利润表] ticker.quarterly_financials:")
    show_df(ticker.quarterly_financials, max_rows=3)

    print("\n  [资产负债表] ticker.balance_sheet:")
    show_df(ticker.balance_sheet, max_rows=3)

    print("\n  [现金流量表] ticker.cashflow:")
    show_df(ticker.cashflow, max_rows=3)


def explore_holders(ticker: yf.Ticker, symbol: str) -> None:
    """
    持股信息（机构/内部人员）
    可作为美股"筹码"的补充参考数据
    """
    section("6. 持股信息  ticker.institutional_holders / major_holders")

    print("\n  [主要股东占比] ticker.major_holders:")
    show_df(ticker.major_holders, max_rows=10)

    print("\n  [机构持股 TOP 10] ticker.institutional_holders:")
    show_df(ticker.institutional_holders, max_rows=10)

    print("\n  [共同基金持股 TOP 5] ticker.mutualfund_holders:")
    show_df(ticker.mutualfund_holders, max_rows=5)


def explore_options(ticker: yf.Ticker, symbol: str) -> None:
    """
    期权数据
    到期日列表 + 某个到期日的认购/认沽持仓
    """
    section("7. 期权数据  ticker.options")

    try:
        expirations = ticker.options
        if not expirations:
            print("  (无期权数据)")
            return

        print(f"  可用到期日（共 {len(expirations)} 个）: {expirations[:5]} …")

        # 取最近一个到期日
        exp = expirations[0]
        chain = ticker.option_chain(exp)

        print(f"\n  到期日 {exp}:")
        print(f"  [认购 Calls]  shape={chain.calls.shape}")
        if not chain.calls.empty:
            print(chain.calls[["strike", "lastPrice", "openInterest", "impliedVolatility"]].head(5).to_string())

        print(f"\n  [认沽 Puts]   shape={chain.puts.shape}")
        if not chain.puts.empty:
            print(chain.puts[["strike", "lastPrice", "openInterest", "impliedVolatility"]].head(5).to_string())

    except Exception as e:
        print(f"  获取期权数据失败: {e}")


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────

SECTION_MAP = {
    "history":    explore_history,
    "info":       explore_info,
    "realtime":   explore_realtime,
    "financials": explore_financials,
    "holders":    explore_holders,
    "options":    explore_options,
}

ALL_SECTIONS = list(SECTION_MAP.keys())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="探索 yfinance 接口，打印各接口的原始返回数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            小节说明：
              history    历史日线 OHLCV（最常用）
              info       完整基本信息（慢，但字段最多）
              realtime   实时行情（fast_info + history 兜底）
              financials 财务报表（利润表/资产负债表/现金流）
              holders    持股信息（机构/主要股东）
              options    期权链（到期日 + 持仓量）
        """),
    )
    parser.add_argument(
        "symbol",
        nargs="?",
        default="AAPL",
        help="美股代码，默认 AAPL",
    )
    parser.add_argument(
        "--section",
        choices=ALL_SECTIONS + ["all"],
        default="all",
        help="要探索的小节，默认 all（跑所有）",
    )
    args = parser.parse_args()

    symbol = args.symbol.strip().upper()

    print(f"\n{'='*60}")
    print(f"  yfinance 接口探索   代码: {symbol}")
    print(f"{'='*60}")
    print(f"  yfinance 版本: {yf.__version__}")

    ticker = yf.Ticker(symbol)

    if args.section == "all":
        targets = ALL_SECTIONS
    else:
        targets = [args.section]

    for name in targets:
        try:
            SECTION_MAP[name](ticker, symbol)
        except Exception as e:
            print(f"\n  [!] {name} 执行出错: {e}")

    print(f"\n{'='*60}")
    print("  探索完成")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
