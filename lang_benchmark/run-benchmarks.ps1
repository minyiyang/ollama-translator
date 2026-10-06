# Language benchmarks (docs/GENERIC_LANGUAGES.md, sections 6 and 7). Run from the repository root in your
# own terminal; long runs started from an assistant session get stopped.
#
#   powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-benchmarks.ps1 en-zh
#   powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-benchmarks.ps1 -Round r2 zh-ja en-de
#   powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-benchmarks.ps1 -Round r2 -Translators "qwen,tg" zh-es
#   powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-benchmarks.ps1 -Round r2          # every pair
#   powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-benchmarks.ps1 -Round wind -Book wind-in-the-willows.epub en-de en-fr
#
# A pair is written source-target (en-de). Each run is the job runs\bench-<round>-<translator>-<pair>,
# with the config lang_benchmark\configs\<translator>-<pair>.yaml:
#   qwen  qwen3.8 translates
#   tg    translategemma:27b translates, qwen3.8 keeps the glossary, gemma4:31b repairs
# Without -Translators each pair runs with the translator section 7 recommends for it.
# -Book names another file in lang_benchmark\books for the pairs given (a second excerpt in the
# same source language); give such a run its own -Round.
# A job that exists is resumed from its checkpoints, so the script can be restarted at any point;
# a new -Round starts new jobs. Close other GPU-heavy applications first (docs/OPERATIONS.md, "GPU memory").
# The books are not in the repository: lang_benchmark\BOOKS.md says where they come from and
# python lang_benchmark\make_books.py builds them.
# Logs: runs\bench-logs\<job>.log. Results: lang_benchmark\results\<round>-results.md and <round>-summary.txt.
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Round = "r1",
    [string]$Translators = "",
    [string]$Book = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Pairs
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Bench = $PSScriptRoot
$Logs = Join-Path $Root "runs\bench-logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Bench "results") | Out-Null

# Pair -> book, and the translator section 7 recommends. Shortest books first.
$Books = [ordered]@{
    "ja-zh" = @("rashomon.epub", "qwen")
    "en-zh" = @("alice-3ch.epub", "qwen")
    "ko-zh" = @("unsu-joeun-nal.epub", "qwen")
    "zh-fr" = @("ah-q-ch1-5.epub", "qwen")
    "zh-es" = @("ah-q-ch1-5.epub", "tg")
    "zh-de" = @("ah-q-ch1-5.epub", "tg")
    "zh-ja" = @("ah-q-ch1-5.epub", "tg")
    "de-en" = @("verwandlung-1.epub", "tg")
    "de-zh" = @("verwandlung-1.epub", "qwen")
    "en-de" = @("alice-ch1-4.epub", "tg")
    "en-es" = @("alice-ch1-4.epub", "tg")
    "en-fr" = @("alice-ch1-4.epub", "tg")
    "en-ja" = @("alice-ch1-4.epub", "tg")
    "en-ko" = @("alice-ch1-4.epub", "tg")
    "fr-en" = @("meteore-ch1-3.epub", "tg")
    "es-en" = @("fortunata-1-2.epub", "tg")
    "es-zh" = @("fortunata-1-2.epub", "qwen")
}

