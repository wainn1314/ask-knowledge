"""Dify HTTP 契约测试：用 httpx.MockTransport 校验请求路径 / 报文 / SSE 解析。

保证 `DIFY_MODE=http` 与 mock 行为一致（同一契约，切换零改动）。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.constants import DifyApp
from app.core.errors import BizError
from app.services.dify.http_client import HttpDifyClient
from app.services.dify.workflow_client import describe_quiz_types, reset_input_form_cache

BASE = "http://dify.test/v1"


def build_client(handler: Any) -> HttpDifyClient:
    client = HttpDifyClient(base_url=BASE, timeout=5.0)
    client._client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(handler))
    return client


@pytest.fixture(autouse=True)
def _api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "dify_app_api_key", "app-chat-key", raising=False)
    monkeypatch.setattr(settings, "dify_quiz_workflow_api_key", "app-quiz-key", raising=False)
    monkeypatch.setattr(settings, "dify_grade_workflow_api_key", "app-grade-key", raising=False)
    monkeypatch.setattr(settings, "dify_dataset_api_key", "dataset-key", raising=False)
    # 契约测试默认走 app 编排（DIFY_CHAT_MODE=rag 时 chat_stream 会先调 /datasets/{id}/retrieve，
    # 由需要该路径的用例自行改写）
    monkeypatch.setattr(settings, "dify_chat_mode", "app", raising=False)
    reset_input_form_cache()


def test_dataset_lifecycle_paths_and_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST" and request.url.path.endswith("/datasets"):
            return httpx.Response(200, json={"id": "ds-1", "name": "数据结构"})
        if request.method == "GET" and request.url.path.endswith("/indexing-status"):
            return httpx.Response(200, json={"data": {"indexing_status": "completed"}})
        if request.method == "GET" and request.url.path.endswith("/segments"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "s1", "position": 1, "content": "内容", "word_count": 3}],
                    "total": 1,
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "not found"})

    client = build_client(handler)
    created = client.create_dataset(name="数据结构", description="复习")
    assert created["id"] == "ds-1"
    assert seen[0].headers["Authorization"] == "Bearer dataset-key"
    body = json.loads(seen[0].content)
    assert body["name"] == "数据结构" and body["indexing_technique"] == "high_quality"

    assert client.get_indexing_status(dataset_id="ds-1", document_id="d1") == "completed"

    segments = client.list_segments(dataset_id="ds-1", document_id="d1", page=1, limit=20)
    assert segments["total"] == 1
    assert segments["items"][0]["segment_id"] == "s1"

    client.delete_document(dataset_id="ds-1", document_id="d1")
    client.delete_dataset(dataset_id="ds-1")
    assert seen[-1].method == "DELETE"


def test_create_document_by_text_maps_chunk_config() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200, json={"document": {"id": "doc-1", "name": "第3章.md", "indexing_status": "waiting"}}
        )

    client = build_client(handler)
    result = client.create_document_by_text(
        dataset_id="ds-1", name="第3章.md", text="# 内容", chunk_size=512, chunk_overlap=50
    )
    assert result["document_id"] == "doc-1"
    assert result["indexing_status"] == "waiting"
    rules = captured["process_rule"]["rules"]
    assert rules["segmentation"]["max_tokens"] == 512
    assert rules["segmentation"]["chunk_overlap"] == 50
    assert rules["segmentation"]["separator"] == "\n\n"
    assert captured["indexing_technique"] == "high_quality"


def test_retrieval_test_parses_nested_records() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "score": 0.8342,
                        "segment": {
                            "id": "seg-1",
                            "position": 12,
                            "content": "中序遍历：左-根-右",
                            "document": {
                                "id": "dify-doc-1",
                                "name": "第3章.pdf",
                                "dataset_id": "ds-1",
                            },
                        },
                    }
                ]
            },
        )

    client = build_client(handler)
    segments = client.retrieval_test(dataset_id="ds-1", query="中序遍历", top_k=3, score_threshold=0.5)
    assert len(segments) == 1
    seg = segments[0]
    assert seg.segment_id == "seg-1" and seg.document_name == "第3章.pdf"
    assert seg.position == 12 and seg.score == pytest.approx(0.8342)


def test_get_indexing_status_prefers_batch_and_parses_list() -> None:
    """新版 Dify：状态按批次查询，`data` 是数组（老版是对象）。"""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/documents/batch-2026/indexing-status"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "doc-1",
                            "indexing_status": "completed",
                            "completed_segments": 4,
                            "total_segments": 4,
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"code": "not_found", "message": "Documents not found."})

    client = build_client(handler)
    status = client.get_indexing_status(dataset_id="ds-1", document_id="doc-1", batch="batch-2026")
    assert status == "completed"
    assert paths == ["/v1/datasets/ds-1/documents/batch-2026/indexing-status"]


def test_get_indexing_status_falls_back_to_document_detail() -> None:
    """老版/异常：batch 与 document_id 两个路径都 404 时，退回文档详情接口。"""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/documents/doc-1"):
            return httpx.Response(200, json={"id": "doc-1", "indexing_status": "indexing"})
        return httpx.Response(404, json={"code": "not_found", "message": "Documents not found."})

    client = build_client(handler)
    status = client.get_indexing_status(dataset_id="ds-1", document_id="doc-1", batch="batch-2026")
    assert status == "indexing"
    assert paths[-1] == "/v1/datasets/ds-1/documents/doc-1"


def test_get_indexing_status_raises_when_document_missing() -> None:
    """三种路径都查不到时，仍然抛出 50201（上层会据此重建文档）。"""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "not_found", "message": "Documents not found."})

    client = build_client(handler)
    with pytest.raises(BizError) as exc:
        client.get_indexing_status(dataset_id="ds-1", document_id="doc-1", batch="batch-2026")
    assert exc.value.code == 50201


def test_retrieval_test_empty_collection_returns_empty() -> None:
    """空知识库（向量集合未建立）时 Dify 报 400，应按空结果处理而不是 50201。"""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "invalid_param",
                "message": (
                    "Unexpected Response: 404 (Not Found)\nRaw response content:\n"
                    "b'{\"status\":{\"error\":\"Collection not found\"}}'"
                ),
                "status": 400,
            },
        )

    client = build_client(handler)
    assert client.retrieval_test(dataset_id="ds-empty", query="验证口令", top_k=5, score_threshold=0.0) == []


def test_retrieval_test_other_errors_still_raise() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "invalid_param", "message": "query is required"})

    client = build_client(handler)
    with pytest.raises(BizError) as exc:
        client.retrieval_test(dataset_id="ds-1", query="x", top_k=5, score_threshold=0.0)
    assert exc.value.code == 50201


def test_chat_stream_sse_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat-messages")
        body = json.loads(request.content)
        assert body["response_mode"] == "streaming"
        assert body["inputs"]["kb_ids"] == ["ds-1"]
        assert body["user"] == "default:u1"
        payloads = [
            {"event": "message", "conversation_id": "c1", "message_id": "m1", "answer": "二叉树"},
            {"event": "message", "conversation_id": "c1", "message_id": "m1", "answer": "有三种遍历 [1]"},
            {
                "event": "message_end",
                "conversation_id": "c1",
                "message_id": "m1",
                "metadata": {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
                "retriever_resources": [
                    {
                        "dataset_id": "ds-1",
                        "document_id": "dify-doc-1",
                        "document_name": "第3章.pdf",
                        "segment_id": "seg-1",
                        "position": 3,
                        "score": 0.81234,
                        "content": "中序遍历…",
                    }
                ],
            },
        ]
        body_text = "".join(f"data: {json.dumps(p)}\n\n" for p in payloads) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body_text, headers={"content-type": "text/event-stream"})

    client = build_client(handler)
    events = list(client.chat_stream(query="遍历方式", dataset_ids=["ds-1"], user="default:u1"))
    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert types[-1] == "end"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "二叉树有三种遍历 [1]"
    sources = next(e for e in events if e["type"] == "sources")
    assert sources["items"][0]["segment_id"] == "seg-1"
    assert sources["items"][0]["index"] == 1
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["total_tokens"] == 15
    assert events[-1]["conversation_id"] == "c1" and events[-1]["message_id"] == "m1"


def test_chat_stream_rag_mode_retrieves_then_injects_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DIFY_CHAT_MODE=rag`：后端先按知识库 dataset 检索，再把资料注入提问。

    对应线上问题：advanced-chat 应用会忽略 `inputs.kb_ids`，必须由后端完成按库检索。
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "dify_chat_mode", "rag", raising=False)
    paths: list[str] = []
    chat_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/datasets/ds-1/retrieve"):
            assert request.headers["Authorization"] == "Bearer dataset-key"
            return httpx.Response(
                200,
                json={
                    "query": "中序遍历有什么性质？",
                    "records": [
                        {
                            "score": 0.91,
                            "segment": {
                                "id": "seg-9",
                                "position": 2,
                                "content": "中序遍历二叉搜索树可以得到递增序列",
                                "document": {"id": "doc-9", "name": "遍历.md", "dataset_id": "ds-1"},
                            },
                        }
                    ],
                },
            )
        assert request.url.path.endswith("/chat-messages")
        chat_body.update(json.loads(request.content))
        payloads = [
            {"event": "message", "conversation_id": "c9", "message_id": "m9", "answer": "可以得到递增序列 [1]"},
            {
                "event": "message_end",
                "conversation_id": "c9",
                "message_id": "m9",
                "metadata": {"usage": {"prompt_tokens": 20, "completion_tokens": 8}},
                # Dify 侧即使回了引用也不再采用，避免与后端检索结果重复
                "retriever_resources": [
                    {"dataset_id": "ds-1", "document_id": "doc-9", "segment_id": "seg-9", "content": "x"}
                ],
            },
        ]
        body_text = "".join(f"data: {json.dumps(p, ensure_ascii=False)}\n\n" for p in payloads)
        return httpx.Response(
            200, text=body_text + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
        )

    client = build_client(handler)
    events = list(client.chat_stream(query="中序遍历有什么性质？", dataset_ids=["ds-1"], user="default:u1"))

    assert paths == ["/v1/datasets/ds-1/retrieve", "/v1/chat-messages"]
    # 注入模板同时带上检索资料与原始问题（并保留 [n] 编号供模型引用）
    assert "中序遍历二叉搜索树可以得到递增序列" in chat_body["query"]
    assert "中序遍历有什么性质？" in chat_body["query"]
    assert "[1]" in chat_body["query"]
    assert chat_body["inputs"]["kb_ids"] == ["ds-1"]
    # 引用来源由后端预取，只下发一次
    sources = [e for e in events if e["type"] == "sources"]
    assert len(sources) == 1 and sources[0]["prefetch"] is True
    assert sources[0]["items"][0]["segment_id"] == "seg-9"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "可以得到递增序列 [1]"
    assert events[-1]["type"] == "end" and events[-1]["conversation_id"] == "c9"


def test_chat_stream_rag_mode_without_hits_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """检索为空时不调用大模型，直接返回标准提示（避免无依据作答）。"""
    from app.core.config import settings
    from app.services.dify.chat_client import NO_CONTEXT_ANSWER

    monkeypatch.setattr(settings, "dify_chat_mode", "rag", raising=False)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"records": []})

    client = build_client(handler)
    events = list(client.chat_stream(query="与资料无关的问题", dataset_ids=["ds-1"], user="default:u1"))

    assert paths == ["/v1/datasets/ds-1/retrieve"]  # 没有 /chat-messages 调用
    assert "".join(e["text"] for e in events if e["type"] == "delta") == NO_CONTEXT_ANSWER
    assert [e["type"] for e in events if e["type"] == "sources"] == []
    assert events[-1] == {
        "type": "end",
        "conversation_id": None,
        "message_id": None,
        "finish_reason": "no_context",
    }


def test_chat_stream_rag_mode_retrieve_failure_emits_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """检索失败（如 dataset 已被删除）要给出明确错误，而不是「未找到相关资料」。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "dify_chat_mode", "rag", raising=False)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "dataset not found"})

    client = build_client(handler)
    events = list(client.chat_stream(query="任意问题", dataset_ids=["ds-gone"], user="default:u1"))

    assert len(events) == 1
    assert events[0]["type"] == "error" and events[0]["code"] == 50201
    assert "知识库检索失败" in events[0]["message"]


