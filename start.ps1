# miniAgents 一键启动脚本 (PowerShell 版)
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$Root = $PSScriptRoot

# --- 功能函数：启动后端 ---
# function Start-Backend {
#     Write-Host "`n==> 启动后端 (FastAPI :8000)" -ForegroundColor Cyan
#     # 使用 Start-Process 开启新窗口，这样日志不会打架，且方便调试
#     $params = @{
#         FilePath         = "uv"
#         ArgumentList     = "run", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"
#         WorkingDirectory = $Root
#         # 如果你确实想在同一个窗口看，就改回 $true，但建议新窗口
#         NoNewWindow      = $false 
#         PassThru         = $true
#     }
#     return Start-Process @params
# }

function Start-Backend {
    Write-Host "`n==> 启动后端 (FastAPI :8000)" -ForegroundColor Cyan
    
    # 拼接虚拟环境解释器的绝对路径
    $pythonExe = Join-Path $Root ".venv\Scripts\python.exe"
    
    if (-not (Test-Path $pythonExe)) {
        Write-Host "  [错误] 找不到虚拟环境: $pythonExe" -ForegroundColor Red
        return $null
    }

    $params = @{
        # 直接启动 python.exe
        FilePath         = $pythonExe
        # 使用 -m 运行模块
        ArgumentList     = "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"
        WorkingDirectory = $Root
        NoNewWindow      = $false 
        PassThru         = $true
    }
    
    return Start-Process @params
}

# --- 功能函数：启动前端 ---
function Start-Frontend {
    $frontendDir = Join-Path $Root "frontend"
    Write-Host "`n==> 启动前端 (Vite :5173)" -ForegroundColor Cyan
    
    if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
        Write-Host "  [!] node_modules 缺失，正在安装依赖..." -ForegroundColor Yellow
        Start-Process -FilePath "npm.cmd" -ArgumentList "install" -WorkingDirectory $frontendDir -Wait
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

# 维持主进程运行
try {
    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    Write-Host "`n[!] 正在深度清理所有服务及其子进程..." -ForegroundColor Yellow
    foreach ($p in $procs) {
        if ($null -ne $p -and -not $p.HasExited) {
            # 这里的核心逻辑：通过父进程 ID 寻找所有子进程并强制终止
            Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $p.Id } | ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                Write-Host "  [-] 已清理子进程: $($_.Name)" -ForegroundColor Gray
            }
            # 杀掉主进程
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  [√] 已关闭主服务: $($p.ProcessName)" -ForegroundColor Gray
        }
    }
    
    Write-Host "[OK] 所有进程已彻底强制退出。" -ForegroundColor Green
}