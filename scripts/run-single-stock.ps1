param(
    [Parameter(Mandatory = $true)]
    [string]$Stock,

    [switch]$RealRun,
    [switch]$EnableNotify,
    [switch]$IncludeMarketReview,
    [switch]$IncludeBacktest,
    [switch]$ForceRun
)

$ErrorActionPreference = 'Stop'

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error 'python command not found. Please activate your Python environment first.'
}

$stockCode = $Stock.Trim().ToUpper()
if ([string]::IsNullOrWhiteSpace($stockCode)) {
    Write-Error 'Stock code cannot be empty.'
}

# Avoid Windows GBK console encoding issues on emoji logs.
$env:PYTHONIOENCODING = 'utf-8'

if (-not $IncludeBacktest) {
    # Disable auto backtest for this run only.
    $env:BACKTEST_ENABLED = 'false'
}

$pythonArgs = @('main.py', '--stocks', $stockCode)

if (-not $RealRun) {
    $pythonArgs += '--dry-run'
}

if (-not $EnableNotify) {
    $pythonArgs += '--no-notify'
}

if (-not $IncludeMarketReview) {
    $pythonArgs += '--no-market-review'
}

if ($ForceRun) {
    $pythonArgs += '--force-run'
}

Write-Host ("Running single-stock analysis for {0}" -f $stockCode)
Write-Host ("Command: python {0}" -f ($pythonArgs -join ' '))

& python @pythonArgs
exit $LASTEXITCODE
