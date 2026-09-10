# Runs the API and the UI. Ctrl-C stops both.
# $env:API_PORT overrides the default when something else already holds 8000.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path backend\.venv\Scripts\python.exe)) { throw "Run .\setup.ps1 first." }

$port = if ($env:API_PORT) { $env:API_PORT } else { "8000" }
$env:VITE_API_TARGET = "http://127.0.0.1:$port"

# Probing the port is not enough: Windows keeps a socket listed under a dead
# process for a while, which looks busy but binds fine. And .NET's TcpListener
# is stricter than Python's socket, so the only honest test is the interpreter
# that will actually serve.
$probe = @"
import socket, sys
s = socket.socket()
try:
    s.bind(('127.0.0.1', $port))
except OSError:
    sys.exit(1)
finally:
    s.close()
"@
$probe | & backend\.venv\Scripts\python.exe - 2>$null
if ($LASTEXITCODE -ne 0) {
  throw "Port $port is in use by another program. Start on another one with:  `$env:API_PORT=8030; .\dev.ps1"
}

Write-Host "API on :$port - the UI port is printed by Vite below"
$api = Start-Process -PassThru -NoNewWindow backend\.venv\Scripts\python.exe `
  @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--port", $port, "--reload")
try {
  npm run dev --prefix frontend
} finally {
  if (-not $api.HasExited) { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
}