def test_chat_stream_maps_error_event() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        payload = {"event": "error", "message": "quota exceeded"}
        return httpx.Response(
            200,
            text=f"data: {json.dumps(payload)}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = build_client(handler)
    events = list(client.chat_stream(query="q", dataset_ids=["ds-1"], user="default:u1"))
    assert events[0]["type"] == "error" and events[0]["code"] == 50201


def test_workflow_run_and_outputs_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_mode"] == "blocking"
        outputs = {
            "questions": json.dumps(
                [
                    {
                        "seq": 1,
                        "type": "single",
                        "stem": "中序遍历顺序？",
                        "options": [
                            {"key": "A", "text": "左-根-右"},
                            {"key": "B", "text": "根-左-右"},
                            {"key": "C", "text": "左-右-根"},
                            {"key": "D", "text": "右-根-左"},
                        ],
                        "answer": "A",
                        "analysis": "中序先左",
                        "difficulty": "easy",
                        "source_index": [1],
                    }
                ],
                ensure_ascii=False,
            )
        }
        return httpx.Response(
            200,
            json={
                "workflow_run_id": "run-1",
                "data": {
                    "status": "succeeded",
                    "outputs": outputs,
                    "total_tokens": 128,
                    "elapsed_time": 1.2,
                },
            },
        )

    client = build_client(handler)
    result = client.generate_questions(
        dataset_ids=["ds-1"],
        types=["single"],
        count=1,
        difficulty="easy",
        topic="遍历",
        user="default:u1",
    )
    assert result["workflow_run_id"] == "run-1"
    assert result["questions"][0]["answer"] == "A"
    assert result["questions"][0]["difficulty"] == "easy"
    assert result["total_tokens"] == 128


def test_grade_short_answer_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer app-grade-key"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "score": 82.5,
                        "hit_points": ["定义"],
                        "miss_points": ["顺序"],
                        "comment": "基本正确",
                    },
                }
            },
        )

    client = build_client(handler)
    result = client.grade_short_answer(
        stem="什么是遍历",
        reference_answer="按顺序访问",
        grading_points=["定义"],
        user_answer="顺序访问",
        user="default:u1",
    )
    assert result["score"] == 82.5
    assert result["hit_points"] == ["定义"]
    assert result["comment"] == "基本正确"


