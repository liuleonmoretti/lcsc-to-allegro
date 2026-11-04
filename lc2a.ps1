$ErrorActionPreference='Stop'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& "$PSScriptRoot/.venv/Scripts/python.exe" "$PSScriptRoot/src/lc2a.py" @args
exit $LASTEXITCODE
