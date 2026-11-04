param([switch]$SkipDoctor)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$configPath = Join-Path $PSScriptRoot 'config.json'

if (!(Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'config.example.json') -Destination $configPath
    Write-Host 'Created config.json. Update the Cadence paths for your computer.'
}
if (!(Test-Path -LiteralPath $pythonPath)) {
    python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed. Check python --version.' }
}
& $pythonPath -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (!$SkipDoctor) {
    & $pythonPath (Join-Path $PSScriptRoot 'src/lc2a.py') doctor
    if ($LASTEXITCODE -ne 0) {
        throw 'Doctor failed. Edit config.json and run ./lc2a.ps1 doctor again. See docs/installation.md.'
    }
}
Write-Host 'Python dependencies installed.'
