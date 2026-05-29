# IPMaster-Cowork — 一键打包 exe 脚本
# 用法: .\packaging\build_exe.ps1 [-GTK3Bin "C:\...\GTK3-Runtime Win64\bin"] [-SkipFrontend] [-SkipInstall]
param(
    [string]$GTK3Bin  = "C:\Program Files\GTK3-Runtime Win64\bin",
    [switch]$SkipFrontend,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root     = Split-Path $PSScriptRoot -Parent   # 项目根目录
$BuildDir = Join-Path $Root "build"
$DistDir  = Join-Path $BuildDir "dist\ipmaster-cowork"
$WorkDir  = Join-Path $BuildDir "work"

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK([string]$msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Err([string]$msg)  { Write-Host "  [ERROR] $msg" -ForegroundColor Red }

# ── 1. 检查 GTK3 ──────────────────────────────────────────────────────────────
Write-Step "检查 GTK3 依赖"
if (Test-Path $GTK3Bin) {
    $env:GTK3_BIN = $GTK3Bin
    Write-OK "GTK3_BIN = $GTK3Bin"
} else {
    Write-Host "  [WARN] GTK3 未找到: $GTK3Bin" -ForegroundColor Yellow
    Write-Host "         cairosvg 功能将不可用（其他功能不受影响）" -ForegroundColor Yellow
}

# ── 2. 构建前端 ───────────────────────────────────────────────────────────────
if (-not $SkipFrontend) {
    Write-Step "构建前端 (npm run build)"
    $frontendDir = Join-Path $Root "frontend"

    if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
        Write-Host "  安装前端依赖 (npm install)..."
        $r = Start-Process -FilePath "npm.cmd" -ArgumentList "install" `
                           -WorkingDirectory $frontendDir -NoNewWindow -Wait -PassThru
        if ($r.ExitCode -ne 0) { Write-Err "npm install 失败"; exit 1 }
    }

    $r = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "build" `
                       -WorkingDirectory $frontendDir -NoNewWindow -Wait -PassThru
    if ($r.ExitCode -ne 0) { Write-Err "前端构建失败"; exit 1 }
    Write-OK "前端已输出到 frontend/dist"
} else {
    Write-Host "  [跳过] 前端构建" -ForegroundColor Gray
    if (-not (Test-Path (Join-Path $Root "frontend\dist"))) {
        Write-Err "frontend/dist 不存在，请先构建前端或去掉 -SkipFrontend"; exit 1
    }
}

# ── 3. 安装/更新 Python 依赖 + PyInstaller ────────────────────────────────────
if (-not $SkipInstall) {
    Write-Step "同步 Python 依赖 (uv sync)"
    uv sync --project $Root
    if ($LASTEXITCODE -ne 0) { Write-Err "uv sync 失败"; exit 1 }

    Write-Step "安装 PyInstaller 与 setuptools"
    uv pip install setuptools pyinstaller
    if ($LASTEXITCODE -ne 0) { Write-Err "安装 PyInstaller 失败"; exit 1 }
    Write-OK "PyInstaller 已就绪"
} else {
    Write-Host "  [跳过] 依赖安装" -ForegroundColor Gray
}

# ── 4. 运行 PyInstaller ───────────────────────────────────────────────────────
Write-Step "PyInstaller 打包"
New-Item -ItemType Directory -Force $BuildDir | Out-Null
Set-Location $Root
uv run pyinstaller "$PSScriptRoot\ipmaster-cowork.spec" --noconfirm `
    --distpath "$BuildDir\dist" `
    --workpath "$BuildDir\work"
if ($LASTEXITCODE -ne 0) { Write-Err "PyInstaller 打包失败"; exit 1 }

# ── 5. 复制运行时文件到 exe 同级目录 ─────────────────────────────────────────
Write-Step "拷贝 resources/ 和配置示例"
Copy-Item (Join-Path $Root "resources") $DistDir -Recurse -Force
Write-OK "已将 resources/ 复制到 build\dist\ipmaster-cowork\"

Copy-Item (Join-Path $Root ".env.example") (Join-Path $DistDir ".env.example") -Force
Write-OK "已将 .env.example 复制到 build\dist\ipmaster-cowork\"

# ── 完成 ──────────────────────────────────────────────────────────────────────
Write-Host @"

==========================================
  打包完成！
  可执行目录: build\dist\ipmaster-cowork\
  启动方式:
    1. 将 .env.example 复制为 build\dist\ipmaster-cowork\.env 并填写配置
    2. 双击 build\dist\ipmaster-cowork\ipmaster-cowork.exe
       或: .\build\dist\ipmaster-cowork\ipmaster-cowork.exe
  默认地址: http://localhost:15926
==========================================
"@ -ForegroundColor Green
