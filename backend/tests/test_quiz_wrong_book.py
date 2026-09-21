"""出题、判分与错题本测试（docs/05 §7-§8）。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import register, unique


def _create_quiz(client: TestClient, headers: dict, kb_id: str, **overrides: Any) -> dict:
    payload = {
        "kb_id": kb_id,
        "types": ["single", "multi", "judge", "short"],
        "count": 4,
        "difficulty": "medium",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/quizzes", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _wrong_answer(question: dict) -> Any:
    if question["type"] == "judge":
        return not question["answer"]
    if question["type"] == "short":
        return "不知道"
    if question["type"] == "multi":
        return ["Z"]
    return "A" if question["answer"] != "A" else "B"


def test_quiz_generate_grade_and_wrong_book(client: TestClient, user: dict, ready_doc: dict) -> None:
    headers, kb_id = user["headers"], ready_doc["kb_id"]
    quiz = _create_quiz(client, headers, kb_id)
    assert quiz["question_count"] == 4
    assert {q["type"] for q in quiz["questions"]} <= {"single", "multi", "judge", "short"}
    assert all("answer" not in q for q in quiz["questions"])

    quiz_id = quiz["quiz_id"]
    detail = client.get(f"/api/v1/quizzes/{quiz_id}?include_answer=true", headers=headers).json()["data"]
    assert all("answer" in q and "analysis" in q for q in detail["questions"])
    assert all("sources" in q for q in detail["questions"])

    right = [{"question_id": q["id"], "answer": q["answer"]} for q in detail["questions"]]
    graded = client.post(f"/api/v1/quizzes/{quiz_id}/answers", headers=headers, json=right).json()["data"]
    assert graded["correct"] == graded["total"] == 4
    assert graded["score"] > 0
    assert graded["wrong_question_ids"] == []

    wrong = [{"question_id": q["id"], "answer": _wrong_answer(q)} for q in detail["questions"]]
    graded_wrong = client.post(f"/api/v1/quizzes/{quiz_id}/answers", headers=headers, json=wrong).json()["data"]
    assert graded_wrong["correct"] < graded_wrong["total"]
    short_item = next(item for item in graded_wrong["items"] if item["type"] == "short")
    assert short_item["score"] is not None and short_item["feedback"]

    result = client.get(f"/api/v1/quizzes/{quiz_id}/result", headers=headers).json()["data"]
    assert result["total"] == 4 and result["wrong_question_ids"]

    wb = client.get("/api/v1/wrong-book", headers=headers).json()["data"]
    assert wb["total"] == len(graded_wrong["wrong_question_ids"])
    item = wb["items"][0]
    assert item["kb_name"] and item["status"] == "unmastered"

    detail_wb = client.get(f"/api/v1/wrong-book/{item['id']}", headers=headers).json()["data"]
    assert detail_wb["answer"] is not None
    assert detail_wb["last_user_answer"] is not None
    retry = client.post(f"/api/v1/wrong-book/{item['id']}/retry", headers=headers).json()["data"]
    assert "answer" not in retry and retry["stem"]

    patched = client.patch(
        f"/api/v1/wrong-book/{item['id']}", headers=headers, json={"status": "mastered", "remark": "已掌握"}
    ).json()["data"]
    assert patched["status"] == "mastered"
    assert client.get("/api/v1/wrong-book?status=mastered", headers=headers).json()["data"]["total"] == 1
    assert client.delete(f"/api/v1/wrong-book/{item['id']}", headers=headers).status_code == 200
    assert client.get("/api/v1/wrong-book", headers=headers).json()["data"]["total"] == wb["total"] - 1


def test_quiz_validation_and_errors(client: TestClient, user: dict, ready_doc: dict) -> None:
    headers, kb_id = user["headers"], ready_doc["kb_id"]
    assert (
        client.post(
            "/api/v1/quizzes", headers=headers, json={"kb_id": kb_id, "types": [], "count": 5}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/quizzes", headers=headers, json={"kb_id": kb_id, "types": ["single"], "count": 99}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/quizzes",
            headers=headers,
            json={"kb_id": "11111111-1111-1111-1111-111111111111", "types": ["single"], "count": 3},
        ).status_code
        == 404
    )

    empty_kb = client.post(
        "/api/v1/kbs", headers=headers, json={"name": f"空库-{unique('kb')}", "visibility": "private"}
    ).json()["data"]
    empty = client.post(
        "/api/v1/quizzes", headers=headers, json={"kb_id": empty_kb["id"], "types": ["single"], "count": 3}
    )
    assert empty.status_code == 400 and "未检索到可用的知识内容" in empty.json()["message"]

    quiz = _create_quiz(client, headers, kb_id)
    bad = client.post(
        f"/api/v1/quizzes/{quiz['quiz_id']}/answers",
        headers=headers,
        json=[{"question_id": "not-in-quiz", "answer": "A"}],
    )
    assert bad.status_code == 400
    assert (
        client.get("/api/v1/quizzes/11111111-1111-1111-1111-111111111111", headers=headers).status_code == 404
    )
    listed = client.get(f"/api/v1/quizzes?kb_id={kb_id}", headers=headers).json()["data"]
    assert listed["total"] >= 1 and listed["items"][0]["kb_name"]
    assert client.delete(f"/api/v1/quizzes/{quiz['quiz_id']}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/quizzes/{quiz['quiz_id']}", headers=headers).status_code == 404


def test_short_answer_grading_and_user_isolation(client: TestClient, user: dict, ready_doc: dict) -> None:
    headers = user["headers"]
    quiz = _create_quiz(client, headers, ready_doc["kb_id"], types=["short"], count=2)
    detail = client.get(f"/api/v1/quizzes/{quiz['quiz_id']}?include_answer=true", headers=headers).json()["data"]
    answers = [
        {"question_id": q["id"], "answer": q["answer"] if isinstance(q["answer"], str) else "参考答案"}
        for q in detail["questions"]
    ]
    graded = client.post(
        f"/api/v1/quizzes/{quiz['quiz_id']}/answers", headers=headers, json=answers
    ).json()["data"]
    assert graded["total"] == 2 and all(item["score"] is not None for item in graded["items"])

    other = register(client)
    assert client.get("/api/v1/wrong-book", headers=other["headers"]).json()["data"]["total"] == 0
    assert client.get(f"/api/v1/quizzes/{quiz['quiz_id']}", headers=other["headers"]).status_code == 404


def test_quiz_material_search_filters_junk_and_dedups() -> None:
    """出题素材要丢掉「没有内容的碎片块」，按相似度排序并跨查询去重。

    真实事故：文档里的 `---` 被切成 3 字符碎片块，在 KB 的相似度阈值下反而命中 → 资料为空 →
    App B 只能按知识库名编题（题与文档无关）。
    """
    from app.services import quiz_service
    from app.services.dify.client import RetrievedSegment

    def seg(segment_id: str, content: str, score: float) -> RetrievedSegment:
        return RetrievedSegment(
            content=content,
            score=score,
            document_id="doc-1",
            document_name="二叉树遍历.md",
            segment_id=segment_id,
            position=1,
            dataset_id="ds-1",
        )

    class _Client:
        """每个查询都返回同一批结果，用于同时验证「过滤 + 去重 + 排序」。"""

        def retrieval_test(
            self, *, dataset_id: str, query: str, top_k: int, score_threshold: float
        ) -> list[RetrievedSegment]:
            assert dataset_id == "ds-1" and top_k == 6 and score_threshold == 0.5
            return [
                seg("s-junk", "---", 0.93),  # 只有装饰线：分数高但没有内容
                seg("s-real", "中序遍历二叉搜索树可以得到递增序列，这是重要性质。", 0.61),
                seg("s-thin", "# 二叉树遍历", 0.55),  # 只有标题，信息量不足
            ]

    segments = quiz_service._search_multi(_Client(), "ds-1", ["遍历", "二叉树遍历"], 6, 0.5)
    assert [item.segment_id for item in segments] == ["s-real"]


def test_quiz_material_comes_from_selected_kb_documents(
    client: TestClient, user: dict, ready_doc: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺陷回归：「出题」必须基于所选知识库的文档内容，而不是知识库名。

    修复前后端只下发 topic（≈知识库名），题目实际由 App B 里静态绑定的检索节点决定 —— 与所选
    知识库的文档无关；修复后后端把**所选知识库**检索到的原文作为 `material` 下发给 App B，
    Prompt 以【资料】为唯一出题依据（见 dify/README.md §4、docs/04 §3）。
    """
    from app.services import quiz_service
    from app.services.dify import get_dify_client

    real = get_dify_client()
    captured: dict[str, Any] = {}

    class _Spy:
        """记录 generate_questions 的入参，其余能力透传真实（mock）实现。"""

        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def generate_questions(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return real.generate_questions(**kwargs)

    monkeypatch.setattr(quiz_service, "get_dify_client", lambda: _Spy())

    kb = client.get(f"/api/v1/kbs/{ready_doc['kb_id']}", headers=user["headers"]).json()["data"]
    quiz = _create_quiz(client, user["headers"], ready_doc["kb_id"], topic="遍历", count=2)

    assert captured["dataset_ids"] == [kb["dify_dataset_id"]]  # 只在所选知识库里检索
    material = captured["material"]
    assert "中序遍历二叉搜索树可以得到递增序列" in material  # 资料 = 文档原文，而非知识库名
    assert "二叉树遍历.md" in material  # 带来源，题目可溯源
    assert captured["topic"] == "遍历"
    assert quiz["question_count"] == 2