def test_grade_short_answer_accepts_json_wrapped_output() -> None:
    """App C 的 End 节点只输出一个 JSON 字符串时也要能解析（否则每题都被判 0 分）。"""
    wrapped = json.dumps(
        {
            "score": "75",
            "hit_points": "定义；顺序",
            "miss_points": "复杂度",
            "comment": "要点基本齐全",
        },
        ensure_ascii=False,
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"status": "succeeded", "outputs": {"text": wrapped}}},
        )

    result = build_client(handler).grade_short_answer(
        stem="什么是遍历",
        reference_answer="按顺序访问",
        grading_points=None,
        user_answer="顺序访问",
        user="default:u1",
    )
    assert result["score"] == 75.0
    assert result["hit_points"] == ["定义", "顺序"]
    assert result["miss_points"] == ["复杂度"]
    assert result["comment"] == "要点基本齐全"


def test_errors_map_to_50201() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = build_client(handler)
    with pytest.raises(BizError) as exc:
        client.create_dataset(name="x")
    assert exc.value.code == 50201

    from app.core.config import settings

    original = settings.dify_dataset_api_key
    settings.dify_dataset_api_key = ""
    try:
        with pytest.raises(BizError) as exc2:
            client.create_dataset(name="x")
        assert exc2.value.code == 50201 and "API Key" in exc2.value.message
    finally:
        settings.dify_dataset_api_key = original


