# Model runs for the file-format feature (docs/FORMAT_SUPPORT.md): the sources and subtitle
# targets no model has been run on yet. Run from the repository root in your own terminal;
# long runs started from an assistant session get stopped.
#
#   powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-format-checks.ps1
#   powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-format-checks.ps1 fmt-md-en-de sub-srt-en-ja
#
# With no names, every run below, in order. A job that exists is resumed from its checkpoints,
# so the script can be stopped and started again at any point. Close other GPU-heavy
# applications first (docs/OPERATIONS.md, "GPU memory").
# Logs: runs\bench-logs\<job>.log. Summary: runs\bench-logs\format-checks-summary.txt.
[CmdletBinding(PositionalBinding = $false)]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Only)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Logs = Join-Path $Root "runs\bench-logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

# Job -> source, config, and what the run is for.
$Runs = [ordered]@{
    "fmt-word-notes-en-de" = @("tests\data\word-pictures-and-notes.docx", "lang_benchmark\configs\tg-en-de.yaml",
        "Word with a picture, a footnote and an endnote: notes and description through the model")
    "fmt-md-en-de"         = @("lang_benchmark\books\alice-tea-party.md", "lang_benchmark\configs\tg-en-de.yaml",
        "Markdown source")
    "fmt-html-en-zh"       = @("lang_benchmark\books\alice-tea-party.html", "lang_benchmark\configs\qwen-en-zh.yaml",
        "HTML source")
    "fmt-txt-en-fr"        = @("lang_benchmark\books\alice-tea-party.txt", "lang_benchmark\configs\tg-en-fr.yaml",
        "plain text source")
    "sub-srt-en-ja"        = @("lang_benchmark\books\alice-tea-party.srt", "lang_benchmark\configs\tg-en-ja.yaml",
        "subtitles into Japanese: reading limits on real output")
    "sub-vtt-en-ko"        = @("lang_benchmark\books\alice-tea-party.vtt", "lang_benchmark\configs\tg-en-ko.yaml",
        "subtitles into Korean: reading limits on real output")
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

function Invoke-Check([string]$JobId, [string]$Source, [string]$Config, [string]$Purpose) {
    $job = Join-Path $Root "runs\$JobId"
    $log = Join-Path $Logs "$JobId.log"
    Clear-OllamaModels
    Write-Host "`n=== $JobId ($(Get-Date -Format s)): $Purpose ===" -ForegroundColor Cyan
    Write-Host "log: $log"
    if (Test-Path (Join-Path $job "state.sqlite3")) {
        $arguments = @("-m", "book_agent.cli", "resume", $job, "--plain")
    } else {
        $arguments = @("-m", "book_agent.cli", "run", $Source, "--config", $Config, "--job-id", $JobId, "--plain")
    }
    $started = Get-Date
    # Every line goes to the terminal as it arrives and to the log. "$_" turns the
    # stderr records Windows PowerShell wraps around native output back into text;
    # Out-File keeps the log UTF-8 (Tee-Object would write UTF-16 here).
    & python @arguments 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        $line
    } | Out-File -FilePath $log -Append -Encoding utf8
    $code = $LASTEXITCODE
    $minutes = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
    # Exit code 2 means paused for final human review: translation, audit, repair and validation are done.
    $outcome = switch ($code) { 0 { "complete" } 2 { "paused for final review" } default { "failed (exit $code)" } }
    Write-Host "$JobId $outcome after $minutes min" -ForegroundColor Cyan
    return [pscustomobject]@{ Job = $JobId; Outcome = $outcome; Minutes = $minutes; Purpose = $Purpose }
}

$env:PYTHONIOENCODING = "utf-8"
# Read Python's UTF-8 output as UTF-8, so the log is not garbled.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
# Print progress lines as they happen instead of in buffered blocks.
$env:PYTHONUNBUFFERED = "1"

# A mistyped option lands among the names; refuse it instead of running the jobs beside it.
$stray = @($Only | Where-Object { $_ -like "-*" })
if ($stray) { Write-Host "unknown option $($stray -join ', '); nothing was run" -ForegroundColor Red; exit 1 }
$names = if ($Only) { $Only } else { @($Runs.Keys) }
$unknown = @($names | Where-Object { -not $Runs.Contains($_) })
if ($unknown) { Write-Host "unknown run $($unknown -join ', ') (known: $($Runs.Keys -join ', ')); nothing was run" -ForegroundColor Red; exit 1 }

$results = @()
$began = Get-Date
foreach ($name in $names) {
    $source, $config, $purpose = $Runs[$name]
    if (-not (Test-Path (Join-Path $Root $source))) {
        Write-Host "no source $source; $name skipped (python lang_benchmark\make_format_samples.py writes the alice-tea-party files)" -ForegroundColor Red
        $results += [pscustomobject]@{ Job = $name; Outcome = "skipped: no source"; Minutes = 0; Purpose = $purpose }
        continue
    }
    if (-not (Test-Path (Join-Path $Root $config))) {
        Write-Host "no config $config; $name skipped" -ForegroundColor Red
        $results += [pscustomobject]@{ Job = $name; Outcome = "skipped: no config"; Minutes = 0; Purpose = $purpose }
        continue
    }
    $results += Invoke-Check $name $source $config $purpose
}
Clear-OllamaModels

$summary = Join-Path $Logs "format-checks-summary.txt"
$table = $results | Format-Table -AutoSize -Wrap | Out-String -Width 200
"Format checks, $(Get-Date -Format s) (began $(Get-Date $began -Format s))`n$table" | Out-File -Encoding utf8 $summary
Write-Host "`nDone ($(Get-Date -Format s))." -ForegroundColor Cyan
Write-Host $table
Write-Host "Summary: $summary"
