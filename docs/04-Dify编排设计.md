# 04 · Dify 编排设计（Dify 1.17.0）

> 前置：读 [01 §1.1 环境变量](./01-架构总览.md)。**`FILES_URL` 必须配**，否则 MinerU 插件处理文件会失败。

## 1. 知识管道（Create Knowledge Base → Custom / Knowledge Pipeline）

```
[文件上传 DataSource]
        │
        ▼
[文档提取器 Extractor]  ← 首选 MinerU 插件；不可用时用「内置解析器」或后端预转 Markdown
        │                  （`DIFY_PARSE_EXTENSIONS` 命中的类型由后端直传原文件走本节点，见 §1.2）
        │  text / images
        ▼
[分块器 Chunker]
        │  separator = "\n\n"
        │  max length = 512   （⚠️ 单位是「字符」，中文≈token）
        │  overlap    = 50
        ▼
[知识库节点 KB]  chunk 内容 + 检索设置
        │  embedding: 中文优先（bge-m3 / text-embedding-v3 等）
        │  retrieval: Hybrid + Rerank + TopK 5 + Score 阈值 0.5
        ▼
[用户输入字段]（可选，上传时填写章节等 metadata）
        │
        ▼
[测试 & 发布] → 上传文件 → 解析 → 分块 → 嵌入 → 可问答
```

**关键配置表**

| 节点 | 配置项 | 值 | 备注 |
|---|---|---|---|
| 数据源 | File Upload | pdf / doc / docx / ppt / pptx / png / jpg / jpeg / md | MinerU 支持范围；Dify 侧白名单需实测 |
| 提取器 | 解析插件 | MinerU（`langgenius/mineru`） | 插件属 Tool 类型，**能否作为管道提取节点需 spike 验证**；不行则走 §1.1 替代路径 |
| 提取器 | MinerU 参数 | `enable_formula=true`、`enable_table=true`、`document_language=ch`、`model_version=pipeline` | 表格/公式对教材类文档重要 |
| 分块器 | delimiter / max / overlap | `\n\n` / `512` / `50` | 与 `kb.chunk_size`、`kb.chunk_overlap` 保持一致 |
| 知识库 | 索引模式 | 高质量（High Quality） | 经济模式无向量检索，不满足需求 |
| 知识库 | 检索 | Hybrid + Rerank | Rerank 模型需先在 Model Provider 配置好 |

### 1.1 替代路径（MinerU 不能插管道时，**MVP 默认走这条更可控**）

```
后端接收文件 → 内容审核 → 调 MinerU 服务（本地/官方 API）→ 得到 Markdown
   → POST /v1/datasets/{dataset_id}/document/create-by-text
        body: { name, text, indexing_technique:"high_quality",
                process_rule:{ mode:"custom",
                  rules:{ pre_processing_rules:[{id:"remove_extra_spaces",enabled:true},
                                                {id:"remove_urls_emails",enabled:false}],
                          segmentation:{ separator:"\n\n", max_tokens:512, chunk_overlap:50 } } } }
   → 轮询 document indexing_status == completed
```
> 这条路径把"审核卡点"和"归属/权限"都留在后端，是 MVP 的推荐实现。

### 1.2 本地解析器啃不动的类型：直传原文件（`DIFY_PARSE_EXTENSIONS`）

```
后端接收文件 → 内容审核 → POST /v1/datasets/{dataset_id}/document/create-by-file（原文件）
   → Dify 自带提取器 / 知识库流水线的解析分支（如 else → parse file）
   → 轮询 document indexing_status == completed
```

- 触发条件：`document.file_ext` ∈ `DIFY_PARSE_EXTENSIONS`（默认 `pdf`；留空 = 全部本地解析）。
- 用途：本地 `builtin` 未装 pypdf / 无 MinerU 时，PDF（扫描件、作品集、图片型 PDF）在本机抽不出文本，
  会报「内置解析器抽不出这个 PDF 的文本」；这类文件交给 Dify 侧解析最省事。
