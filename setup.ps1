param(
    [switch]$SkipFfmpeg,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptRoot

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Ensure-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name is not available. $hint"
    }
}

Write-Step "Checking prerequisites"
Ensure-Command python "Install Python 3.10+ and add it to PATH."

$venvPython = Join-Path $scriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Step "Creating virtual environment (.venv)"
    python -m venv .venv
}

if (-not (Test-Path $venvPython)) {
    throw "Failed to create virtual environment."
}

Write-Step "Installing dependencies"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt
& $venvPython -m pip install -r requirements-dev.txt

if (-not $SkipFfmpeg) {
    Write-Step "Checking FFmpeg"
    $hasFfmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $hasFfprobe = Get-Command ffprobe -ErrorAction SilentlyContinue

    if (-not ($hasFfmpeg -and $hasFfprobe)) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Host "FFmpeg not found. Attempting installation via winget..." -ForegroundColor Yellow
            winget install Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Could not install FFmpeg automatically. Install it manually and restart terminal." -ForegroundColor Yellow
            } else {
                Write-Host "FFmpeg installation command completed. Restart terminal/Cursor so PATH refreshes." -ForegroundColor Green
            }
        } else {
            Write-Host "winget is not available. Install FFmpeg manually and ensure ffmpeg/ffprobe are in PATH." -ForegroundColor Yellow
        }
    } else {
        Write-Host "FFmpeg already available." -ForegroundColor Green
    }
}

if (-not $SkipTests) {
    Write-Step "Running tests"
    & $venvPython -m pytest
}

Write-Step "Setup complete"
Write-Host "Start bot with: .\start.bat" -ForegroundColor Green
