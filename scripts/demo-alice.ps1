# Show-case demo: translate the bundled Alice EPUB end to end with LLM glossary approval.
# Usage (from the repo root):
#   .\scripts\demo-alice.ps1            # validate, dry-run, then run
#   .\scripts\demo-alice.ps1 -Resume    # continue an interrupted run
param([switch]$Resume)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Config = Join-Path $Root "configs\demo-alice.yaml"
$Source = Join-Path $Root "sample\Alice's Adventures in Wonderland by Lewis Carroll.epub"
$JobId = "demo-alice-en-zh"
$Job = Join-Path $Root "runs\$JobId"

function Invoke-Agent { python -m book_agent.cli @args; if ($LASTEXITCODE) { exit $LASTEXITCODE } }

if ($Resume) {
    Invoke-Agent resume $Job --plain
} else {
    Invoke-Agent config --file $Config
    Invoke-Agent run $Source --config $Config --job-id $JobId --dry-run
    Invoke-Agent run $Source --config $Config --job-id $JobId --plain
}
Invoke-Agent status $Job
Invoke-Agent report $Job
