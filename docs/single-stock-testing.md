# 单股测试运行指南

本文只讲一件事：如何在本项目里测试单只股票。

## 1. 最常用命令

只测一只股票，不发通知，不跑大盘复盘：

```bash
python main.py --stocks SNDK --dry-run --no-notify --no-market-review
```

说明：
- `--stocks SNDK`：只跑你指定的股票。
- `--dry-run`：抓取和计算流程会跑，但跳过个股 AI 分析。
- `--no-notify`：不推送到企业微信/飞书/Telegram 等渠道。
- `--no-market-review`：不执行大盘复盘，避免混入大盘报告内容。

## 2. 参数说明（单股测试相关）

### 必加
- `--stocks <代码>`
  - 指定单只或多只股票。
  - 单只示例：`--stocks SNDK`
  - 多只示例：`--stocks SNDK,AAPL,TSLA`

### 强烈建议（测试时）
- `--dry-run`
  - 用于快速验证数据链路与流程，不消耗 AI 分析调用。
- `--no-notify`
  - 避免测试消息打扰真实通知群。
- `--no-market-review`
  - 只看个股，不看大盘，输出更干净。

### 按需添加
- `--debug`
  - 打开更详细日志，排查失败原因时使用。
- `--force-run`
  - 跳过交易日检查（周末/节假日也强制跑）。
- `--workers <N>`
  - 修改并发数（默认用配置值）。

## 3. 推荐参数组合

### A. 最安全的单股测试（推荐）

```bash
python main.py --stocks SNDK --dry-run --no-notify --no-market-review
```

适合：先确认数据获取、流程和配置是否正常。

### B. 单股真实分析（调用 AI）

```bash
python main.py --stocks SNDK --no-notify --no-market-review
```

适合：确认该股票的最终分析结果和建议内容。

### C. 单股真实分析并允许通知

```bash
python main.py --stocks SNDK --no-market-review
```

适合：在已确认配置正确后，进行接近生产的验证。

## 4. 跑完后报告在哪里

默认都保存在项目根目录下的 `reports` 文件夹。

### 个股分析报告（真实分析时）

- 触发条件：不使用 `--dry-run`
- 文件名：`reports/report_YYYYMMDD.md`
- 例子：`reports/report_20260428.md`

说明：
- 只要跑了真实个股分析，就会保存这份决策仪表盘报告。
- 即使使用 `--no-notify`，本地报告也会保存。

### 大盘复盘报告（启用大盘时）

- 触发条件：未加 `--no-market-review` 且大盘复盘实际执行
- 文件名：`reports/market_review_YYYYMMDD.md`
- 例子：`reports/market_review_20260428.md`

### 你现在最常用命令的结果

命令：

```bash
python main.py --stocks SNDK --dry-run --no-notify --no-market-review
```

这个组合通常不会生成新的 Markdown 报告文件（因为同时关闭了个股 AI 报告和大盘复盘报告）。

### 快速定位最新报告（Windows PowerShell）

```powershell
Get-ChildItem reports -File | Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name,LastWriteTime
```

如果你想看到个股报告文件，请至少去掉 `--dry-run`：

```bash
python main.py --stocks SNDK --no-notify --no-market-review
```

## 5. PowerShell 快捷脚本

项目提供了 Windows 快捷脚本：

```bash
powershell -ExecutionPolicy Bypass -File scripts/run-single-stock.ps1 -Stock SNDK
```

脚本默认行为：
- 自动带 `--dry-run`
- 自动带 `--no-notify`
- 自动带 `--no-market-review`
- 默认关闭自动回测（仅本次进程生效）

可选开关：
- `-RealRun`：去掉 dry-run，执行真实 AI 分析
- `-EnableNotify`：开启通知
- `-IncludeMarketReview`：包含大盘复盘
- `-IncludeBacktest`：包含自动回测
- `-ForceRun`：强制运行（跳过交易日检查）

示例：

```bash
powershell -ExecutionPolicy Bypass -File scripts/run-single-stock.ps1 -Stock SNDK -RealRun -EnableNotify
```

## 6. 常见误区

- 误区：加了 `--dry-run` 就不会有任何 AI 输出。
  - 实际：`--dry-run` 只影响个股分析 AI；如果未加 `--no-market-review`，大盘复盘仍可能调用 AI。

- 误区：跑单股就一定不会出现大盘信息。
  - 实际：若 `MARKET_REVIEW_ENABLED=true` 且未加 `--no-market-review`，主流程会继续执行大盘复盘。

## 7. 建议的排查顺序

1. 先跑 A 组合（`--dry-run --no-notify --no-market-review`）
2. 再跑 B 组合（去掉 `--dry-run`）
3. 最后才启用通知（去掉 `--no-notify`）
