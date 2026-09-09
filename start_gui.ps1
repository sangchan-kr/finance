$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$pythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    uv sync
}

Start-Process `
    -FilePath $pythonw `
    -ArgumentList "-m", "kis_trader.gui" `
    -WorkingDirectory $PSScriptRoot
