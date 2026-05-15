# miniAgents — 打包成 Docker 镜像供 Linux 离线部署
# 用法: .\packaging\build_docker_image.ps1 [-Output miniagents-linux.tar]
param(
    [string]$Output = "miniagents-linux.tar",
    [string]$Tag    = "latest"
)

$ErrorActionPreference = "Stop"
$Root      = Split-Path $PSScriptRoot -Parent   # 项目根目录
$BuildDir  = Join-Path $Root "build"
$ImageName = "miniagents"
$FullTag   = "${ImageName}:${Tag}"

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK([string]$msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Err([string]$msg)  { Write-Host "  [ERROR] $msg" -ForegroundColor Red; exit 1 }

# ── 1. 检查 Docker ────────────────────────────────────────────────────────────
Write-Step "检查 Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "未找到 docker 命令，请安装 Docker Desktop"
}
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Err "Docker 未运行，请启动 Docker Desktop" }
Write-OK "Docker 已就绪"

# ── 2. 构建镜像 ───────────────────────────────────────────────────────────────
Write-Step "构建镜像 $FullTag（首次约 5-10 分钟，之后有缓存会很快）"
docker build -f "$PSScriptRoot\Dockerfile" -t $FullTag "$Root"
if ($LASTEXITCODE -ne 0) { Write-Err "镜像构建失败" }
Write-OK "镜像构建完成"

# ── 3. 导出为 tar ─────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force $BuildDir | Out-Null
$OutputPath = Join-Path $BuildDir $Output
Write-Step "导出镜像到 build\$Output"
docker save $FullTag -o $OutputPath
if ($LASTEXITCODE -ne 0) { Write-Err "镜像导出失败" }

$sizeMB = [math]::Round((Get-Item $OutputPath).Length / 1MB)
Write-OK "已保存: build\$Output ($sizeMB MB)"

# ── 完成 ──────────────────────────────────────────────────────────────────────
Write-Host @"

==========================================
  打包完成！

  将以下两个文件传到 Linux 目标机:
    build\$Output
    .env.example

  目标机操作（仅需安装 Docker，无需联网）:

    docker load -i miniagents-linux.tar
    cp .env.example .env
    # 编辑 .env 填写配置后:
    docker run -d \
      --name miniagents \
      -p 15926:15926 \
      --env-file .env \
      -v ./data:/miniagents/data \
      -v ./resources:/miniagents/resources \
      -v ./logs:/miniagents/logs \
      --restart unless-stopped \
      miniagents:latest

  访问: http://localhost:15926
==========================================
"@ -ForegroundColor Green
