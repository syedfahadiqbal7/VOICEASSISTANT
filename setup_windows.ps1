[CmdletBinding()]
param(
    [switch]$Development,
    [switch]$SkipOllamaCheck
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    & py -3.11 --version 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Python 3.11 (64-bit) is required. Install it from https://www.python.org/downloads/windows/ and rerun this script.'
    }
    $pythonCommand = 'py'
    $pythonArgs = @('-3.11')
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw 'Python 3.11 is not installed or not on PATH.' }
    & python --version
    $pythonCommand = 'python'
    $pythonArgs = @()
}

if (-not (Test-Path '.venv')) {
    & $pythonCommand @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python virtual environment.' }
}

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Failed to upgrade pip.' }

$requirements = if ($Development) { 'requirements-dev.txt' } else { 'requirements.txt' }
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed: $requirements" }

Write-Host ''
Write-Host 'Python dependencies installed successfully.' -ForegroundColor Green
if (-not $SkipOllamaCheck) {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Host 'Ollama CLI found. Ensure the Ollama service is running and the configured model is pulled.'
    } else {
        Write-Warning 'Ollama was not found. Install it from https://ollama.com/ and pull the model configured in config.py.'
    }
}
Write-Host 'Start the application with: .\.venv\Scripts\python.exe main.py'
