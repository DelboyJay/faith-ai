param(
    [switch]$InstallPython = $true,
    [switch]$InstallDockerDesktop = $true
)

$ErrorActionPreference = "Stop"

function Test-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-FeatureState {
    param([Parameter(Mandatory = $true)][string]$FeatureName)
    try {
        return (Get-WindowsOptionalFeature -Online -FeatureName $FeatureName).State
    }
    catch {
        return "Unknown"
    }
}

if (-not (Test-Command winget)) {
    Write-Error "winget is required for setup.ps. Install App Installer from Microsoft Store and rerun."
}

if ($InstallPython) {
    if (Test-Command python) {
        Write-Host "Python already available on PATH."
    }
    else {
        Write-Host "Installing Python 3 via winget..."
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    }
}

if (-not $InstallDockerDesktop) {
    Write-Host "Skipping Docker Desktop installation by request."
    exit 0
}

if (Test-Command docker) {
    Write-Host "Docker already available on PATH."
    exit 0
}

$wslState = Get-FeatureState -FeatureName "Microsoft-Windows-Subsystem-Linux"
$hyperVState = Get-FeatureState -FeatureName "Microsoft-Hyper-V-All"
$vmPlatformState = Get-FeatureState -FeatureName "VirtualMachinePlatform"

$dockerDesktopSupported = @($wslState, $hyperVState, $vmPlatformState) -contains "Enabled"

if (-not $dockerDesktopSupported) {
    Write-Host "Docker Desktop cannot be installed in the current Windows configuration." -ForegroundColor Yellow
    Write-Host "WSL, Hyper-V, and VirtualMachinePlatform are all disabled."
    Write-Host "If you need to keep VirtualBox isolated from Hyper-V/WSL, run Docker Engine inside a Linux VM instead and use setup.sh there."
    exit 1
}

Write-Host "Installing Docker Desktop via winget..."
winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements

Write-Host "Docker Desktop installation completed. Start Docker Desktop once, accept the license, then verify with:"
Write-Host "  docker version"
Write-Host "  docker compose version"
