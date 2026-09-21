# Dify 侧落地（dify/）

后端在 `DIFY_MODE=http` 时调用的三件套（Dify 1.17.0 验证）：**App A 问答 Chatflow**、
**App B 出题 Workflow**、**App C 简答判分 Workflow**，以及一个 **datasets 知识库 API Key**。
Prompt、节点编排、结构化输出与校验规则全部在 `docs/04-Dify编排设计.md`，本文件只列「落地清单」。

## 1. 必须创建的应用与 Key

| 用途 | 类型 | 需开启 | 对应环境变量 |
|---|---|---|---|
| App A `chat-qa` | Chatflow | 流式输出、引用与归属（retriever_resources）、内容审核扩展点 | `DIFY_APP_API_KEY` |
| App B `workflow-quiz` | Workflow（blocking） | 输出变量 `questions`（JSON 字符串） | `DIFY_QUIZ_WORKFLOW_API_KEY` |
| App C `workflow-quiz-grade` | Workflow（blocking） | 输出变量 `score / hit_points / miss_points / comment` | `DIFY_GRADE_WORKFLOW_API_KEY` |
| 知识库 API | —（Dataset 服务 API） | 建库/入库/检索/分块 | `DIFY_DATASET_API_KEY` |

App A 的输入变量（后端 SSRF 传入，见 `backend/app/services/dify/chat_client.py`）：

```json
{ "inputs": { "kb_ids": ["<dataset_id>"], "top_k": 5, "score_threshold": 0.5, "answer_style": "教学式，简洁分点" } }
```

上表名称是后端环境变量语义名；Dify 控制台里的应用名可自定义（当前 workspace：App A = `ask ai`、
App B = `ask work`，App C 建议 `workflow-quiz-grade`）。

`kb_ids` 是后端按租户/成员权限算出的 Dify dataset_id 列表，前端永不接触。
`dify/dsl/chat-qa.yml` 的 Start 节点声明 `top_k` / `score_threshold` / `answer_style` 三个入参，
**`kb_ids` 有意不声明**：实测本实例 Dify 的 Start 变量只支持
`text-input / select / paragraph / number / checkbox / json_object / file / file-list`（**没有数组**），
声明成 `array[string]` 会让每次调用被 Dify 挡回 `400 invalid_param`；后端探测到「未声明」就不会
下发该字段（`workflow_client._declared_inputs`），行为与声明前一致。
另外 Dify 的「知识检索」节点只能**静态勾选**知识库，本来也读不到 `{{kb_ids}}`：

- `DIFY_CHAT_MODE=rag`（推荐）：后端先按所选知识库自己的 dataset 检索，再把带 `[1][2]` 编号的
  【资料】注入提问 —— 换库即时生效，`kb_ids` 只留在后端；
- `DIFY_CHAT_MODE=app`：由 App A 内的检索节点召回，需在 Dify 界面把它勾选成目标知识库
  （或复制多份 App A），`kb_ids` 即使送过去也会被 Dify 忽略。

Prompt 已要求**引用必须带 [1][2] 编号**，与后端 `sources` 数组下标一一对应。

## 2. 内容审核扩展点（后端 → Dify 回调）

在 App A 的「内容审核」设置里把 API 扩展指向后端：

```
POST http://<backend-host>:8000/api/v1/moderation
Header: X-Moderation-Token: <与 backend/.env 的 MODERATION_CALLBACK_TOKEN 一致>
```

后端会处理 `ping`、`app.moderation.input`、`app.moderation.output` 三类回调，
返回 `{ "flagged": bool, "action": "direct_output", "preset_response": "..." }`（见 `docs/05 §9`）。
命中风险时返回 `flagged=true`，Dify 会用 `preset_response` 替换输出，从而不把违规内容给到用户。

## 3. 知识库与分块口径

- 分块由 Dify 负责：后端调用 `document/create-by-text` 时传 `process_rule.rules.segmentation`
  （`separator="\n\n"`、`max_tokens=chunk_size`、`chunk_overlap`，**单位是字符**，见 `docs/04 §8`）。
