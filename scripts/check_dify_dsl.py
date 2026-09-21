"""校验 dify/dsl/*.yml 与后端契约是否对得上，并把 Prompt 导出到 dify/prompts/。

跑法（backend 目录为工作目录时也可）：python scripts/check_dify_dsl.py
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_backend_parsers():
    """复用 backend 的解析函数（脚本在仓库根跑，需先把 backend 挂到 sys.path）。

    刻意放在函数里而不是模块顶层：避免代码语句夹在 import 之间触发 ruff 的 I001。
    """
    if str(ROOT / "backend") not in sys.path:
        sys.path.insert(0, str(ROOT / "backend"))
    from app.services.dify.mapping import parse_grade_result, parse_workflow_questions

    return parse_workflow_questions, parse_grade_result


parse_workflow_questions, parse_grade_result = load_backend_parsers()

DSL = ROOT / "dify" / "dsl"
PROMPTS = ROOT / "dify" / "prompts"


def load(name: str) -> dict:
    return yaml.safe_load((DSL / name).read_text(encoding="utf-8"))


def node(data: dict, node_type: str) -> dict:
    return next(n for n in data["workflow"]["graph"]["nodes"] if n["data"]["type"] == node_type)


def start_vars(data: dict) -> dict[str, dict]:
    return {v["variable"]: v for v in node(data, "start")["data"]["variables"]}


#: 本实例 Dify 允许的 Start 变量类型（2026-09 实测 /workflows/run 校验枚举：**不支持数组**）
DIFY_ALLOWED_TYPES = {
    "text-input",
    "select",
    "paragraph",
    "number",
    "external_data_tool",
    "file",
    "file-list",
    "checkbox",
    "json_object",
}


def assert_types_allowed(name: str, variables: dict[str, dict]) -> None:
    """Start 变量类型必须落在 Dify 支持的枚举内。

    真实教训：把 `grading_points` 写成 `array[string]`，导入后每次判分都被 Dify 挡回
    `400 invalid_param: type Input should be 'text-input' ...`（数组不在枚举里）。
    """
    bad = {k: v["type"] for k, v in variables.items() if v["type"] not in DIFY_ALLOWED_TYPES}
    assert not bad, f"{name} 出现 Dify 不支持的 Start 变量类型: {bad}"


#: Dify 运行时替换 Prompt 里的 {{#节点id.变量名#}} 用的是内建正则，节点 id 只允许 [A-Za-z0-9_]{1,50}。
#: 真实教训：App C 手写节点 id `grade-start`（带中划线）→ 占位符根本没被替换，模型读到的是
#: 字面文本「{{#grade-start.stem#}}」，判分永远 0 分；流式事件里 llm 节点的输入能看到原文。
#: 注意：`value_selector: [grade_code, score]` 这种结构化选择器不走该正则，所以它一直正常，
#: 掩盖了问题——只有 Prompt 文本形态的引用会失效。
NODE_ID_OK = re.compile(r"^[a-zA-Z0-9_]{1,50}$")
REF_ANY = re.compile(r"\{\{[^{}]*\}\}")
REF_OK = re.compile(r"^\{\{#([a-zA-Z0-9_]{1,50})(?:\.([a-zA-Z0-9_]{1,50}))?#\}\}$")
#: 系统根（不是节点 id）：单段引用如 `{{#sys.query#}}`，选择器如 `[sys, query]`
SYSTEM_REFS = {"sys", "conversation", "environment", "loop"}


def run_code_node(data: dict, llm_output: str) -> dict:
    """在本地执行 DSL 里 Code 节点的 main()，等价于 Dify 沙箱。"""
    code = node(data, "code")["data"]["code"]
    ns: dict = {}
    exec(compile(code, "code-node", "exec"), ns)  # noqa: S102
    return ns["main"](llm_output)


def check_app_a() -> None:
    print("App A chat-qa.yml")
    dsl = load("chat-qa.yml")
    variables = start_vars(dsl)
    # kb_ids 有意不声明：本实例 Dify 不支持数组型 Start 变量，且检索节点无法按变量选库；
    # 后端探测到「未声明」就不会下发该字段（见 dify/README.md §1）。
    assert set(variables) == {"top_k", "score_threshold", "answer_style"}, variables
    assert all(not v["required"] for v in variables.values())
    assert_types_allowed("App A", variables)
    retrieval = node(dsl, "knowledge-retrieval")
    assert retrieval["data"]["query_variable_selector"][-1] == "sys.query"
    prompt = node(dsl, "llm")["data"]["prompt_template"][0]["text"]
    for needle in ("[1][2]", "【资料】", "{{#context#}}", "{{#sys.query#}}"):
        assert needle in prompt, needle
    print("  OK：3 个入参（= 后端 inputs 去掉 kb_ids）｜Prompt 要求 [n] 编号且优先用【资料】")


def check_app_b() -> None:
    print("App B workflow-quiz-generate.yml")
    dsl = load("workflow-quiz-generate.yml")
    variables = start_vars(dsl)
    assert set(variables) == {"difficulty", "count", "type", "topic", "context"}, variables
    assert_types_allowed("App B", variables)
    assert variables["type"]["required"] and variables["count"]["type"] == "number"
    assert not variables["topic"]["required"]
    # context = 后端按所选知识库检索出的【资料】；App B 的检索节点只能静态绑库，题源必须靠它
    assert not variables["context"]["required"] and variables["context"]["type"] == "paragraph"
    assert node(dsl, "knowledge-retrieval")["data"]["query_variable_selector"][-1] == "topic"
    prompt = node(dsl, "llm")["data"]["prompt_template"][0]["text"]
    for needle in (
        "{{#1789374991715.context#}}",
        "{{#1789374991715.topic#}}",
        "【资料】",
        "唯一出题依据",
        "source_index",
        "单选题/多选题/判断题/简答题",
    ):
        assert needle in prompt, needle

    # Code 节点：缺 id / 中文题型 / "A. xxx" 选项 / 非法题（选项只有 1 个）混合输出
    raw = """```json
{"questions": [
 {"id": 1, "question": "O(1) 查找用哪种结构？", "type": "单选题",
  "options": ["A. 顺序表", "B. 哈希表", "C. 链表", "D. 栈"], "answer": "B",
  "explanation": "哈希表均摊 O(1) 查找"},
 {"question": "哪些是稳定排序？", "type": "多选题",
  "options": ["A. 冒泡", "B. 快排", "C. 归并", "D. 堆排"], "answer": ["A", "C"],
  "explanation": "冒泡与归并稳定"},
 {"question": "栈是先进先出。", "type": "判断题", "options": [], "answer": "错误",
  "explanation": "栈是后进先出"},
 {"question": "简述时间复杂度的含义", "type": "简答题", "options": [],
  "answer": "衡量运行时间随规模增长的量级", "explanation": "忽略常数与低阶项"},
 {"question": "选项不足的坏题", "type": "单选题", "options": ["A. 只有一个"],
  "answer": "A", "explanation": ""}
]}
```"""
    out = run_code_node(dsl, raw)
    assert out["success"] == "True", out
    assert '"id": 2' in out["questions"] or '"id":2' in out["questions"], "缺 id 未被自动补齐"
    questions = parse_workflow_questions(out)
    assert [q["type"] for q in questions] == ["single", "multi", "judge", "short"], questions
    assert [q["answer"] for q in questions] == ["B", ["A", "C"], False, "衡量运行时间随规模增长的量级"]
    assert questions[0]["options"][0] == {"key": "A", "text": "顺序表"}, questions[0]
    assert questions[3]["analysis"] and [q["seq"] for q in questions] == [1, 2, 3, 4]

    # 自报失败时：后端要能读到 error（workflow_client._self_reported_error）
    bad = run_code_node(dsl, "抱歉，我无法根据以上内容出题。")
    assert bad["success"] == "False" and bad["error"], bad
    print(
        "  OK：5 个入参（含后端注入的【资料】context）｜Prompt 以【资料】为唯一出题依据"
        "｜Code 节点输出可被 parse_workflow_questions 解析（含坏题剔除）"
    )


def check_app_c() -> None:
    print("App C workflow-quiz-grade.yml")
    dsl = load("workflow-quiz-grade.yml")
    variables = start_vars(dsl)
    assert set(variables) == {"stem", "reference_answer", "grading_points", "user_answer"}, variables
    assert_types_allowed("App C", variables)
    # 采分点用段落文本（数组不被 Dify 支持）：后端探测到非数组就改发「；」拼接串
    assert variables["grading_points"]["type"] == "paragraph"
    prompt = node(dsl, "llm")["data"]["prompt_template"][0]["text"]
    for needle in ("{{#grade_start.stem#}}", "{{#grade_start.grading_points#}}", '"score"'):
        assert needle in prompt, needle
    end = node(dsl, "end")
    assert {o["variable"] for o in end["data"]["outputs"]} == {
        "score",
        "hit_points",
        "miss_points",
        "comment",
    }

    cases = [
        # 1) 常见形态：思考段 + ```json 围栏
        (
            (
                "<think>先对齐采分点</think>\n```json\n"
                '{"score": 85, "hit_points": ["说出 O(n)"], "miss_points": ["未提均摊"],'
                ' "comment": "要点命中较全"}\n```'
            ),
            85.0,
            ["说出 O(n)"],
            ["未提均摊"],
        ),
        # 2) score 是字符串 + 命中点用「；」拼接
        (
            (
                '{"score": "72.5", "hit_points": "说出定义；举出例子", "miss_points": "未提边界",'
                ' "comment": "基本正确"}'
            ),
            72.5,
            ["说出定义", "举出例子"],
            ["未提边界"],
        ),
        # 3) 越界分数被夹到 0-100
        ('{"score": 130, "hit_points": [], "miss_points": [], "comment": "满分"}', 100.0, [], []),
        # 4) 模型不听话（非 JSON）时不炸，返回 0 分 + 原因
        ("我不会打分。", 0.0, [], []),
    ]
    for raw, score, hits, misses in cases:
        out = run_code_node(dsl, raw)
        result = parse_grade_result(out)
        assert result["score"] == score, (raw, result)
        assert result["hit_points"] == hits and result["miss_points"] == misses, (raw, result)
        assert result["comment"], result
    print("  OK：4 个入参（采分点=段落文本）｜Code 节点 → parse_grade_result 全通（含分数夹取与坏输出兜底）")


PROMPT_SPECS = [
    ("chat-qa.yml", "qa_system.md", "App A `chat-qa` 系统提示词"),
    ("workflow-quiz-generate.yml", "quiz_generate_system.md", "App B `workflow-quiz-generate` 系统提示词"),
    ("workflow-quiz-grade.yml", "quiz_grade_system.md", "App C `workflow-quiz-grade` 系统提示词"),
]


def iter_refs(obj: object) -> list[str]:
    """递归取出 DSL 里所有 {{#...#}} 形态的引用（Prompt 文本、End 节点 answer 等）。"""
    if isinstance(obj, str):
        return REF_ANY.findall(obj)
    if isinstance(obj, dict):
        return [ref for value in obj.values() for ref in iter_refs(value)]
    if isinstance(obj, list):
        return [ref for item in obj for ref in iter_refs(item)]
    return []


def iter_selectors(obj: object, path: str = "graph"):
    """递归取出所有 `*_selector` 结构化选择器（如 [grade_code, score]），空列表=未配置。"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.endswith("_selector") and isinstance(value, list):
                yield f"{path}.{key}", value
            yield from iter_selectors(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            yield from iter_selectors(item, f"{path}[{index}]")


def check_refs() -> None:
    """节点 id 与 Prompt 变量引用必须能被 Dify 解析（见 NODE_ID_OK 注释里的真实教训）。"""
    print("节点 id / 变量引用（Dify 只认 [A-Za-z0-9_]）")
    for dsl_name, _md_name, _title in PROMPT_SPECS:
        dsl = load(dsl_name)
        graph = dsl["workflow"]["graph"]
        ids = {n["id"] for n in graph["nodes"]}
        for nid in sorted(ids):
            assert NODE_ID_OK.match(nid), f"{dsl_name} 节点 id 含非法字符（引用会失效）：{nid}"
        for edge in graph["edges"]:
            assert edge["source"] in ids and edge["target"] in ids, (
                f"{dsl_name} 连线指向不存在的节点：{edge['id']}"
            )
        for ref in iter_refs(graph["nodes"]):
            matched = REF_OK.match(ref)
            assert matched, f"{dsl_name} 该引用不会被 Dify 替换（会原样喂给模型）：{ref}"
            head, tail = matched.group(1), matched.group(2)
            assert tail is None or head in ids or head in SYSTEM_REFS, (
                f"{dsl_name} 引用指向不存在的节点：{ref}"
            )
        for where, selector in iter_selectors(graph["nodes"]):
            if not selector:  # 未配置（如 context.enabled=false 时 context.variable_selector 为空）
                continue
            assert selector[0] in ids or selector[0] in SYSTEM_REFS, (
                f"{dsl_name} {where} 指向不存在的节点：{selector}"
            )
        print(f"  OK：{dsl_name} 节点 id 可被引用｜连线、Prompt 引用、变量选择器都指向存在的节点")


def export_prompts() -> None:
    print("导出 Prompt → dify/prompts/")
    PROMPTS.mkdir(parents=True, exist_ok=True)
    for dsl_name, md_name, title in PROMPT_SPECS:
        dsl = load(dsl_name)
        llm = node(dsl, "llm")["data"]
        text = llm["prompt_template"][0]["text"]
        model = llm["model"]
        body = (
            f"<!-- 由 dify/dsl/{dsl_name} 自动导出，请勿手改：改 Prompt 请改 DSL 后重跑"
            " `python scripts/check_dify_dsl.py` -->\n"
            f"# {title}\n\n"
            f"- 来源：`dify/dsl/{dsl_name}` → LLM 节点\n"
            f"- 模型：`{model['provider']}` / `{model['name']}`"
            f"（temperature={model['completion_params'].get('temperature')}）\n\n"
            f"```text\n{text}\n```\n"
        )
        (PROMPTS / md_name).write_text(body, encoding="utf-8", newline="\n")
        print(f"  [OK] dify/prompts/{md_name}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    check_app_a()
    check_app_b()
    check_app_c()
    check_refs()
    export_prompts()
    print("ALL OK")