- 代价：这条通道本地不产出 Markdown —— 入库前的**文本审核**退化为只审文件名，文档详情页的
  「预览」改为拉 Dify 分块拼装；`parse_mode` 记为 `dify`（前端显示「Dify 侧解析」）。
- 流水线型知识库（`pipeline_id` 非空，如 Dify 的「Untitled 1」）以流水线节点的分块配置为准，
  后端传的 `process_rule` 不生效。

### 1.3 知识库接口的每分钟配额（「Dify 返回 403」的根因与治理）

Dify 把「订阅侧的知识库接口频率上限」返回成 **403**（不是 429）：

```
POST /v1/datasets/{id}/retrieve -> 403
{"code":"forbidden",
 "message":"Sorry, you have reached the knowledge base request rate limit of your subscription.",
 "status":403}
```

- 触发点：Dify 的 `cloud_edition_billing_rate_limit_check("knowledge", ...)` 挂在**所有知识库类接口**上
  —— `/datasets/{id}/retrieve`、`/documents`、`/documents/{id}/segments`、
  `/documents/{batch}/indexing-status`、以及建库/改库/删库。本仓库当前订阅实测 **10 次/分钟**
  （第 11 次触发，60s 滚动窗口，窗口过期自动恢复）。
- 典型翻车链路（真实故障）：上传 PDF（走 Dify 侧解析）→ worker 每 **1 秒**轮询一次入库状态
  + 前端每 **2 秒**刷一次文档列表 → 几十秒就把配额打满 → 用户随后点「出题」，
  出题前的多路检索直接 403，前端显示「Dify 返回 403」。
- 治理（`app/services/dify/quota.py`）：
  1. **闸门**：本进程发往 `/datasets/*` 的请求走滚动窗口令牌
     （`DIFY_KNOWLEDGE_RATE_LIMIT_PER_MINUTE`，默认 8 次/分钟，留余量给同 Key 的其它调用方）；
     没名额时最多等 `DIFY_KNOWLEDGE_WAIT_SECONDS`（默认 15s），仍拿不到就报 **42901** 可读提示；
  2. **短 TTL 缓存**：检索 60s、分块列表 30s、文档列表 5s —— 重复查询与前端轮询不再消耗配额；
  3. **stale-while-error**：真被限流时用上一次的缓存结果兜底，出题/问答不中断；
  4. **403 归一**：识别 rate limit 的 403 → 42901「请约 1 分钟后重试」，不再把裸状态码丢给用户；
  5. 入库轮询 `INDEXING_POLL_INTERVAL_SECONDS`（默认 5s）+「状态接口路径记忆」
     （每次轮询只打 1 次接口，而不是原来的 3 次降级试探），且被限流时继续等待而不是把文档判失败。

### 1.4 上游报错怎么定位（400 / 403 都要能看到 Dify 的原始 body）

Dify 的原因只写在**响应 body** 里，只回一句状态码等于没法处理，因此：

- **错误提示带原因**：`Dify 返回 400/422` 会拼上 Dify 的 `message` 与请求路径，例如
  `Dify 返回 400（POST /datasets/{id}/retrieve）：3 validation errors for HitTestingPayload ...`
  （去掉 pydantic 文档链接、压成一行、截断 200 字，见 `services/dify/http_base.py`）。
  403 限流仍走 §1.3 的 42901 友好提示，不暴露裸状态码。
- **日志落盘**：配 `LOG_FILE=logs/backend.log`（`.env.example` 已给；相对路径按 `backend/` 解析，
  5MB × 3 轮转）后，控制台的同样内容会写进 `backend/logs/backend.log`。每个 Dify 非 2xx 都留一行
  `Dify <METHOD> <path> -> <status> <原始 body（最多 2000 字）>` —— 控制台一滚就没了，这行是
  事后定位的唯一线索。`LOG_LEVEL` 控级别；`LOG_FILE=` 留空则只输出控制台（pytest 里就是这么设的，
  避免测试噪声淹没真实排障信息）。
