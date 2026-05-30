<#
.SYNOPSIS
  IPMaster-Cowork — 完整桌面应用打包脚本 (Electron + PyInstaller)
.DESCRIPTION
  输出: build\electron-dist\IPMaster-Cowork Setup <version>.exe  (NSIS 安装包)
        build\electron-dist\IPMaster-Cowork <version>.exe         (免安装便携版)
.PARAMETER GTK3Bin
  GTK3 Runtime bin 目录（用于 cairosvg）。找不到时跳过，不影响其他功能。
.PARAMETER SkipFrontend
  跳过桌面前端构建（需要 frontend-desktop/dist 已存在）。
.PARAMETER SkipBackend
  跳过 PyInstaller 打包（需要 build/dist/ipmaster-cowork 已存在）。
.PARAMETER SkipInstall
  跳过 uv sync / PyInstaller 安装步骤。
.EXAMPLE
  .\packaging\build_electron.ps1
  .\packaging\build_electron.ps1 -SkipFrontend -SkipBackend   # 只重新打 Electron
#>
param(
  [string]$GTK3Bin   = "C:\Program Files\GTK3-Runtime Win64\bin",
  [switch]$SkipFrontend,
  [switch]$SkipBackend,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root     = Split-Path $PSScriptRoot -Parent
$BuildDir = Join-Path $Root "build"

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK([string]$msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Err([string]$msg)  { Write-Host "  [ERROR] $msg" -ForegroundColor Red; exit 1 }
function Write-Warn([string]$msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }

# ── 1. 构建桌面前端 ──────────────────────────────────────────────────────────
if (-not $SkipFrontend) {
  Write-Step "构建前端 (npm run build in frontend-desktop/)"
  $frontendDir = Join-Path $Root "frontend-desktop"

  if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Host "  安装前端依赖 (npm install)..."
    $r = Start-Process npm.cmd -ArgumentList "install" -WorkingDirectory $frontendDir -NoNewWindow -Wait -PassThru
    if ($r.ExitCode -ne 0) { Write-Err "npm install 失败" }
  }

  $r = Start-Process npm.cmd -ArgumentList "run","build" -WorkingDirectory $frontendDir -NoNewWindow -Wait -PassThru
  if ($r.ExitCode -ne 0) { Write-Err "桌面前端构建失败" }
  Write-OK "前端已输出到 frontend-desktop/dist"
} else {
  if (-not (Test-Path (Join-Path $Root "frontend-desktop\dist"))) {
    Write-Err "frontend-desktop/dist 不存在，请去掉 -SkipFrontend 或先手动构建"
  }
  Write-Warn "跳过前端构建"
}

# ── 2. 打包 Python 后端 ───────────────────────────────────────────────────────
if (-not $SkipBackend) {
  Write-Step "打包 Python 后端 (PyInstaller)"

  # 检查 GTK3
  if (Test-Path $GTK3Bin) {
    $env:GTK3_BIN = $GTK3Bin
    Write-OK "GTK3_BIN = $GTK3Bin"
  } else {
    Write-Warn "GTK3 未找到: $GTK3Bin  (cairosvg 不可用，其他功能正常)"
  }

  if (-not $SkipInstall) {
    uv sync --project $Root
    if ($LASTEXITCODE -ne 0) { Write-Err "uv sync 失败" }
    uv pip install setuptools pyinstaller
    if ($LASTEXITCODE -ne 0) { Write-Err "安装 PyInstaller 失败" }
    Write-OK "Python 依赖就绪"
  }

  New-Item -ItemType Directory -Force $BuildDir | Out-Null
  Set-Location $Root
  uv run pyinstaller "$PSScriptRoot\ipmaster-cowork-desktop.spec" --noconfirm `
    --distpath "$BuildDir\dist" `
    --workpath "$BuildDir\work"
  if ($LASTEXITCODE -ne 0) { Write-Err "PyInstaller 打包失败" }

  # 复制运行时资源到 exe 同级（与 build_exe.ps1 一致）
  $DistDir = Join-Path $BuildDir "dist\ipmaster-cowork"
  Copy-Item (Join-Path $Root "resources") $DistDir -Recurse -Force
  Copy-Item (Join-Path $Root ".env.example") (Join-Path $DistDir ".env.example") -Force
  Write-OK "后端已输出到 build/dist/ipmaster-cowork/"
} else {
  if (-not (Test-Path (Join-Path $BuildDir "dist\ipmaster-cowork\ipmaster-cowork.exe"))) {
    Write-Err "build/dist/ipmaster-cowork/ipmaster-cowork.exe 不存在，请去掉 -SkipBackend 或先手动构建后端"
  }
  Write-Warn "跳过后端打包"
}

# ── 3. 修复 winCodeSign 缓存（符号链接权限问题）────────────────────────────────
# electron-builder 下载的 winCodeSign 包含 macOS 符号链接，非 Admin 无法创建。
# 提前建好目标目录并补空占位文件，让 electron-builder 直接使用缓存。
Write-Step "检查 winCodeSign 缓存"
$wcsDir   = Join-Path $env:LOCALAPPDATA "electron-builder\Cache\winCodeSign"
$wcsCache = Join-Path $wcsDir "winCodeSign-2.6.0"
if (-not (Test-Path $wcsCache)) {
  Write-Host "  winCodeSign 缓存不存在，尝试从现有提取目录修复..."
  $tmpDir = Get-ChildItem $wcsDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d+$' } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
  if ($tmpDir) {
    # 补充缺失的 macOS 符号链接占位文件
    $libDir = Join-Path $tmpDir.FullName "darwin\10.12\lib"
    New-Item -ItemType Directory -Force -Path $libDir | Out-Null
    foreach ($f in @("libcrypto.dylib","libssl.dylib")) {
      $fp = Join-Path $libDir $f
      if (-not (Test-Path $fp)) { New-Item -ItemType File -Force -Path $fp | Out-Null }
    }
    Rename-Item -Path $tmpDir.FullName -NewName "winCodeSign-2.6.0"
    # 清理其余残留
    Get-ChildItem $wcsDir -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -ne "winCodeSign-2.6.0" } |
      Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-OK "winCodeSign 缓存已修复"
  } else {
    Write-Warn "winCodeSign 缓存不存在，首次 electron-builder 运行时将自动下载（如失败请以管理员身份运行，或开启 Windows 开发者模式）"
  }
} else {
  Write-OK "winCodeSign 缓存已就绪"
}

