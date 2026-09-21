<#
.SYNOPSIS
  一键启动开发环境：后端 FastAPI(uvicorn) + 前端 Vite，各自开一个终端窗口。

.DESCRIPTION
  1) 检查 python / npm 是否可用；
  2) 缺依赖才安装（后端 pip install -e ".[dev]"、前端 npm install）；
  3) backend/.env 不存在时自动从 .env.example 生成（默认 DIFY_MODE=mock，无需真实 Dify）；
  4) data/app.db 不存在时自动跑 scripts/seed.py（建租户 + owner@example.com + 示例知识库）；
  5) 启动后端与前端，并打印访问地址与登录凭据。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
.EXAMPLE
  # 端口被占用时换端口（会自动把前端代理指向新后端端口）
  powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 -BackendPort 8001 -FrontendPort 5180
.EXAMPLE
  # 跳过安装/种子，后端占当前窗口、前端单开窗口
  powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 -SkipInstall -SkipSeed -Foreground
#>
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$SkipInstall,
    [switch]$SkipSeed,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$envFile = Join-Path $backend '.env'
$dbFile = Join-Path $root 'data\app.db'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw '未找到 python，请先安装 Python 3.11+' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw '未找到 npm，请先安装 Node.js 18+' }

if (-not $SkipInstall) {
    python -c 'import fastapi' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[.] 安装后端依赖 ...' -ForegroundColor Cyan
        Push-Location $backend; python -m pip install -e '.[dev]'; Pop-Location
    }
    if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
        Write-Host '[.] 安装前端依赖 ...' -ForegroundColor Cyan
        Push-Location $frontend; npm install; Pop-Location
    }
}

if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $backend '.env.example') $envFile
    Write-Host '[+] 已生成 backend/.env（DIFY_MODE=mock，PARSER_MODE=builtin）' -ForegroundColor Green
}

if (-not $SkipSeed -and -not (Test-Path $dbFile)) {
    Write-Host '[.] 初始化种子数据 ...' -ForegroundColor Cyan
    Push-Location $backend; python scripts/seed.py; Pop-Location
}

$env:BACKEND_URL = "http://127.0.0.1:$BackendPort"
$env:VITE_PORT = "$FrontendPort"
$beCmd = "Set-Location '$backend'; uvicorn app.main:app --reload --host 127.0.0.1 --port $BackendPort"
$feCmd = "Set-Location '$frontend'; npm run dev"

Write-Host ''
Write-Host "[+] 后端  http://127.0.0.1:$BackendPort   文档 /docs   健康 /health" -ForegroundColor Green
Write-Host "[+] 前端  http://localhost:$FrontendPort   （/api 已代理到后端，SSE 可直连）" -ForegroundColor Green
Write-Host '[+] 登录  owner@example.com / secret123' -ForegroundColor Green
Write-Host '[i] 关闭对应终端窗口即停止服务（Ctrl+C 亦可）' -ForegroundColor DarkGray
Write-Host ''

if ($Foreground) {
    Start-Process -FilePath 'powershell' -ArgumentList "-NoExit -Command `"$feCmd`""
    Invoke-Expression $beCmd
} else {
    Start-Process -FilePath 'powershell' -ArgumentList "-NoExit -Command `"$beCmd`""
    Start-Process -FilePath 'powershell' -ArgumentList "-NoExit -Command `"$feCmd`""
}
