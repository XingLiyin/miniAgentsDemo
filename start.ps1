# miniAgents one-click startup script (PowerShell)
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$Root = $PSScriptRoot

# --- Ensure uv is available, install if missing ---
function Ensure-Uv {
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) { return $uvCmd.Source }

    $uvDefaultPath = "$env:USERPROFILE\.local\bin\uv.exe"
    if (Test-Path $uvDefaultPath) {
        $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
        return $uvDefaultPath
    }

    Write-Host "  [!] uv not found, installing..." -ForegroundColor Yellow
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Write-Host "  [ERROR] Failed to install uv: $_" -ForegroundColor Red
        exit 1
    }

    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"

    if (-not (Test-Path $uvDefaultPath)) {
        Write-Host "  [ERROR] uv not found after install. Please install manually: https://docs.astral.sh/uv/" -ForegroundColor Red
        exit 1
    }

    Write-Host "  [OK] uv installed successfully" -ForegroundColor Green
    return $uvDefaultPath
}

# --- Sync virtualenv (always run to ensure dependencies are up to date) ---
function Sync-Venv {
    Write-Host "  [*] Syncing virtualenv..." -ForegroundColor Gray
    & uv sync --project $Root
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] uv sync failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Virtualenv ready" -ForegroundColor Green
    return Join-Path $Root ".venv\Scripts\python.exe"
}

# --- Start backend ---
function Start-Backend {
    Write-Host "`n==> Starting backend (FastAPI :8000)" -ForegroundColor Cyan

    Ensure-Uv | Out-Null
    $pythonExe = Sync-Venv

    $params = @{
        FilePath         = $pythonExe
        ArgumentList     = "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"
        WorkingDirectory = $Root
        NoNewWindow      = $false
        PassThru         = $true
    }
    return Start-Process @params
}

# --- Start frontend ---
function Start-Frontend {
    $frontendDir = Join-Path $Root "frontend"
    Write-Host "`n==> Starting frontend (Vite :5173)" -ForegroundColor Cyan

    if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
        Write-Host "  [!] node_modules missing, installing dependencies..." -ForegroundColor Yellow
        Start-Process -FilePath "npm.cmd" -ArgumentList "install" -WorkingDirectory $frontendDir -NoNewWindow -Wait
        Write-Host "  [OK] Frontend dependencies installed" -ForegroundColor Green
    }

    $params = @{
        FilePath         = "npm.cmd"
        ArgumentList     = "run", "dev"
        WorkingDirectory = $frontendDir
        NoNewWindow      = $false
        PassThru         = $true
    }
    return Start-Process @params
}

# --- Main ---
$procs = @()

if (-not $FrontendOnly) { $procs += Start-Backend }
if (-not $BackendOnly)  { $procs += Start-Frontend }

Write-Host "`n==========================================" -ForegroundColor Green
Write-Host "  Services started! Press Ctrl+C to stop." -ForegroundColor Green
Write-Host "  Backend docs: http://localhost:8000/docs"
Write-Host "  Frontend:     http://localhost:5173"
Write-Host "==========================================" -ForegroundColor Green

try {
    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    Write-Host "`n[!] Stopping all services..." -ForegroundColor Yellow
    foreach ($p in $procs) {
        if ($null -ne $p -and -not $p.HasExited) {
            Get-CimInstance Win32_Process |
                Where-Object { $_.ParentProcessId -eq $p.Id } |
                ForEach-Object {
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                    Write-Host "  [-] Killed child process: $($_.Name)" -ForegroundColor Gray
                }
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  [OK] Stopped: $($p.ProcessName)" -ForegroundColor Gray
        }
    }
    Write-Host "[DONE] All processes terminated." -ForegroundColor Green
}
