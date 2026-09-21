<#
.SYNOPSIS
  一键启动「三件套」开发环境：主后端 FastAPI(8000) + ask ai 知识库后端(8080) + 前端 Vite(5173)。

.DESCRIPTION
  与 scripts/dev.ps1 的区别：本脚本额外启动 ask ai 阶段五后端，
  因为前端「知识库管理」页的 /api/knowledge/* 由它提供（代理配置见 frontend/vite.config.ts）。
  流程：
  1) 检查 python / npm 是否可用；
  2) 缺依赖才安装（主后端 pip install -e ".[dev]"、ask ai pip install -r requirements.txt、前端 npm install）；
  3) backend/.env 不存在时从 .env.example 生成；检查 ask ai/.env 是否含 DIFY_DATASET_ID；
  4) data/app.db 不存在时自动跑 scripts/seed.py；
  5) 启动前检查三个端口是否被占用（Vite 是 strictPort，占用不会自动换端口，会直接报错）；
  6) 分别开三个终端窗口启动，并打印地址、登录凭据、停止方式。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1

.EXAMPLE
  # 端口被占用时换端口（前端会自动把代理指向新端口）
  powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1 -BackendPort 8001 -AskAiPort 8081 -FrontendPort 5180

.EXAMPLE
  # 只做环境检查、不启动进程
  powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1 -DryRun

.EXAMPLE
  # 停掉这三个端口的服务
  powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1 -Stop

.EXAMPLE
  # ask ai 后端不在同级目录时显式指定
  powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1 -AskAiPath 'D:\other\ask ai'
#>
param(
    [int]$BackendPort = 8000,
    [int]$AskAiPort = 8080,
    [int]$FrontendPort = 5173,
    [string]$AskAiPath = '',
    [switch]$SkipInstall,
    [switch]$SkipSeed,
    [switch]$DryRun,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
if ($AskAiPath) {
    $askAi = (Resolve-Path -LiteralPath $AskAiPath).Path
} else {
    # 默认与前端仓库同级：<父目录>/ask ai
    $askAi = Join-Path (Split-Path -Parent $root) 'ask ai'
}

function Info($m) { Write-Host "[.] $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "[+] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

# 返回占用指定端口的进程（没有则 $null）
function Get-PortOwner([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conn) { return $null }
    return Get-Process -Id ($conn | Select-Object -First 1).OwningProcess -ErrorAction SilentlyContinue
}

# ── 停止模式：按端口杀三个服务 ──────────────────────────────────────────
if ($Stop) {
    foreach ($port in @($BackendPort, $AskAiPort, $FrontendPort)) {
        $proc = Get-PortOwner $port
        if ($proc) {
            # /T 连子进程一起杀：uvicorn --reload（reloader 父进程持有端口 + 子进程服务）
            # 与 npm run dev（node 子进程）都有子进程继承端口，只杀父进程会留下孤儿占端口
            taskkill /PID $proc.Id /T /F 2>$null | Out-Null
            if (Get-PortOwner $port) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
            Ok "已停止 :$port（$($proc.ProcessName) PID $($proc.Id)，含子进程）"
        } else {
            Warn ":$port 没有进程在监听"
        }
    }
    exit 0
}

# ── 1. 环境检查 ────────────────────────────────────────────────────────
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Die '未找到 python，请先安装 Python 3.11+' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Die '未找到 npm，请先安装 Node.js 18+' }
if (-not (Test-Path (Join-Path $backend 'app\main.py'))) { Die "主后端目录不完整：$backend" }
if (-not (Test-Path (Join-Path $askAi 'main.py'))) {
    Die "未找到 ask ai 后端（$askAi）。请用 -AskAiPath 指定其目录（应包含 main.py）"
}

# ── 2. 依赖（缺才装） ──────────────────────────────────────────────────
if (-not $SkipInstall) {
    python -c 'import fastapi' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Info '安装主后端依赖 ...'
        Push-Location $backend; python -m pip install -e '.[dev]'; Pop-Location
    }
    python -c 'import fastapi, multipart' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Info '安装 ask ai 后端依赖 ...'
        Push-Location $askAi; python -m pip install -r requirements.txt; Pop-Location
    }
    if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
        Info '安装前端依赖 ...'
        Push-Location $frontend; npm install; Pop-Location
    }
}

# ── 3. 配置与种子数据 ──────────────────────────────────────────────────
$envFile = Join-Path $backend '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $backend '.env.example') $envFile
    Ok '已生成 backend/.env（DIFY_MODE=mock，PARSER_MODE=builtin）'
}
$askAiEnv = Join-Path $askAi '.env'
if (-not (Test-Path $askAiEnv)) {
    Warn "ask ai 缺少 $askAiEnv —— 知识库接口需要其中的 DIFY_BASE_URL / DIFY_DATASET_API_KEY / DIFY_DATASET_ID"
} elseif (Select-String -Path $askAiEnv -Pattern '^DIFY_DATASET_ID=.+' -Quiet) {
    Ok 'ask ai/.env 已就绪（含 DIFY_DATASET_ID）'
} else {
    Warn 'ask ai/.env 未配置 DIFY_DATASET_ID，知识库管理接口会调用失败'
}

$dbFile = Join-Path $root 'data\app.db'
if (-not $SkipSeed -and -not (Test-Path $dbFile)) {
    Info '初始化种子数据 ...'
    Push-Location $backend; python scripts/seed.py; Pop-Location
}

# ── 4. 端口检查（Vite strictPort：占用即报错） ──────────────────────────
$conflicts = @()
foreach ($item in @(
        @{ Name = '主后端'; Port = $BackendPort },
        @{ Name = 'ask ai 后端'; Port = $AskAiPort },
        @{ Name = '前端'; Port = $FrontendPort })) {
    $proc = Get-PortOwner $item.Port
    if ($proc) { $conflicts += "    :$($item.Port)（$($item.Name)）被 $($proc.ProcessName) 占用，PID $($proc.Id)" }
}
if ($conflicts.Count -gt 0) {
    Warn '以下端口已被占用，直接启动会失败：'
    $conflicts | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
    Warn '处理方式：① 停掉它们 scripts/dev-all.ps1 -Stop   ② 换端口 -BackendPort 8001 -AskAiPort 8081 -FrontendPort 5180'
    if (-not $DryRun) { exit 1 }
}

# ── 5. 启动 ────────────────────────────────────────────────────────────
$beCmd = "Set-Location '$backend'; uvicorn app.main:app --reload --host 127.0.0.1 --port $BackendPort"
$aiCmd = "Set-Location '$askAi'; uvicorn main:app --reload --host 0.0.0.0 --port $AskAiPort"
$feCmd = "Set-Location '$frontend'; `$env:BACKEND_URL='http://127.0.0.1:$BackendPort'; " +
    "`$env:ASK_AI_URL='http://127.0.0.1:$AskAiPort'; `$env:VITE_PORT='$FrontendPort'; npm run dev"

if ($DryRun) {
    Info 'DryRun：只做检查，不启动进程。将要执行的命令：'
    Write-Host "    主后端   $beCmd"
    Write-Host "    ask ai   $aiCmd"
    Write-Host "    前端     $feCmd"
    Ok '环境检查通过'
    exit 0
}

Write-Host ''
Ok "主后端    http://127.0.0.1:$BackendPort   文档 /docs   健康 /health"
Ok "ask ai    http://127.0.0.1:$AskAiPort   文档 /docs   （知识库管理：/api/knowledge/*）"
Ok "前端      http://localhost:$FrontendPort   知识库管理页 http://localhost:$FrontendPort/#/knowledge"
Ok '登录      owner@example.com / secret123'
Write-Host '[i] 三个服务各占一个窗口，关窗即停止；全部停止：scripts/dev-all.ps1 -Stop' -ForegroundColor DarkGray
Write-Host ''

Start-Process -FilePath 'powershell' -ArgumentList "-NoExit -Command `"$beCmd`""
Start-Process -FilePath 'powershell' -ArgumentList "-NoExit -Command `"$aiCmd`""
Start-Process -FilePath 'powershell' -ArgumentList "-NoExit -Command `"$feCmd`""
