param(
    [string]$PythonExe = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"

Write-Host "[A1] L0 ruff check"
& $PythonExe -m ruff check agents game_workflows tools state web_api tests

Write-Host "[A1] Coverage run"
& $PythonExe -m pytest tests `
    --cov=agents `
    --cov=game_workflows `
    --cov=web_api `
    --cov=state `
    --cov=tools `
    --cov-branch `
    --cov-report=term-missing `
    --cov-report=xml `
    --cov-fail-under=85 `
    -q

Write-Host "[A1] Coverage gate passed (global line >= 85%)"
