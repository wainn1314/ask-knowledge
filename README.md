# Ask Knowledge · AI 学习伙伴

把「自己的资料」变成**可问答、可自测、可追踪错题**的学习伙伴。混合架构：

- **Dify 负责 RAG/LLM 引擎**（分块、嵌入、混合检索、Rerank、问答生成、出题与判分 Workflow）
- **自建后端负责业务编排**（多租户与权限、上传与审核卡点、MinerU 解析、SSE 转发、会话/题目/错题归档、用量与审计）
- **自建前端**（Vue 3 + TS）：流式问答、上传进度、出题答题、错题本

> 设计文档见 `docs/`（01 方案 / 02 目录 / 03 数据库 / 04 Dify 编排 / 05 接口清单 / 06 任务拆分 / 07 验收）。
> 关键原则：**前端零 Dify 凭据**，所有 Dify 调用都在后端；`kb_ids`/`dataset_ids` 由后端按权限计算。

## 一、5 分钟跑起来（零外部依赖：SQLite + mock Dify）

**方式 A：一键脚本（推荐，Windows）**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

脚本会自动完成：依赖检查/安装 → 生成 `backend/.env` → 首次运行建种子数据 → 分别开窗启动后端与前端，
并打印地址与登录凭据。端口冲突时换端口即可（会自动把前端代理指向新后端端口）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 -BackendPort 8001 -FrontendPort 5180
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 -Foreground -SkipInstall   # 后端占当前窗口
```

**方式 A+：连「知识库管理」后端一起启动（三件套，推荐）**

`scripts/dev-all.ps1` 在 `dev.ps1` 的基础上多起一个 ask ai 阶段五后端（`:8080`）——
前端「知识库管理」页的 `/api/knowledge/*` 由它提供。默认假设「ask ai」目录与前端仓库同级，否则用 `-AskAiPath` 指定：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1
powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1 -DryRun    # 只做环境检查，不启动进程
powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1 -Stop      # 停掉这三个端口的服务
powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1 -BackendPort 8001 -AskAiPort 8081 -FrontendPort 5180
```

> 不想敲命令：**双击仓库根目录的 `start-all.cmd`** 即可（等价于上面第一条命令；参数同样可传，如 `start-all.cmd -Stop`、`start-all.cmd -DryRun`）。

启动前会检查三个端口是否被占用（Vite 是 `strictPort`，占用会直接报错而不是自动换端口）、
检查 ask ai `.env` 是否配了 `DIFY_DATASET_ID`，并自动把 `BACKEND_URL` / `ASK_AI_URL` / `VITE_PORT`
注入各自的窗口，所以前端代理无需手动改。三个服务各占一个终端窗口，关窗即停止。

**方式 B：手动两步（Windows / macOS / Linux 通用；`scripts/dev.sh` 是 bash 版一键脚本）**

```bash
# 1) 后端（工作目录 backend/；SQLite 与存储都用绝对路径 <仓库>/data，与 cwd 无关）
cd backend
python -m pip install -e ".[dev]"      # 或用现有环境：fastapi/uvicorn/sqlalchemy/pydantic-settings/PyJWT/httpx
cp .env.example .env                    # 默认 DIFY_MODE=mock（离线契约实现）、PARSER_MODE=builtin
python scripts/seed.py                  # 建租户 + owner@example.com/secret123 + 示例知识库（并自动解析入库）
uvicorn app.main:app --reload --port 8000
#   文档：http://127.0.0.1:8000/docs    健康：http://127.0.0.1:8000/health

# 2) 前端（另一个终端）
cd frontend
npm install
npm run dev                             # http://localhost:5173 （/api 代理到 http://127.0.0.1:8000）
```

登录：`owner@example.com` / `secret123`。此时即可完整演示：上传 → 解析/审核/入库进度 → 流式问答（带 [n] 引用）→
出题 → 答题判分 → 错题本 → 会话历史。

## 二、切到真实 Dify / PostgreSQL

```dotenv
# backend/.env
DIFY_MODE=http
DIFY_BASE_URL=http://127.0.0.1/v1        # 或 http://dify-api:5001/v1
DIFY_APP_API_KEY=app-xxx                 # App A：chat-qa（Chatflow，开启流式 + 引用 + 审核扩展点）
DIFY_QUIZ_WORKFLOW_API_KEY=app-quiz-xxx  # App B：workflow-quiz（出题，输出结构见 docs/04 §3）
DIFY_GRADE_WORKFLOW_API_KEY=app-grade-xxx# App C：workflow-quiz-grade（简答判分）
DIFY_DATASET_API_KEY=dataset-xxx         # 知识库 API
DIFY_APP_ID=xxx                          # 审核回调校验（可空）
DATABASE_URL=postgresql+psycopg://ak:ak@127.0.0.1:5432/ask_knowledge
PARSER_MODE=mineru_local                 # 扫描件/PDF 走 MinerU：MINERU_BASE_URL=http://127.0.0.1:8888
AUDIT_PROVIDER=aliyun                    # 生产内容安全（未装 SDK / 无凭据时自动回退本地规则并记 audit_log）
WORKER_INLINE=false                      # 生产单独起 worker：python -m app.workers.runner
```

Dify 侧应用创建步骤见 `dify/README.md`，Prompt 与校验规则见 `docs/04-Dify编排设计.md`。

## 三、核心链路

```
上传(POST /kbs/{id}/documents)
  → 硬校验（数量/大小/扩展名/文件头/上传审核）
  → 落盘 uploads/{tenant}/{kb}/{sha16}.{ext} + 入队 parse_task（202 立即返回）
  → worker：解析（builtin | MinerU）→ 解析后送审（命中则 blocked）→ 入库 Dify（轮询 indexing-status）→ ready
  → 前端 2s 轮询 /documents/{id}/status

问答(POST /kbs/{id}/chat/stream，SSE)
  → 输入审核 → Dify chat-messages(streaming) → 归一化事件 meta/sources/message(usage)/done
  → 归档 user/assistant 消息（含引用快照、token、耗时）→ 回答送审（标记复核）
```

## 四、仓库结构

```
├── backend/      FastAPI（app/core db models schemas api services workers integrations + tests + scripts/seed.py）
├── frontend/     Vue 3 + TS + Vite（api/ utils/sse.ts stores/ router/ layouts/ views/ components/）
├── dify/         Dify 应用与 Prompt 落地说明
├── docs/         01–07 设计文档（方案、目录、DB、Dify 编排、接口、任务、验收）
├── deploy/       nginx.conf（SSE 必须关闭缓冲）+ 生产环境变量清单（Docker/compose 待补）
├── scripts/      check_schema.py 等运维校验脚本
└── data/         运行时数据（uploads/ parsed/ app.db），不入库
```

## 五、测试与校验

```bash
cd backend
python -m pytest -q            # 44 个用例：认证/多租户隔离/上传校验/流水线/SSE/出题判分/审核/Dify HTTP 契约/解析与存储
python ../scripts/check_schema.py   # 校验 docs/03 DDL 与多租户约定（可选依赖 sqlglot）

cd ../frontend
npm run typecheck              # vue-tsc
npm run build                  # 产物 dist/
```

## 六、已知边界（MVP）

- **mock 模式的 Dify 状态在内存**：重启后端后 mock 里的 dataset/document 会清空；文档再次「重试」会
  自动重新入库（worker 已处理 Dify 侧文档失效的情况），或重跑 `python backend/scripts/seed.py`。
- `AUDIT_PROVIDER=aliyun` 需安装可选依赖 `pip install -e ".[aliyun]"`，否则自动回退本地词库规则。
- `PARSER_MODE=builtin` 对 PDF 需 `pip install -e ".[parse]"`（pypdf）；扫描件请切 MinerU。
- 未内置 Alembic 迁移（MVP 用 `create_all`），升级路径见 `docs/03-数据库设计.md §10`。
- 前端未引入 Pinia / Markdown 渲染库（减少依赖，Markdown 以等宽文本展示），差异见 `frontend/README.md`。

## 七、启动方式速查

| 场景 | 命令（工作目录见括号） |
|---|---|
| 开发一键起（后端+前端各一窗） | `powershell -ExecutionPolicy Bypass -File scripts/dev.ps1` |
| 开发一键起（bash） | `./scripts/dev.sh`（先 `chmod +x scripts/dev.sh`） |
| 换端口（自动改前端代理） | `scripts/dev.ps1 -BackendPort 8001 -FrontendPort 5180` |
| 后端占当前窗口、跳过安装/种子 | `scripts/dev.ps1 -Foreground -SkipInstall -SkipSeed` |
| 只起后端（backend/） | `uvicorn app.main:app --reload --port 8000` |
| 只起前端（frontend/） | `npm run dev`（需后端在 8000，或设 `$env:BACKEND_URL`） |
| 前端类型检查 / 产物（frontend/） | `npm run typecheck`、`npm run build`（产物 `dist/`，`npm run preview` 预览） |
| 生产 API（backend/） | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4`（`.env` 里 `WORKER_INLINE=false`） |
| 生产 Worker（backend/） | `python -m app.workers.runner`（可多副本；与 API 分离部署） |
| 初始化/重置种子数据（backend/） | `python scripts/seed.py`（幂等；`--no-doc` 只建库，`--email/--password` 改管理员） |
| 健康检查 | `curl http://127.0.0.1:8000/health`（含 Dify 可达性、解析器、审核、worker 模式） |

### 常见问题

| 现象 | 原因与处理 |
|---|---|
| `Address already in use` / 端口被占 | 用 `-BackendPort/-FrontendPort` 换端口；或 `Get-NetTCPConnection -LocalPort 8000` 找到占用进程 |
| 前端 502 / 登录 404 | 前端代理指向的后端没起来，或换了后端端口但没同步（用 `scripts/dev.ps1` 会自动同步；手动起时设 `BACKEND_URL`） |
| `npm install` 慢或超时 | `npm config set registry https://registry.npmmirror.com` 后重试 |
| `ModuleNotFoundError: fastapi` | `cd backend; python -m pip install -e ".[dev]"`（Python ≥ 3.11） |
| 登录返回 401 / 用户不存在 | 用的是 `<仓库>/data/app.db` 这一个库；删掉 `data/app.db` 后重跑 `python scripts/seed.py` |
| 文档一直 `pending`/`parsing` | worker 没跑：确认 `WORKER_INLINE=true`，或独立起 `python -m app.workers.runner` |
| 换了 `DIFY_MODE=mock` 重启后问答「无相关内容」 | mock 的知识库在内存，重启即清空：文档点「重试」会自动重新入库，或重跑 `seed.py` |
| 手改 `scripts/*.ps1` 后报「缺少 }」 | 该脚本必须以 **UTF-8 with BOM** 保存（Windows PowerShell 5.1 按 ANSI 解码无 BOM 的中文会吞字节） |
| 生产环境 SSE 变成一次性返回 | Nginx 需 `proxy_buffering off; proxy_read_timeout 3600s;`（见 `deploy/nginx.conf`） |

