# 前端（frontend/）

Vue 3 + TypeScript + Vite，零额外 UI 依赖（不用 Element Plus，样式在 `src/styles.css`）。
所有请求经 `src/api/http.ts`，流式问答只经 `src/utils/sse.ts`（`fetch` + `ReadableStream`，
因为 `EventSource` 不支持 POST 与自定义头）。

## 快速开始

```bash
# 1) 先启动后端（另一个终端，工作目录 backend/）
cd backend
cp .env.example .env          # DIFY_MODE=mock 即可离线跑通
python scripts/seed.py        # 建默认租户 + owner@example.com/secret123 + 示例知识库
uvicorn app.main:app --reload --port 8000

# 2) 前端
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

开发环境通过 Vite 代理 `/api` → `http://127.0.0.1:8000`，无 CORS 问题。
「知识库管理」页（`#/knowledge`）走另一套后端：`/api/knowledge/*` → `http://127.0.0.1:8080`
（ask ai 阶段五 FastAPI，可用 `ASK_AI_URL` 覆盖），启动方式：

```bash
# 3) ask ai 阶段五后端（另一个终端，工作目录「ask ai/」）—— 提供知识库文档管理接口
python main.py                # 监听 0.0.0.0:8080，需 .env 里的 DIFY_DATASET_*
```

> 一键启动三件套（主后端 + ask ai 后端 + 前端，各开一个窗口）：
> `powershell -ExecutionPolicy Bypass -File scripts/dev-all.ps1`
> 支持 `-DryRun`（只检查）、`-Stop`（停掉三个服务）、`-BackendPort/-AskAiPort/-FrontendPort`（换端口）、
> `-AskAiPath`（ask ai 目录不在同级时指定）。
生产部署务必让 Nginx 对 `/api/v1/kbs/*/chat/stream` 关闭缓冲（见 `deploy/nginx.conf`：
`proxy_buffering off; proxy_cache off;`），否则 SSE 会被攒批。

## 目录

```
src/
├── api/            http.ts（fetch + 统一错误/鉴权）+ kb.ts document.ts chat.ts quiz.ts wrongBook.ts knowledge.ts（ask ai 后端）
├── utils/          sse.ts（POST + ReadableStream 解析）+ format.ts
├── stores/         auth.ts（轻量 reactive store，未引入 Pinia）
├── router/         index.ts（登录守卫）
├── layouts/        BasicLayout.vue（顶栏 + 健康状态）
├── views/
│   ├── login/            登录 / 注册
│   ├── kb/               list.vue（列表/新建）· detail.vue（上传、进度轮询、预览、分块、检索测试、成员）· manage.vue（ask ai 知识库文档管理）
│   ├── chat/             index.vue（SSE 流式 + [n] 引用 + 会话侧栏）
│   ├── quiz/             generate.vue · take.vue · result.vue
│   ├── wrong-book/       index.vue
│   └── history/          index.vue（会话列表 / 重命名 / 删除）
├── components/     Uploader.vue · StatusTag.vue · AuditBadge.vue · EmptyState.vue
└── types/          api.d.ts（与 docs/05 响应结构一一对应）
```

## 与 docs/02 §3 的差异（有意为之）

- 未引入 Pinia：MVP 只有登录态与局部页面状态，用 `reactive` 足够，减少依赖。
- `views/document/*`、`views/kb/members.vue` 合并进 `views/kb/detail.vue`（上传 / 进度轮询 / 成员），
  减少页面跳转；需要拆分时按 docs/02 §3 目录直接平移即可。
- 未引入 Markdown 渲染库：Markdown 以 `pre` 原样展示（`white-space: pre-wrap`），
  避免 MVP 引入 XSS 风险与额外依赖。
- 「知识库管理」的上传弹窗按 Element Plus `el-dialog`/`el-upload` 的交互实现
  （遮罩、点遮罩关闭、拖拽上传、进度条、二次确认），但用项目自有样式与 `fetch` 封装，
  不引入 Element Plus/Pinia。

## 关键交互

| 场景 | 行为 |
|---|---|
| 上传后 | `202` 受理 → 文档列表每 2s 轮询 `/documents/{id}/status`，`ready/failed/blocked` 时停止 |
| 审核拦截 | 上传/解析命中返回 `50301`，前端 `AuditBadge` 显示「已拦截」；SSE 场景收到 `moderation` 事件弹出提示 |
| 问答 | `meta` → `sources` → `message(delta)*` → `usage` → `done`；引用只给本地 `document_id`，前端拿不到 Dify ID |
| 续聊 | 侧栏点会话 → `POST /conversations/{id}/continue`；`dify_available=false` 时提示仅可查看历史 |
| 出题 | 同步等待（约 120s 上限），生成后跳转答题；作答提交后进入成绩单，错题自动进错题本 |
| 知识库管理（`#/knowledge`） | 上传走弹窗（点击/拖拽，`.txt/.md/.pdf/.docx/.html/.csv/.xlsx`，≤15MB），成功后自动刷新列表；`available` 绿 / `indexing` 蓝 / `error` 红；删除二次确认；有索引中的文档时每 5s 自动刷新 |