- 实测过的 400 来源：`retrieval_model` 字段缺失或 `search_method` 取值非法（Dify 回
  `invalid_param` + pydantic 校验详情）、`/parameters` 与 `/workflows/run` 的入参不匹配
  （见 §3 的入参自适应）。

## 2. App A：`chat-qa`（Chatflow）—— AI 知识问答

```
[Start]
  inputs:
    kb_ids          array[string]  # 后端传入；必须由后端按权限计算，禁止前端直接指定
                                   # ⚠️ 落地提醒：实测 Dify 的 Start 变量不支持数组，
                                   # 因此校准版 DSL 里不声明该变量（见 §3 与 dify/README.md §1）
    top_k           number         # 默认 5
    score_threshold number         # 默认 0.5
    answer_style    string         # 默认 "教学式，简洁分点"
        │
        ▼
[知识检索 Knowledge Retrieval]
  - 检索知识库：{{#start.kb_ids#}}（支持多库）
    ⚠️ 实测本实例 Dify 只能在此**静态勾选**知识库，无法用变量选库（见 §3 / §6）
  - 检索设置：Hybrid + Rerank，TopK={{#start.top_k#}}，阈值={{#start.score_threshold#}}
        │  result: [{content, score, document_name, segment_id, position, ...}]
        ▼
[条件分支 If-Else]  结果为空 或 最高分 < 阈值
  ├─ 是 → [Answer] "知识库中未找到与问题相关的内容，请换一种问法或补充资料。"
  └─ 否 → [LLM]
              system: 见 dify/prompts/qa_system.md
              user:   问题 = {{#sys.query#}}；参考资料 = {{#knowledge_retrieval.result#}}
              要求：只依据参考资料作答；结论后标注 [1][2] 形式引用编号；
                    资料不足时明确说明"资料中未提及"，禁止编造
         → [Answer] {{#llm.text#}}   （流式输出）
```

- **开启内容审核**：应用设置 → 内容审核 → 勾选"审核输入内容"与"审核输出内容"（流式输出由 Dify 自动按 100 字符分段送审）。
- **引用来源**：后端解析 `message_end.retriever_resources`，字段映射见 [05 §5.2](./05-接口清单.md)。
- **续聊**：后端保存 `conversation_id` 并在后续请求回传；`user` 固定传 `"{tenant_code}:{user_id}"`，保证 Dify 侧会话按用户隔离。

## 3. App B：`workflow-quiz`（Workflow）—— 智能出题

```
[Start]
  inputs: kb_ids, types(array), count(number), difficulty(string), topic(string),
          context(string，后端按所选知识库检索出的【资料】——唯一出题依据，见下方「出题素材来源」)
        │
        ▼
[知识检索]  查询词 = topic 或 "核心知识点"；TopK = min(20, count*3)（仅作【资料】为空时的兜底）
        │
        ▼
[LLM 结构化生成]
  system: dify/prompts/quiz_generate_system.md
  输出 JSON（严格 schema）：
  { "questions": [
      { "type":"single|multi|judge|short",
        "stem":"...", "options":[{"key":"A","text":"..."}],
        "answer":"A" | ["A","C"] | true | "参考答案文本",
        "analysis":"解析...", "difficulty":"easy|medium|hard",
        "source_index":[1,3] } ] }
        │
        ▼
[Code 节点]  校验并修复：
  - type 合法；single/judge 选项数=4；multi 选项 4-5 且答案≥2 项
  - answer 必须落在选项 keys 内；short 必须有非空 answer
  - 剔除不合法题目；题数不足时置 need_more=true
        │
        ▼
[If-Else need_more] ─ 是 → 回到 [LLM]（最多补 1 轮）
        │
        ▼
[End]  输出 questions（后端负责落库 quiz / question）
```

**出题素材来源（2026-09-15 缺陷修复：题必须出自所选知识库的文档）**

