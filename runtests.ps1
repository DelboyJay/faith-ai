<#
.SYNOPSIS
Run the FAITH Python quality checks and full test suite from PowerShell.

.DESCRIPTION
Uses FAITH_TEST_PYTHON when set, otherwise prefers the real Python found by
`where.exe python.exe`. The script runs Ruff checks first and then pytest. Any
extra arguments are passed through to pytest so targeted runs still work.

.PARAMETER SkipLint
Skip the Ruff lint check and run pytest only.

.PARAMETER PytestArgs
Additional arguments passed to pytest.
#>

[CmdletBinding()]
param(
    [switch]$SkipLint,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

function Resolve-PythonCommand {
    <#
    .SYNOPSIS
    Resolve the Python executable used for tests.

    .DESCRIPTION
    Honours FAITH_TEST_PYTHON first, then uses `where.exe python.exe` while
    ignoring the Windows Store launcher shim. The repository virtual
    environment is only used as a fallback because it may be incomplete.

    .OUTPUTS
    System.String
    #>

    if ($env:FAITH_TEST_PYTHON) {
        return $env:FAITH_TEST_PYTHON
    }

    $wherePython = @()
    try {
        $wherePython = @(where.exe python.exe 2>$null)
    }
    catch {
        $wherePython = @()
    }
    foreach ($candidate in $wherePython) {
        if ($candidate -and ($candidate -notlike "*\Microsoft\WindowsApps\python.exe") -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    $localPythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    $localPythonCandidates = @("Python313", "Python312", "Python311", "Python310") |
        ForEach-Object { Join-Path $localPythonRoot "$_\python.exe" }
    foreach ($candidate in $localPythonCandidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    throw "Could not find a usable Python executable. Set FAITH_TEST_PYTHON to the correct python.exe path and try again."
}

function Invoke-CheckedCommand {
    <#
    .SYNOPSIS
    Run one command and fail the script when it returns a non-zero exit code.

    .DESCRIPTION
    Keeps command execution readable while preserving the child process exit
    code for CI or manual PowerShell use.

    .PARAMETER FilePath
    Executable path or command name.

    .PARAMETER Arguments
    Arguments passed to the executable.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host ""
    Write-Host "> $FilePath $($Arguments -join ' ')" -ForegroundColor Cyan
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$python = Resolve-PythonCommand

if (-not $SkipLint) {
    Invoke-CheckedCommand -FilePath $python -Arguments @("-m", "ruff", "check", "src", "tests")
}

$pytestCommand = @("-m", "pytest", "-q")
if ($PytestArgs) {
    $pytestCommand += $PytestArgs
}
Invoke-CheckedCommand -FilePath $python -Arguments $pytestCommand

Write-Host ""
Write-Host "All requested checks passed." -ForegroundColor Green