def test_400_surfaces_dify_reason() -> None:
    """线上 400 回归：错误信息要带上 Dify 给的原因，不能只有一句「Dify 返回 400」。"""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "invalid_param",
                "message": (
                    "3 validation errors for HitTestingPayload\n"
                    "retrieval_model.search_method\n"
                    "  Input should be 'semantic_search', 'full_text_search' or 'hybrid_search'\n"
                    "  For further information visit https://errors.pydantic.dev/2.12/v/enum"
                ),
                "status": 400,
            },
        )

    client = build_client(handler)
    with pytest.raises(BizError) as exc:
        client.retrieval_test(dataset_id="ds-1", query="二叉树")

    msg = exc.value.message
    assert exc.value.code == 50201
    assert msg.startswith("Dify 返回 400（POST /datasets/ds-1/retrieve）")
    assert "validation errors for HitTestingPayload" in msg
    assert "errors.pydantic.dev" not in msg  # 文档链接对用户没用，去掉
    assert "\n" not in msg  # 压成一行，便于前端提示
    assert exc.value.detail["status"] == 400


def test_workflow_error_status_raises() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": {"status": "failed", "error": "quota exceeded", "outputs": {}}}
        )

    client = build_client(handler)
    with pytest.raises(BizError) as exc:
        client.run_workflow(app=DifyApp.QUIZ.value, inputs={}, user="default:u1")
    assert exc.value.code == 50201 and "failed" in exc.value.message


