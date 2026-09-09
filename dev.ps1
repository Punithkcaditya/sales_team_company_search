# Runs the API on :8000 and the UI on :5173. Ctrl-C stops both.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path backend\.venv\Scripts\python.exe)) { throw "Run .\setup.ps1 first." }

$api = Start-Process -PassThru -NoNewWindow backend\.venv\Scripts\python.exe `
  @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--port", "8000", "--reload")
try {
  npm run dev --prefix frontend
} finally {
  if (-not $api.HasExited) { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
}
