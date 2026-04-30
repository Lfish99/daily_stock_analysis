#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单股测试快捷脚本（Python 版本）

设计目标：
1. 提供一个比手敲 main.py 参数更直观的单股运行入口。
2. 默认使用“安全测试模式”：dry-run + 不推送 + 不跑大盘复盘 + 不跑自动回测。
3. 通过可选参数按需切换为更接近生产的真实运行模式。

典型用法：
    python scripts/run_single_stock.py SNDK

等价于：
    python main.py --stocks SNDK --dry-run --no-notify --no-market-review

开启真实分析并允许通知：
    python scripts/run_single_stock.py SNDK --real-run --enable-notify
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="单股测试运行脚本（封装 main.py 常用参数）"
    )

    # 必填：要测试的股票代码。统一转大写后传给 main.py。
    parser.add_argument(
        "stock",
        type=str,
        help="股票代码，例如 SNDK / AAPL / 600519"
    )

    # 默认是 dry-run（跳过个股 AI 分析），开启该开关后才做真实 AI 分析。
    parser.add_argument(
        "--real-run",
        action="store_true",
        help="执行真实分析（默认是 dry-run）"
    )

    # 默认不推送，避免测试时打扰生产通知渠道。
    parser.add_argument(
        "--enable-notify",
        action="store_true",
        help="开启通知推送（默认关闭）"
    )

    # 默认不跑大盘复盘，保证输出聚焦于“单股测试”。
    parser.add_argument(
        "--include-market-review",
        action="store_true",
        help="包含大盘复盘（默认关闭）"
    )

    # 默认禁用自动回测，减少测试时额外耗时和噪音。
    parser.add_argument(
        "--include-backtest",
        action="store_true",
        help="包含自动回测（默认关闭）"
    )

    # 当遇到非交易日检查时，允许强制执行。
    parser.add_argument(
        "--force-run",
        action="store_true",
        help="传递 --force-run 给 main.py"
    )

    # 可选：指定 Python 可执行文件路径；默认使用当前解释器。
    parser.add_argument(
        "--python",
        type=str,
        default=sys.executable,
        help="Python 解释器路径（默认当前解释器）"
    )

    return parser


def build_main_command(args: argparse.Namespace) -> List[str]:
    """
    根据脚本参数拼装 main.py 命令。

    说明：
    - 这里故意只拼“和单股测试相关”的参数，不引入无关开关，保持脚本可预测。
    - 默认行为是保守的：减少 AI 消耗、减少通知干扰、减少大盘和回测噪音。
    """
    stock = args.stock.strip().upper()
    if not stock:
        raise ValueError("stock code cannot be empty")

    command: List[str] = [
        args.python,
        "main.py",
        "--stocks",
        stock,
    ]

    if not args.real_run:
        command.append("--dry-run")

    if not args.enable_notify:
        command.append("--no-notify")

    if not args.include_market_review:
        command.append("--no-market-review")

    if args.force_run:
        command.append("--force-run")

    return command


def main() -> int:
    """脚本主入口：组装命令、打印命令、转发执行。"""
    parser = build_parser()
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    try:
        command = build_main_command(args)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    # 复制当前进程环境变量后再做“本次运行专属调整”，避免污染全局 shell。
    env = os.environ.copy()

    # Windows 控制台常见 gbk 编码场景下，emoji 日志可能触发编码错误。
    # 指定 UTF-8 可以降低日志编码问题概率。
    env["PYTHONIOENCODING"] = "utf-8"

    # 默认关闭自动回测：只影响当前子进程，不会写回 .env。
    if not args.include_backtest:
        env["BACKTEST_ENABLED"] = "false"

    print(f"Running single-stock analysis for {args.stock.strip().upper()}")
    print("Command:", " ".join(command))

    # 不捕获 stdout/stderr，让用户实时看到 main.py 原始日志输出。
    completed = subprocess.run(command, cwd=project_root, env=env)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