# ── 出题入参自适应（线上 400 回归：真实 App B 的 Start 变量是 type/count/difficulty） ──
def _quiz_handler(
    declared: list[dict[str, Any]], captured: dict[str, Any], outputs: dict[str, Any]
) -> Any:
    """`GET /parameters` 返回声明的 Start 变量；`POST /workflows/run` 记录报文并回固定 outputs。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/parameters"):
            return httpx.Response(200, json={"user_input_form": declared})
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "workflow_run_id": "run-quiz",
                "data": {"status": "succeeded", "outputs": outputs, "total_tokens": 42},
            },
        )

    return handler


def test_generate_questions_follows_declared_start_variables() -> None:
    """真实 App B：只声明 type(段落)/count(数字)/difficulty(下拉) → 只发这三个，type 为中文题型描述。"""
    captured: dict[str, Any] = {}
    outputs = {
        "questions": json.dumps(
            [
                {
                    "id": 1,
                    "type": "单选题",
                    "question": "下列关于栈的说法正确的是？",
                    "options": ["A. 先进先出", "B. 后进先出", "C. 随机存取", "D. 只能顺序访问"],
                    "answer": "B",
                    "explanation": "栈是后进先出结构。",
                }
            ],
            ensure_ascii=False,
        )
    }
    declared = [
        {"select": {"variable": "difficulty", "type": "select", "required": True}},
        {"number": {"variable": "count", "type": "number", "required": True}},
        {"paragraph": {"variable": "type", "type": "paragraph", "required": True}},
    ]
    client = build_client(_quiz_handler(declared, captured, outputs))
    result = client.generate_questions(
        dataset_ids=["ds-1"],
        types=["single", "short"],
        count=3,
        difficulty="hard",
        topic="栈",
        user="default:u1",
    )

    assert captured["inputs"] == {
        "type": "单选题、简答题（主题：栈）",
        "count": 3,
        "difficulty": "hard",
    }
    assert result["questions"][0]["stem"] == "下列关于栈的说法正确的是？"
    assert result["questions"][0]["options"][0] == {"key": "A", "text": "先进先出"}
    assert result["questions"][0]["analysis"] == "栈是后进先出结构。"


def test_generate_questions_sends_backend_material_as_context() -> None:
    """App B 声明 context（资料）时，后端下发「按所选知识库检索到的原文」→ 题目出自文档。

    线上缺陷回归：App B 的「知识检索」节点只能静态勾选某个库，题目只跟 topic（≈知识库名）
    有关、与所选知识库的文档无关；修复后【资料】由后端检索后经 context 下发。
    """
    captured: dict[str, Any] = {}
    outputs = {
        "questions": json.dumps(
            [
                {
                    "id": 1,
                    "type": "单选题",
                    "question": "中序遍历二叉搜索树可以得到什么序列？",
                    "options": ["A. 递减序列", "B. 递增序列", "C. 随机序列", "D. 层级序列"],
                    "answer": "B",
                    "explanation": "资料里写着「中序遍历二叉搜索树可以得到递增序列」。",
                }
            ],
            ensure_ascii=False,
        )
    }
    declared = [
        {"paragraph": {"variable": "type", "type": "paragraph", "required": True}},
        {"number": {"variable": "count", "type": "number", "required": True}},
        {"paragraph": {"variable": "topic", "type": "paragraph"}},
        {"paragraph": {"variable": "context", "type": "paragraph"}},
    ]
    material = "[1]（二叉树遍历.md）\n中序遍历二叉搜索树可以得到递增序列。"
    client = build_client(_quiz_handler(declared, captured, outputs))
    result = client.generate_questions(
        dataset_ids=["ds-1"],
        types=["single"],
        count=1,
        difficulty="medium",
        topic="二叉树遍历",
        material=material,
        user="default:u1",
    )

    assert captured["inputs"]["context"] == material
    assert captured["inputs"]["topic"] == "二叉树遍历"  # 资料走 context，topic 只留出题重点
    assert captured["inputs"]["type"] == "单选题"
    assert "kb_ids" not in captured["inputs"]
    assert result["questions"][0]["stem"] == "中序遍历二叉搜索树可以得到什么序列？"


def test_generate_questions_inlines_material_when_context_undeclared() -> None:
    """老版本 App B（只声明 type/count/difficulty/topic）也不能「凭空出题」：资料并入 topic。

    退化路径只是过渡手段，正式口径见 dify/README.md §4（开始节点补 context 段落变量）。
    """
    captured: dict[str, Any] = {}
    outputs = {
        "questions": [
            {"seq": 1, "type": "judge", "stem": "中序遍历二叉搜索树可以得到递增序列。", "answer": "正确"}
        ]
    }
    declared = [
        {"select": {"variable": "difficulty", "type": "select", "required": True}},
        {"number": {"variable": "count", "type": "number", "required": True}},
        {"paragraph": {"variable": "type", "type": "paragraph", "required": True}},
        {"paragraph": {"variable": "topic", "type": "paragraph"}},
    ]
    client = build_client(_quiz_handler(declared, captured, outputs))
    client.generate_questions(
        dataset_ids=["ds-1"],
        types=["judge"],
        count=1,
        difficulty="easy",
        topic="二叉树遍历",
        material="中序遍历二叉搜索树可以得到递增序列。",
        user="default:u1",
    )

    topic = captured["inputs"]["topic"]
    assert topic.startswith("【资料·出题唯一依据】")
    assert "中序遍历二叉搜索树可以得到递增序列。" in topic
    assert topic.endswith("【出题重点】二叉树遍历")

    # 超长资料：并入 topic 时截断（既保证题源，又别把检索节点的查询词撑爆）
    captured.clear()
    client.generate_questions(
        dataset_ids=["ds-1"],
        types=["judge"],
        count=1,
        difficulty="easy",
        topic="二叉树遍历",
        material="长" * 9000,
        user="default:u1",
    )
    assert len(captured["inputs"]["topic"]) < 3300


def test_generate_questions_without_material_keeps_old_behavior() -> None:
    """没传资料时（如老调用方）报文与修复前一致，避免契约漂移。"""
    captured: dict[str, Any] = {}
    declared = [
        {"paragraph": {"variable": "type", "type": "paragraph", "required": True}},
        {"number": {"variable": "count", "type": "number", "required": True}},
        {"paragraph": {"variable": "topic", "type": "paragraph"}},
        {"paragraph": {"variable": "context", "type": "paragraph"}},
    ]
    outputs = {
        "questions": [
            {"seq": 1, "type": "judge", "stem": "栈是后进先出结构。", "answer": "正确"}
        ]
    }
    client = build_client(_quiz_handler(declared, captured, outputs))
    client.generate_questions(
        dataset_ids=["ds-1"],
        types=["judge"],
        count=1,
        difficulty="medium",
        topic="栈",
        user="default:u1",
    )

    assert captured["inputs"]["context"] == ""
    assert captured["inputs"]["topic"] == "栈"


def test_generate_questions_keeps_design_contract_when_declared() -> None:
    """设计契约（docs/04 §3）：声明 kb_ids/types/topic 时按数组发，且不再夹带 type 描述。"""
    captured: dict[str, Any] = {}
    declared = [
        {"paragraph": {"variable": "kb_ids", "type": "array"}},
        {"paragraph": {"variable": "types", "type": "array"}},
        {"number": {"variable": "count", "type": "number"}},
        {"select": {"variable": "difficulty", "type": "select"}},
        {"paragraph": {"variable": "topic", "type": "paragraph"}},
    ]
    outputs = {
        "questions": [
            {"seq": 1, "type": "judge", "stem": "栈是先进先出结构。", "answer": "错误"}
        ]
    }
    client = build_client(_quiz_handler(declared, captured, outputs))
    result = client.generate_questions(
        dataset_ids=["ds-1", "ds-2"],
        types=["judge"],
        count=1,
        difficulty="medium",
        topic="栈",
        user="default:u1",
    )

    assert captured["inputs"] == {
        "kb_ids": ["ds-1", "ds-2"],
        "types": ["judge"],
        "count": 1,
        "difficulty": "medium",
        "topic": "栈",
    }
    assert result["questions"][0]["type"] == "judge"
    assert result["questions"][0]["answer"] is False




def test_generate_questions_falls_back_to_union_when_parameters_fails() -> None:
    """探针失败（旧版本/网络抖动）时发并集报文：Dify 忽略未声明变量，两种契约都能跑。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/parameters"):
            return httpx.Response(500, text="boom")
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "questions": [
                            {
                                "type": "single",
                                "stem": "x",
                                "options": ["a", "b", "c", "d"],
                                "answer": "A",
                            }
                        ]
                    },
                }
            },
        )

    client = build_client(handler)
    client.generate_questions(
        dataset_ids=["ds-1"],
        types=["single"],
        count=5,
        difficulty="easy",
        topic=None,
        user="default:u1",
    )
    assert captured["inputs"]["type"] == "单选题"
    assert captured["inputs"]["types"] == ["single"]
    assert captured["inputs"]["kb_ids"] == ["ds-1"]
    assert captured["inputs"]["topic"] == "核心知识点"
    assert captured["inputs"]["count"] == 5


