# Installs both halves of the app.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> backend"
python -m venv backend\.venv
& backend\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& backend\.venv\Scripts\python.exe -m pip install --quiet -r backend\requirements-dev.txt

Write-Host "==> frontend"
npm install --prefix frontend --no-audit --no-fund

Write-Host ""
Write-Host "Done. Run .\dev.ps1, then open http://localhost:5173"
Write-Host "Optional: copy backend\.env.example to backend\.env and add API keys for live research."
