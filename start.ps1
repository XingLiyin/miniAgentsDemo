# miniAgents one-click start
# Usage: .\start.ps1 [-BackendOnly] [-FrontendOnly]

param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$Root = $PSScriptRoot

function Start-Backend {
    Write-Host ""
    Write-Host "==> Backend (FastAPI :8000)" -ForegroundColor Cyan
    $params = @{
        FilePath         = "uv"
        ArgumentList     = "run", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"
        WorkingDirectory = $Root
        PassThru         = $true
        NoNewWindow      = $true
    }
    return (Start-Process @params)
}

function Start-Frontend {
    Write-Host ""
    Write-Host "==> Frontend (Vite :5173)" -ForegroundColor Cyan
    $frontendDir = Join-Path $Root "frontend"
    if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
        Write-Host "  node_modules missing, running npm install..." -ForegroundColor Yellow
        Push-Location $frontendDir
        npm.cmd install
        Pop-Location
    }
    $params = @{
        FilePath         = "npm.cmd"
        ArgumentList     = "run", "dev"
        WorkingDirectory = $frontendDir
        PassThru         = $true
        NoNewWindow      = $true
    }
    return (Start-Process @params)
}

$procs = @()

if (-not $FrontendOnly) { $procs += Start-Backend }
if (-not $BackendOnly)  { $procs += Start-Frontend }

Write-Host ""
Write-Host "Services started. Press Ctrl+C to stop." -ForegroundColor Green
Write-Host "  Backend: http://localhost:8000/docs"   -ForegroundColor DarkGray
Write-Host "  Frontend: http://localhost:5173"        -ForegroundColor DarkGray
Write-Host ""

try {
    while ($true) {
        Start-Sleep -Seconds 5
    }
}
finally {
    Write-Host "Stopping all services..." -ForegroundColor Yellow
    foreach ($p in $procs) {
        if ($null -ne $p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "Done." -ForegroundColor Green
}
