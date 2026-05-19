# ANP Local Studio one-click startup for PowerShell.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot
Write-Host "========================================"
Write-Host " ANP Local Studio startup"
Write-Host "========================================"

$ForceInstall = ($args -contains "--reinstall")

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Error "Python was not found. Please install Python 3.11+ and add it to PATH."
  exit 1
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "[1/5] Creating virtual environment..."
  python -m venv .venv
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
  Write-Host "[1/5] Virtual environment already exists."
}

. ".venv\Scripts\Activate.ps1"

if ((Test-Path ".venv\.installed") -and -not $ForceInstall) {
  Write-Host "[2/5] Dependencies already installed. Use --reinstall to force reinstall."
} else {
  Write-Host "[2/5] Installing dependencies..."
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  Set-Content -Path ".venv\.installed" -Value "installed" -Encoding UTF8
}

if (Test-Path ".env") {
  Write-Host "[3/5] Loading .env..."
  Get-Content ".env" | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
      $idx = $line.IndexOf("=")
      if ($idx -gt 0) {
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()
        Set-Item -Path ("Env:" + $key) -Value $val
      }
    }
  }
} else {
  Write-Host "[3/5] No .env file found; skipping."
}

Write-Host "[4/5] Preparing folders and database..."
New-Item -ItemType Directory -Force -Path data, logs, logs\screenshots | Out-Null
python -c "from config_loader import load_from_environment; from review_queue.db import initialize_database; print('database', initialize_database(load_from_environment()))"
if ($LASTEXITCODE -ne 0) {
  Write-Error "Database initialization failed. Please check config.yaml."
  exit $LASTEXITCODE
}

if (-not $env:ANP_REVIEW_HOST) { $env:ANP_REVIEW_HOST = "127.0.0.1" }
if (-not $env:ANP_REVIEW_PORT) { $env:ANP_REVIEW_PORT = "18000" }

Write-Host "[4.5/5] Releasing port $($env:ANP_REVIEW_PORT)..."
$port = [int]$env:ANP_REVIEW_PORT
$listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
  foreach ($conn in $listeners) {
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "  Stopping PID=$($conn.OwningProcess) Name=$($proc.ProcessName)"
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 800
} else {
  Write-Host "  Port $port is free."
}

Write-Host "[5/5] Starting ANP Local Studio..."
Write-Host "URL: http://$env:ANP_REVIEW_HOST`:$env:ANP_REVIEW_PORT"

$pythonw = ".venv\Scripts\pythonw.exe"
if (Test-Path $pythonw) {
  Start-Process -FilePath $pythonw -ArgumentList @("tray_app.py", "--host", $env:ANP_REVIEW_HOST, "--port", $env:ANP_REVIEW_PORT) -WindowStyle Hidden
  Write-Host "Tray app started. Waiting for service health..."
  $ok = $false
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
      $url = "http://" + $env:ANP_REVIEW_HOST + ":" + $env:ANP_REVIEW_PORT + "/api/health"
      $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
      if ($resp.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
  }
  if ($ok) {
    Write-Host "OK: ANP is running in the tray/background."
  } else {
    Write-Warning "Service did not become reachable within 15 seconds. Check logs\tray.log and logs\uvicorn.log."
  }
} else {
  Write-Warning "$pythonw was not found; starting foreground server instead."
  python -m review_queue.human_review --host $env:ANP_REVIEW_HOST --port $env:ANP_REVIEW_PORT
}