- **入库前清掉 Markdown 装饰线**（`clean_markdown_for_index`：`---` / `***` / `___`，表格分隔行
  `| --- |` 保留）：`\n\n` 切块会把独立的分隔线切成 3 字符碎片块，实测这些碎片会占检索 top_k、
  甚至以高分挤掉正文（真机上直接导致「出题拿不到资料」）。清洗只影响**新入库 / 重新入库**的文档：
  正文有变化时入库阶段会删掉重建 Dify 文档，所以对老文档点一次「重新入库」即可生效。
- 检索配置（top_k / score_threshold / 混合检索 / rerank）在 KB 维度存库，调 Dify 时按库传入。
- 入库进度由后轮询 `documents/{id}/indexing-status`，`completed` 才把文档置为 `ready`。

## 4. App B 出题资料（必做：题必须出自所选知识库的文档）

Dify 的「知识检索」节点**只能静态勾选**知识库，所以「只用所选知识库出题」不能靠它。正确口径是
**后端按所选知识库检索 → 把原文当 `context` 入参下发给 App B**（实现见 `docs/04 §3`）。

App B 需要人工补一步（控制台 1 分钟，**不用换 API Key、不用重导 DSL**）：

1. 「开始」节点 → 添加变量：名称 `context`，类型 **段落**，必填**关闭**（后端探测到就下发【资料】；
   未声明时会退化成把【资料】并入 `topic`，日志里有 `WARNING` 提示）；
2. LLM 节点 → Prompt：把「知识内容：\n`{{#context#}}`」换成 `dify/prompts/quiz_generate_system.md`
   里的【资料】块 —— 用变量选择器插入 `{{#1789374991715.context#}}`（**节点 id 是数字，会被正确替换**；
   规则见上方「节点 id 规则」），并保留【知识库检索】段作为兜底；
3. 发布。

> 为什么必须让 Prompt「以【资料】为准」：App B 里的检索节点绑定的是**某一个固定库**（导出文件里是
> 不透明引用，导到别的实例还会失效），不换 Prompt 时它召回的旧库内容会混进题目——真机复验就看到过
> 「考 A 库，却出现 B 库字段说明」的混答。
>
> 覆盖重导 `dify/dsl/workflow-quiz-generate.yml` 也能得到同样的 Start 变量与 Prompt，但会把这个
> 固定库引用一并重置，所以**重导后记得重新勾选知识库**。

## 5. 目录约定

```
dify/
├── dsl/        # 可导入的 DSL：chat-qa.yml / workflow-quiz-generate.yml / workflow-quiz-grade.yml
│               # （知识管道 kb-pipeline-mineru.yml 仍在 Dify 控制台内按 docs/04 §1 编排，未导出）
├── prompts/    # 由 DSL 自动导出的 Prompt 备份（qa_system.md / quiz_generate_system.md / quiz_grade_system.md）
└── README.md   # 本文件
```