「知识检索」节点只能**静态勾选**知识库，所以「只用所选知识库出题」不靠它，靠后端下发资料：

| 环节 | 做法 |
|---|---|
| 入参 | 后端按所选知识库检索后，把命中的原文拼成【资料】（带 `[n]` 编号与来源文件）作为 **`context`（paragraph）** 下发；`topic` 退回「重点知识点」本义（可留空） |
| 检索 | 多路查询（用户主题 → 知识库名 → 最近文档名）→ 合并去重 → **过滤没有实质内容的碎片块**（如内容只有 `---`）→ 相似度降序取 `top_k = max(6, min(12, 题数×2))`；阈值把有效素材挡光时放宽为 0 再取一次 |
| Prompt | 以【资料】为**唯一出题依据**；「知识检索」结果仅作【资料】为空时的兜底，两者冲突以【资料】为准 |
| 兜底 | 有效正文不足（< 50 字实质内容）直接 `400`（`知识库里的可用正文太少，无法出题`），**绝不按库名编题** |
| 兼容 | App B 未声明 `context` 时（老版本），后端把【资料】并入 `topic`（带 `【资料·出题唯一依据】` 标头、上限 3000 字），保证题仍出自文档；日志会 `WARNING` 提示补变量 |
| 入库 | 分块前清洗 Markdown 装饰线（`---`/`***`/`___`，表格分隔行 `\| --- \|` 保留）：否则 `separator="\n\n"` 会把它们切成 3 字符碎片块，占满检索 top_k 并可能挤掉正文；已有文档点「重新入库」会自动重建 Dify 文档 |

> 仍需人工一步（Dify 控制台，1 分钟，不影响 API Key）：App B「开始」节点加变量 `context`（段落、非必填），
> 并把 LLM Prompt 的「知识内容」块换成 `dify/prompts/quiz_generate_system.md` 里的【资料】块（用变量选择器插入
> `{{#1789374991715.context#}}`，节点 id 是数字，会被正确替换）。步骤见 `dify/README.md` §5。

**入参自适应（后端已实现，2026-09 线上 400 回归修复）**
- 真实 Dify 应用的 Start 变量由用户自助配置，可能与上面契约不一致（线上实例只声明
  `difficulty(select)` / `count(number)` / `type(paragraph)`）。后端出题前先 `GET /parameters`
  探测 Start 变量（结果缓存 5 分钟），按声明拼报文：`types` 映射为中文题型描述串（如
  `单选题、简答题`），`count` 声明为 number 时发数字，其余按文本发。
- 探测失败（旧版本 / 网络抖动）时发「并集」报文（`kb_ids` + `types` + `type` + `count` +
  `difficulty` + `topic`）：Dify 会忽略未声明的变量，因此两种契约都能跑通。
- Workflow 未声明 `topic` 时，后端把主题并入 `type`（`单选题、简答题（主题：xxx）`），
  保证「按主题出题」仍然生效；若要严格按知识库出题，建议在 Start 节点补 `topic`/`kb_ids`
  并把它们接到「知识检索」节点。
- **仓库已附带校准版 DSL**：`dify/dsl/workflow-quiz-generate.yml`（真实 App B 导出后最小改动）。
  Start 节点声明 `type(paragraph)` / `count(number)` / `difficulty(select)` / `topic(paragraph)`，
  「知识检索」查询词 = `topic`，导入后后端不必再把主题并进 `type`；
  `kb_ids` 有意不声明 —— Dify 的 Start 变量**不支持数组**（实测校验枚举只有
  `text-input/select/paragraph/number/checkbox/json_object/file/file-list`，声明 `array[string]` 会被
  挡回 `400 invalid_param`），而且「知识检索」节点也读不到它；
  其 Code 节点输出 `{success, questions(JSON 字符串), error, raw_output}`，与
  `parse_workflow_questions` 对齐（含缺 `id`、选项数不合规等坏题的剔除）。