# ── 4. 生成应用图标 (SVG → ICO) ──────────────────────────────────────────────
Write-Step "生成应用图标"
$iconSvg = Join-Path $Root "resources\brand\icon.svg"
$iconIco = Join-Path $Root "electron\assets\icon.ico"
New-Item -ItemType Directory -Force (Join-Path $Root "electron\assets") | Out-Null

uv run --project $Root python "$PSScriptRoot\gen_icon.py"
if ($LASTEXITCODE -ne 0) {
  Write-Warn "图标生成失败，将使用 Electron 默认图标"
} else {
  Write-OK "图标已生成: $iconIco"
}

# ── 5. 构建 Electron 应用 ─────────────────────────────────────────────────────
Write-Step "构建 Electron 桌面应用 (electron-builder)"
$electronDir = Join-Path $Root "electron"

Write-Host "  安装 Electron 依赖 (npm install)..."
$r = Start-Process npm.cmd -ArgumentList "install" -WorkingDirectory $electronDir -NoNewWindow -Wait -PassThru
if ($r.ExitCode -ne 0) { Write-Err "electron npm install 失败" }

$r = Start-Process npm.cmd -ArgumentList "run","build" -WorkingDirectory $electronDir -NoNewWindow -Wait -PassThru
if ($r.ExitCode -ne 0) { Write-Err "electron-builder 打包失败" }

Write-OK "Electron 应用已输出到 build/electron-dist/"

# ── 完成 ──────────────────────────────────────────────────────────────────────
$outputDir = Join-Path $BuildDir "electron-dist"
Write-Host @"

==========================================
  打包完成！
  输出目录: build\electron-dist\
  安装包:   IPMaster-Cowork Setup *.exe
  便携版:   IPMaster-Cowork *.exe

  首次运行配置：
    数据目录自动创建于 %APPDATA%\IPMaster-Cowork\
    LLM 配置文件：%APPDATA%\IPMaster-Cowork\.env
    启动后通过前端界面添加 LLM Provider 即可使用。
==========================================
"@ -ForegroundColor Green
