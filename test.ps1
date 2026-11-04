param([string]$Python = (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'))

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (!(Test-Path -LiteralPath $Python)) {
    throw 'Python environment not found. Run ./setup.ps1 -SkipDoctor first.'
}
& $Python (Join-Path $PSScriptRoot 'scripts/run_portable_tests.py')
exit $LASTEXITCODE