- 输出侧统一容错：题型别名（`单选题|多选题|判断题|填空题|简答题`）、字段别名
  （`question`→`stem`、`explanation`→`analysis`）、字符串选项（`"A. xxx"`）、判断题布尔别名。

**Prompt 要点**（详见 `dify/prompts/quiz_generate_system.md`）
- 明确题型定义与各题型 JSON 形状；判断题 `options` 为 null、`answer` 为布尔
  （随仓库的 App B DSL 写的是 `options: []` + `answer: 正确/错误`，两种写法后端都能归一）。
- 要求"每题必须能由给定参考资料支撑"，并给出 `source_index`（引用第几段资料）→ 后端据此写入 `source_segments`，实现题目溯源。
- 难度定义写清：easy=记忆/识别，medium=理解/应用，hard=分析/综合。

## 4. App C：`workflow-quiz-grade`（Workflow）—— 简答题评分

```
[Start] inputs: stem, reference_answer, grading_points(段落文本，后端以「；」拼接), user_answer
   → [LLM] 输出 { "score": 0-100, "hit_points":[...], "miss_points":[...], "comment":"..." }
   → [End]
```
> 后端规则：`score >= 60` 记 `is_correct = true`；`< 60` 自动登记错题本。
>
> App C 的 DSL 见 `dify/dsl/workflow-quiz-grade.yml`（Start → LLM → Code 解析 → End）：
> `grading_points` 声明为**段落文本**（Dify 没有数组型 Start 变量，声明数组会 400）；后端探测到
> 该变量不是数组时会改发「；」拼接串（`workflow_client.grade_short_answer`），与 Code 节点的「；」
> 拆分对称 —— 无论声明成文本还是数组都能跑。
> Code 节点把模型输出归一成 `score(number)` + `hit_points/miss_points(string，`；` 分隔)` + `comment`，
> 越界分数夹到 0-100，模型不吐 JSON 时返回 0 分 + 原因；后端 `parse_grade_result` 还能兜住
> 「End 只输出单个 JSON 字符串」的写法（键名 `result/output/text/data/answer/json`），
> 两种编排都不会把整题误判成 0 分。采分点若被声明成文本变量，后端自动改用「；」拼接串发送。
>
> ⚠️ **节点 id 必须只用 `[A-Za-z0-9_]`（不能带中划线）**：Dify 替换 Prompt 里的
> `{{#节点id.变量名#}}` 用的是内建正则 `[a-zA-Z0-9_]{1,50}`，id 带 `-` 时占位符不会被替换，
> 模型直接读到字面文本 `{{#grade-start.stem#}}` → 判分恒定 0 分（App C 初版即此坑，已改
> `grade_start/grade_llm/grade_code/grade_end`）。结构化选择器（`value_selector`）不受影响，
> 所以症状极具迷惑性：开始节点取到了值、Code 节点也读到了 LLM 文本，只有 Prompt 变量是空。
> 详见 `dify/README.md` §4「节点 id 规则」，静态校验见 `scripts/check_dify_dsl.py`。

## 5. Moderation 扩展服务契约（后端 `api/v1/moderation.py`）

Dify 侧配置：设置 → 自定义端点 → 新增 `moderation`，填 URL 与 API Key（= `MODERATION_CALLBACK_TOKEN`）。

| 请求 `point` | 关键参数 | 期望响应 |
|---|---|---|
| `ping` | — | `{"result": "pong"}`（Dify 配置校验用） |
| `app.moderation.input` | `params.app_id`、`params.query`、`params.inputs` | `{"flagged": bool, "action": "direct_output", "preset_response": "..."}` |
| `app.moderation.output` | `params.app_id`、`params.text`（流式为 100 字符分段） | `{"flagged": bool, "action": "direct_output" \| "overridden", "preset_response": "...", "text": "..."}` |

