#!/usr/bin/env bash
# 一键启动开发环境（macOS / Linux）：后端 uvicorn + 前端 vite，Ctrl+C 同时停止两者。
#   chmod +x scripts/dev.sh && ./scripts/dev.sh
#   BACKEND_PORT=8001 FRONTEND_PORT=5180 ./scripts/dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

command -v python >/dev/null || { echo "未找到 python，请先安装 Python 3.11+"; exit 1; }
command -v npm >/dev/null || { echo "未找到 npm，请先安装 Node.js 18+"; exit 1; }

python -c 'import fastapi' 2>/dev/null || { echo '[.] 安装后端依赖 ...'; (cd "$BACKEND" && python -m pip install -e '.[dev]'); }
[ -d "$FRONTEND/node_modules" ] || { echo '[.] 安装前端依赖 ...'; (cd "$FRONTEND" && npm install); }

[ -f "$BACKEND/.env" ] || { cp "$BACKEND/.env.example" "$BACKEND/.env"; echo '[+] 已生成 backend/.env（DIFY_MODE=mock）'; }
[ -f "$ROOT/data/app.db" ] || { echo '[.] 初始化种子数据 ...'; (cd "$BACKEND" && python scripts/seed.py); }

export BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
export VITE_PORT="$FRONTEND_PORT"

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo ""
echo "[+] 后端  http://127.0.0.1:${BACKEND_PORT}   文档 /docs   健康 /health"
echo "[+] 前端  http://localhost:${FRONTEND_PORT}   （/api 已代理到后端，SSE 可直连）"
echo "[+] 登录  owner@example.com / secret123"
echo ""

(cd "$BACKEND" && uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT") &
(cd "$FRONTEND" && npm run dev) &
wait
