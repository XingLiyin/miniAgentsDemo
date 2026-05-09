# miniAgents 一键启动脚本 (PowerShell 版)
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$Root = $PSScriptRoot

# --- 确保 uv 可用，不存在则自动安装 ---
function Ensure-Uv {
    # 先检查 PATH 中是否有 uv
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) { return $uvCmd.Source }

    # 检查 uv 的默认安装位置
    $uvDefaultPath = "$env:USERPROFILE\.local\bin\uv.exe"
    if (Test-Path $uvDefaultPath) {
        # 加入当前会话的 PATH
        $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
        return $uvDefaultPath
    }

    Write-Host "  [!] 未找到 uv，正在自动安装..." -ForegroundColor Yellow
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Write-Host "  [错误] uv 安装失败: $_" -ForegroundColor Red
        exit 1
    }

    # 安装后刷新 PATH
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"

    if (-not (Test-Path $uvDefaultPath)) {
        Write-Host "  [错误] uv 安装后仍找不到可执行文件，请手动安装: https://docs.astral.sh/uv/" -ForegroundColor Red
        exit 1
    }

    Write-Host "  [√] uv 安装成功" -ForegroundColor Green
    return $uvDefaultPath
}

# --- 确保虚拟环境已构建 ---
function Ensure-Venv {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Host "  [!] 虚拟环境不存在，正在创建并安装依赖..." -ForegroundColor Yellow
        & uv sync --project $Root
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [错误] 虚拟环境构建失败" -ForegroundColor Red
            exit 1
        }
        Write-Host "  [√] 虚拟环境构建完成" -ForegroundColor Green
    }
    return $venvPython
}

# --- 启动后端 ---
function Start-Backend {
    Write-Host "`n==> 启动后端 (FastAPI :8000)" -ForegroundColor Cyan

    Ensure-Uv | Out-Null
    $pythonExe = Ensure-Venv

    $params = @{
        FilePath         = $pythonExe
        ArgumentList     = "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"
        WorkingDirectory = $Root
        NoNewWindow      = $false
        PassThru         = $true
    }
    return Start-Process @params
}

# --- 启动前端 ---
function Start-Frontend {
    $frontendDir = Join-Path $Root "frontend"
    Write-Host "`n==> 启动前端 (Vite :5173)" -ForegroundColor Cyan

    if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
        Write-Host "  [!] 首次运行，正在安装前端依赖..." -ForegroundColor Yellow
        Start-Process -FilePath "npm.cmd" -ArgumentList "install" -WorkingDirectory $frontendDir -NoNewWindow -Wait
        Write-Host "  [√] 前端依赖安装完成" -ForegroundColor Green
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

# --- 主逻辑 ---
$procs = @()

if (-not $FrontendOnly) { $procs += Start-Backend }
if (-not $BackendOnly)  { $procs += Start-Frontend }

Write-Host "`n==========================================" -ForegroundColor Green
Write-Host "  服务已启动！按 Ctrl+C 停止所有进程" -ForegroundColor Green
Write-Host "  后端文档: http://localhost:8000/docs"
Write-Host "  前端界面: http://localhost:5173"
Write-Host "==========================================" -ForegroundColor Green

# 维持主进程运行，Ctrl+C 时清理所有子进程
try {
    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    Write-Host "`n[!] 正在清理所有服务及其子进程..." -ForegroundColor Yellow
    foreach ($p in $procs) {
        if ($null -ne $p -and -not $p.HasExited) {
            Get-CimInstance Win32_Process |
                Where-Object { $_.ParentProcessId -eq $p.Id } |
                ForEach-Object {
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                    Write-Host "  [-] 已清理子进程: $($_.Name)" -ForegroundColor Gray
                }
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  [√] 已关闭主服务: $($p.ProcessName)" -ForegroundColor Gray
        }
    }
    Write-Host "[OK] 所有进程已彻底退出。" -ForegroundColor Green
}