```python
# 伪代码：统一入口
def handle(point: str, params: dict):
    if point == "ping":
        return {"result": "pong"}
    is_input = point.endswith("input")
    text = params.get("query") if is_input else params.get("text")
    verdict = moderator.check(text, scene="query" if is_input else "answer")
    if verdict.result == "pass":
        return {"flagged": False, "action": "direct_output", "preset_response": "ok"}
    return {"flagged": True, "action": "direct_output",
            "preset_response": "内容涉及敏感信息，已拦截，请调整后重试。"}
```
> 每次调用都写 `audit_log`，保留阿里云 `request_id` 与命中 label。

## 6. 变量与调用约定（重要）

| 约定 | 内容 |
|---|---|
| `user` 字段 | 固定 `"{tenant_code}:{user_id}"`；Dify 按此隔离会话，`/conversations?user=` 直接可用 |
| `kb_ids` | **只能由后端按权限计算后传入**；前端永远不直接给 Dify 传 dataset_id；Dify 侧 Start 不声明它（本实例不支持数组型变量，声明会被 400 挡回） |
| `conversation_id` | 由后端维护（存 `conversation.dify_conversation_id`），前端只认本地会话 ID |
| 引用编号 | Prompt 要求模型输出 `[n]`；后端把 `n` 映射到 `retriever_resources[n-1]`，随流下发 `sources` 事件 |
| 模型选择 | 问答用长上下文模型，出题用强指令遵循模型，嵌入用中文优化模型；均在 Dify Model Provider 配置，后端不直连模型 |

## 7. DSL 导入与自检清单

1. `dify/dsl/*.yml` 在 Dify 控制台"导入 DSL"逐个导入，改名为 `chat-qa`、`workflow-quiz`、`workflow-quiz-grade`。
   （三份 DSL 已随仓库提供：`chat-qa.yml` / `workflow-quiz-generate.yml` / `workflow-quiz-grade.yml`；
   导入后换模型、配 API Key，再跑 `python scripts/check_dify_dsl.py` 离线校验入参/输出契约）
2. 逐项校验：
   - [ ] `FILES_URL` 已配置且 Dify 容器可访问自身 API
   - [ ] MinerU 插件已安装，Base URL / Token / Service Type 正确
   - [ ] 嵌入模型、Rerank 模型已配置并可用
   - [ ] App A / App B 的「知识检索」节点已重新勾选目标知识库
     （导出里的 `dataset_ids` 是实例内不透明引用，非 UUID，跨实例会失效）
   - [ ] Start 变量类型都在 Dify 枚举内（本实例**不支持数组**，别声明 `array[string]`）
     —— `python scripts/check_dify_dsl.py` 会校验这一点
   - [ ] 知识管道分块 = 512 / 50，分隔符 `\n\n`
   - [ ] Chatflow 已开启输入/输出内容审核，自定义端点 ping 返回 `pong`
   - [ ] 出题 Workflow 用 3 组参数试跑，JSON 可被后端解析
   - [ ] 两个 App 均申请 API Key，且只写在后端 `.env`（绝不进前端）

## 8. 已知限制与应对（评审要讲清）

| 限制 | 影响 | 应对 |
|---|---|---|
| 分块单位是**字符**不是 token（`max_tokens` 为历史命名） | 英文文档块偏小（512 字符 ≈ 100 token） | 交付说明中注明；需要严格 token 时改用"自建分块 + `create-by-text`" |
| Dify **无文档导入审核 hook** | 上传内容绕过审核风险 | 由后端在 `create-by-text` 之前强制送审（§1.1 路径天然满足） |
| 插件市场 item 被作者删除的风险（曾发生） | 插件不可再安装 | MinerU 走"直调服务 + 后端转 Markdown"，不把关键链路押在插件上 |
| 单次上传 5 个文件 / 15MB 默认限制 | 大批量导入体验差 | 调大 Dify 环境变量 + 前端串行分批上传 |
| Dify LICENSE 多租户条款 | 对外 SaaS 有合规风险 | MVP 单 workspace；对外多租户前完成商业授权评估 |