function Clear-OllamaModels {
    try {
        $loaded = (Invoke-RestMethod -Uri "http://localhost:11434/api/ps" -TimeoutSec 10).models
        foreach ($model in $loaded) {
            Invoke-RestMethod -Uri "http://localhost:11434/api/generate" -Method Post -TimeoutSec 60 `
                -Body (@{ model = $model.name; keep_alive = 0 } | ConvertTo-Json) | Out-Null
            Write-Host "unloaded $($model.name)"
        }
    } catch { Write-Host "could not reach Ollama to unload models: $_" }
}

function Invoke-Benchmark([string]$JobId, [string]$Source, [string]$Config) {
    $job = Join-Path $Root "runs\$JobId"
    $log = Join-Path $Logs "$JobId.log"
    Clear-OllamaModels
    Write-Host "`n=== $JobId ($(Get-Date -Format s)) ===" -ForegroundColor Cyan
    Write-Host "log: $log"
    if (Test-Path (Join-Path $job "state.sqlite3")) {
        $arguments = @("-m", "book_agent.cli", "resume", $job, "--plain")
    } else {
        $arguments = @("-m", "book_agent.cli", "run", $Source, "--config", $Config, "--job-id", $JobId, "--plain")
    }
    # Every line goes to the terminal as it arrives and to the log. "$_" turns the
    # stderr records Windows PowerShell wraps around native output back into text;
    # Out-File keeps the log UTF-8 (Tee-Object would write UTF-16 here).
    & python @arguments 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        $line
    } | Out-File -FilePath $log -Append -Encoding utf8
    # Exit code 2 means paused for final human review: translation, audit, repair and validation are done.
    Write-Host "$JobId finished with exit code $LASTEXITCODE (0 complete, 2 paused for final review)" -ForegroundColor Cyan
}

$env:PYTHONIOENCODING = "utf-8"
# Read Python's UTF-8 output as UTF-8, so the log and the results files are not garbled.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
# Print progress lines as they happen instead of in buffered blocks.
$env:PYTHONUNBUFFERED = "1"

# A mistyped option lands among the pairs; refuse it instead of running the pairs beside it.
$stray = @($Pairs | Where-Object { $_ -like "-*" })
if ($stray) { Write-Host "unknown option $($stray -join ', '); nothing was run" -ForegroundColor Red; exit 1 }
if (-not $Pairs) { $Pairs = @($Books.Keys) }
$Chosen = @($Translators -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$jobs = @()
foreach ($pair in $Pairs) {
    if (-not $Books.Contains($pair)) { Write-Host "unknown pair $pair (known: $($Books.Keys -join ', '))" -ForegroundColor Red; continue }
    # PowerShell names are case-insensitive: this must not be called $book, which is the -Book option.
    $bookPath = Join-Path "lang_benchmark\books" $(if ($Book) { $Book } else { $Books[$pair][0] })
    if (-not (Test-Path (Join-Path $Root $bookPath))) { Write-Host "no book $bookPath; skipped (build the books: python lang_benchmark\make_books.py; see lang_benchmark\BOOKS.md)" -ForegroundColor Red; continue }
    $kinds = if ($Chosen) { $Chosen } else { @($Books[$pair][1]) }
    foreach ($kind in $kinds) {
        $config = "lang_benchmark\configs\$kind-$pair.yaml"
        if (-not (Test-Path (Join-Path $Root $config))) { Write-Host "no config $config; skipped" -ForegroundColor Yellow; continue }
        Invoke-Benchmark "bench-$Round-$kind-$pair" $bookPath $config
        $jobs += "bench-$Round-$kind-$pair"
    }
}
Clear-OllamaModels

if ($jobs) {
    $env:PYTHONPATH = "$Root;$Bench"
    $results = Join-Path $Bench "results\$Round-results.md"
    $summary = Join-Path $Bench "results\$Round-summary.txt"
    # Both files cover every job of the round that exists, so a round run in several goes reports whole.
    $all = @(Get-ChildItem (Join-Path $Root "runs") -Directory -Filter "bench-$Round-*" |
        Where-Object { Test-Path (Join-Path $_.FullName "state.sqlite3") } | ForEach-Object { $_.Name })
    python lang_benchmark\evaluate.py @all | Out-File -Encoding utf8 $results
    python lang_benchmark\summarize.py @all | Out-File -Encoding utf8 $summary
    python lang_benchmark\inspect_runs.py @jobs
    Write-Host "`nDone ($(Get-Date -Format s))."
    Write-Host "Results: $results"
    Write-Host "Summary: $summary"
    Get-Content $summary -Encoding utf8 | ForEach-Object { Write-Host $_ }
} else {
    Write-Host "nothing was run"
}
