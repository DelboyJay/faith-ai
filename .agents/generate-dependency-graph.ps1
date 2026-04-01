$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Regenerating FAITH dependency graph from epic.yaml..."

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 (Join-Path $scriptDir "generate_dependency_graph.py")
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python (Join-Path $scriptDir "generate_dependency_graph.py")
}
else {
    Write-Host "Failed to generate graph. Check Python is installed and on PATH."
    exit 1
}

Write-Host "Done."

$svgPath = Join-Path $scriptDir "dependency-graph.svg"
if (Test-Path $svgPath) {
    Write-Host "Opening dependency-graph.svg..."
    Start-Process $svgPath
}
