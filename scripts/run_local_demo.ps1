[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$ui = Join-Path $root "apps\ui\react_app"

function Get-Listener([int]$Port) {
    $line = netstat -ano -p tcp | Select-String (":$Port\s+.*LISTENING\s+(\d+)$") | Select-Object -First 1
    if ($null -eq $line) { return $null }
    $match = [regex]::Match($line.Line, "(\d+)$")
    $pid = $match.Groups[1].Value
    try { $command = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid").CommandLine } catch { $command = "<command line unavailable>" }
    return "port $Port is in use by PID $pid ($command)"
}

foreach ($port in 8000, 8001, 5173, 5174) {
    $listener = Get-Listener $port
    if ($null -ne $listener) { throw "Conflicting listener detected: $listener. Stop or isolate it before starting the canonical local demo." }
}
if (-not (Test-NetConnection 127.0.0.1 -Port 5432 -InformationLevel Quiet)) { throw "PostgreSQL is not reachable at 127.0.0.1:5432." }
try { $models = Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5 } catch { throw "Ollama is not reachable at 127.0.0.1:11434." }
if ($models.models.name -notcontains "qwen3.5:4b") { throw "Ollama model qwen3.5:4b is not installed. Run: ollama pull qwen3.5:4b" }
if (-not (Test-Path $python)) { throw "Virtual environment Python was not found at $python." }

Start-Process -FilePath $python -WorkingDirectory $root -ArgumentList "-m uvicorn pms_api.app:create_runtime_app --factory --app-dir apps/api/src --host 127.0.0.1 --port 8000"
$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    try { $apiHealth = Invoke-RestMethod "http://127.0.0.1:8000/health/runtime" -TimeoutSec 2 } catch { $apiHealth = $null }
} until ($null -ne $apiHealth -or (Get-Date) -gt $deadline)
if ($null -eq $apiHealth) { throw "FastAPI did not become ready on port 8000 within 30 seconds." }

Start-Process -FilePath "npm.cmd" -WorkingDirectory $ui -ArgumentList "run dev"
$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    try { $proxyHealth = Invoke-RestMethod "http://127.0.0.1:5173/health/runtime" -TimeoutSec 2 } catch { $proxyHealth = $null }
} until ($null -ne $proxyHealth -or (Get-Date) -gt $deadline)
if ($null -eq $proxyHealth) { throw "Vite did not proxy health on port 5173 within 30 seconds." }
if ($proxyHealth.runtime_id -ne $apiHealth.runtime_id) { throw "Vite proxy runtime mismatch: API $($apiHealth.runtime_id), proxy $($proxyHealth.runtime_id)." }
Write-Host "Demo ready: runtime $($apiHealth.runtime_id), model $($apiHealth.generation_model), fallback $($apiHealth.fallback_enabled)."
Start-Process "http://127.0.0.1:5173/assistant"