> 目录现状（2026-09）：`dify/dsl/` 已收入三份可导入 DSL —— 由真实 Dify 实例导出，再按后端契约做了
> 最小改动校准：
>
> | 文件 | 对应应用 | 校准内容 |
> |---|---|---|
> | `chat-qa.yml` | App A（导出名 `ask ai`） | Start 声明 `top_k/score_threshold/answer_style`（`kb_ids` 有意不声明，见 §1）；Prompt 要求 `[1][2]` 编号并优先使用后端注入的【资料】 |
> | `workflow-quiz-generate.yml` | App B（导出名 `ask work`） | Start 增补 `topic`（`kb_ids` 同上）+ **`context`（后端下发的【资料】，段落、非必填）**；Prompt 改为「以【资料】为唯一出题依据，检索结果仅兜底」；「知识检索」查询词改用 `topic`；题型名对齐中文枚举；Code 节点不再强制 `id`（缺失时自动补序号）。**升级提示**：老版 App B 不声明 `context`，后端会退化把【资料】并入 `topic`（题仍出自文档），按 §4 补变量即可走干净路径 |
> | `workflow-quiz-grade.yml` | App C（新建） | `stem/reference_answer/grading_points(段落文本)/user_answer` → LLM → Code 归一化 → End 输出 `score/hit_points/miss_points/comment`；节点 id 用 `grade_start/grade_llm/grade_code/grade_end`（**下划线，见下方「节点 id 规则」**） |
>
> 导入后仍需人工三步：① 把 LLM 节点模型换成自己 workspace 里可用的模型；
> ② 给 App B / App C 各申请一个 API Key 写进 `backend/.env`；
> ③ 检查 App C「开始」节点的 `grading_points` 是**段落**而不是数组（本仓库 2026-09-15 之前的
> 版本写成了 `array[string]`，那样每次判分都会被 Dify 挡回 400）。
>
> ### 节点 id 规则（2026-09-15 踩坑，务必遵守）
>
> **节点 id 只能是 `[A-Za-z0-9_]`，绝不能带中划线。** Dify 运行时替换 Prompt 里的
> `{{#节点id.变量名#}}` 用的是内建正则 `\{\{#([a-zA-Z0-9_]{1,50}(?:\.[a-zA-Z0-9_]{1,50})?)#\}\}`：
> id 带 `-` 时占位符**不会被替换**，模型收到的是字面文本 `{{#grade-start.stem#}}`，于是「材料为空」
> 永远给 0 分（App C 初版写的就是 `grade-start/grade-llm/grade-code/grade-end`，已全部改成下划线）。
>
> 容易漏掉的点：`value_selector: [grade_code, score]`、`[grade_llm, text]` 这类**结构化选择器不走该正则**，
> 所以它们一直正常——「开始节点取值正常、Code 节点也能读到 LLM 文本，只有 Prompt 里 4 个变量是空」，
> 正是这个 bug 的典型症状，别去怀疑参数名或 Start 变量类型。
>
> 排查手法：`response_mode` 改成 `streaming` 调 `/workflows/run`，逐个 consumer 事件里
> `node_started` / `node_finished` 都带 `node_id` 与各节点的真实输入输出，能直接看到
> `llm` 节点收到的 Prompt 原文（`data.outputs.text` 里会有模型的 `<think>`，它自己会复述
> 「变量未填充：{{#grade-start.stem#}}」）。
>
> 该规则已进静态校验：`scripts/check_dify_dsl.py` 会断言所有节点 id 合法、连线与 `{{#...#}}`
> 引用都指向真实存在的节点（`grade-start` 这种写法当场报错）。
>
> `dify/prompts/*.md` 由 `scripts/check_dify_dsl.py` 从 DSL 自动导出（改 Prompt 请改 DSL 后重跑）：
> 该脚本会离线校验三份 DSL 的入参/输出能否被后端解析，**不需要真实 Dify**。
>
> ```bash
> python scripts/check_dify_dsl.py
> ```
>
> 已知限制（Dify 能力边界，非本仓库能改）：检索节点不支持按变量选库。应对已经落地 ——
> **问答**用 `DIFY_CHAT_MODE=rag`（后端检索后注入【资料】），**出题**由后端按所选知识库检索后
> 经 `context` 下发【资料】（见 §4）；App B 内的检索节点只作兜底，多库出题无需复制应用。
>
> ⚠️ 导入后必做一件事：**在 App A / App B 的「知识检索」节点里重新勾选目标知识库**。
> 导出文件里的 `dataset_ids` 是实例内的不透明引用（App B 导出里形如 `ftog4r…`，64 位 base64、非 UUID，
> 在 `GET /datasets` 里查不到）：导回同一个 workspace 可用，导到别的 Dify 实例会失效。
> 当前 workspace 的数据集：`langgtp`（74d8bc11…，出题实测用的库）/ `Untitled 1`（9143ff65…）/
> `prompt模板`（428a8b0c…）。
> 在没有真实 Dify 的环境里，用 `DIFY_MODE=mock` 即可完整跑通全部业务流程与自动化测试。

## 6. 自检

```bash
curl http://127.0.0.1:8000/health
# data.dify.keys 里每个 Key 是否为 true、reachable 是否为 true，一眼可见
```