def test_parse_real_app_b_chinese_questions() -> None:
    """真实 App B 报文（question/explanation/选项带 "A." 前缀/中文题型）→ 内部结构。"""
    outputs = {
        "success": "True",
        "error": "",
        "questions": json.dumps(
            [
                {
                    "id": 1,
                    "type": "单选题",
                    "question": "JSON 值可以是下列哪一项？",
                    "options": ["A. 字符串", "B. undefined", "C. 单引号字符串", "D. 注释"],
                    "answer": "A",
                    "explanation": "JSON 值支持字符串、数字、布尔、null、数组、对象。",
                },
                {
                    "id": 2,
                    "type": "多选题",
                    "question": "下列属于 JSON 支持的数据类型的有？",
                    "options": ["A. 字符串", "B. 数字", "C. 布尔值", "D. undefined"],
                    "answer": ["A", "B", "C"],
                    "explanation": "JSON 不支持 undefined。",
                },
                {
                    "id": 3,
                    "type": "判断题",
                    "question": "JSON 标准支持注释。",
                    "options": [],
                    "answer": "错误",
                    "explanation": "JSON 标准不支持注释。",
                },
                {
                    "id": 4,
                    "type": "填空题",
                    "question": "JSON 对象由一对_____包围。",
                    "options": [],
                    "answer": "花括号 {}",
                    "explanation": "对象用 {} 包裹。",
                },
            ],
            ensure_ascii=False,
        ),
    }

    from app.services.dify.mapping import parse_workflow_questions

    questions = parse_workflow_questions(outputs)
    assert [q["type"] for q in questions] == ["single", "multi", "judge", "short"]
    assert questions[0]["options"][1] == {"key": "B", "text": "undefined"}
    assert questions[1]["answer"] == ["A", "B", "C"]
    assert questions[2]["answer"] is False and questions[2]["options"] is None
    assert questions[3]["answer"] == "花括号 {}" and questions[3]["options"] is None
    assert questions[0]["analysis"].startswith("JSON 值支持")


def test_workflow_self_reported_failure_surfaces_reason() -> None:
    """Workflow 自报 success=false：错误信息里带上 error 文本，便于定位。"""
    declared = [{"paragraph": {"variable": "type", "type": "paragraph"}}]
    captured: dict[str, Any] = {}
    client = build_client(
        _quiz_handler(declared, captured, {"success": "False", "error": "LLM 节点超时", "questions": ""})
    )
    with pytest.raises(BizError) as exc:
        client.generate_questions(
            dataset_ids=["ds-1"],
            types=["single"],
            count=3,
            difficulty="easy",
            topic=None,
            user="default:u1",
        )
    assert exc.value.code == 50201
    assert "LLM 节点超时" in exc.value.message


def test_describe_quiz_types_maps_each_type() -> None:
    assert describe_quiz_types(["single", "multi", "judge", "short"]) == "单选题、多选题、判断题、简答题"
    assert describe_quiz_types([]) == "单选题"
