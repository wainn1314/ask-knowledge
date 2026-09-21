# 部署（deploy/）

本目录放**生产/联调环境**的落地配置。当前只提交了 `nginx.conf`（已在本机验证过语义与位置约定，
但**未在真实 Nginx 上做过语法校验**：本机无 Nginx/Docker），Docker 镜像与 `docker-compose.yml` 待补。

## 1. 生产启动（不用容器，最小可行）

```bash
# 后端 API（多进程；SQLite 请保持 1 个 worker，或直接用 PostgreSQL）
cd backend
export WORKER_INLINE=false          # worker 独立进程部署，避免 reload/多副本重复消费
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# 任务 Worker（可多副本，SQLite 不建议 >1）
cd backend
python -m app.workers.runner

# 前端产物
cd frontend
npm ci && npm run build             # 产物 frontend/dist → 交给 Nginx 的 root
```

## 2. Nginx 反代

把 `frontend/dist` 放到 Nginx 的 `root`（默认 `/usr/share/nginx/html`），
`nginx.conf` 放到 `/etc/nginx/conf.d/default.conf`，然后：

```bash
nginx -t && nginx -s reload
```

**最容易踩的坑**：`/chat/stream` 是 SSE，必须 `proxy_buffering off` + 长 `proxy_read_timeout`，
否则前端会「等答案全部生成完才一次性显示」。本仓库 `nginx.conf` 已单独为 SSE 路由做处理。

## 3. 生产必须改的环境变量

```dotenv
APP_ENV=prod
APP_SECRET_KEY=<32 字节以上随机串>          # 必改
DATABASE_URL=postgresql+psycopg://ak:***@db:5432/ask_knowledge
DIFY_MODE=http
DIFY_BASE_URL=http://dify-api:5001/v1
DIFY_APP_API_KEY=app-***                    # App A chat-qa
DIFY_QUIZ_WORKFLOW_API_KEY=app-***          # App B workflow-quiz
DIFY_GRADE_WORKFLOW_API_KEY=app-***         # App C workflow-quiz-grade
DIFY_DATASET_API_KEY=dataset-***
PARSER_MODE=mineru_local
MINERU_BASE_URL=http://mineru:8888
AUDIT_PROVIDER=aliyun                       # 或 local（弱一些）
ALIYUN_ACCESS_KEY_ID=***
ALIYUN_ACCESS_KEY_SECRET=***
MODERATION_CALLBACK_TOKEN=<随机串，与 Dify 审核扩展配置一致>   # 必改
WORKER_INLINE=false
```

> CORS：`app/main.py` 里 MVP 放开了 `allow_origins=["*"]`，生产请改成前端域名白名单。

## 4. 待补（尚未提交，勿依赖）

- `Dockerfile.backend` / `Dockerfile.frontend`
- `docker-compose.yml`（backend + worker + postgres + nginx，可选 dify-api 复用现有实例）
- PostgreSQL 下的 Alembic 迁移（当前 MVP 用 `create_all`，见 `docs/03-数据库设计.md §10`）
