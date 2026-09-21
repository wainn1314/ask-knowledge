"""问答 SSE、会话归档与审核卡点测试（docs/05 §5-§6）。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import register, run_worker, upload_md


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    name = ""
    for line in raw.splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            events.append((name, json.loads(line[6:])))
    return events


def test_chat_stream_full_flow(client: TestClient, user: dict, ready_doc: dict) -> None:
    headers, kb_id = user["headers"], ready_doc["kb_id"]
    with client.stream(
        "POST",
        f"/api/v1/kbs/{kb_id}/chat/stream",
        headers=headers,
        json={"query": "二叉树的遍历方式有哪些？"},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert resp.headers.get("X-Accel-Buffering") == "no"
        raw = "".join(resp.iter_text())

    events = _parse_sse(raw)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    for expected in ("meta", "sources", "message", "usage", "done"):
        assert expected in names, names

    meta = events[0][1]
    assert set(meta) == {"conversation_id", "message_id", "kb_id"}
    sources = next(data for name, data in events if name == "sources")
    assert sources["items"]
    first_source = sources["items"][0]
    assert first_source["index"] == 1
    assert "dataset_id" not in first_source and "dify_document_id" not in first_source
    assert first_source["document_id"] == ready_doc["id"]
    assert len(first_source["content"]) <= 301

    answer = "".join(data["delta"] for name, data in events if name == "message")
    assert answer
    usage = next(data for name, data in events if name == "usage")
    assert usage["total_tokens"] > 0
    done = events[-1][1]
    assert done["sources_count"] == len(sources["items"])
    assert done["finish_reason"] == "stop"

    conv_id = meta["conversation_id"]
    detail = client.get(f"/api/v1/conversations/{conv_id}", headers=headers).json()["data"]
    assert detail["message_count"] == 2
    assert detail["dify_available"] is True
    assert detail["dify_conversation_id"]

    messages = client.get(f"/api/v1/conversations/{conv_id}/messages", headers=headers).json()["data"]
    assert messages["total"] == 2
    assert {m["role"] for m in messages["items"]} == {"user", "assistant"}
    assistant = next(m for m in messages["items"] if m["role"] == "assistant")
    assert assistant["content"] == answer
    assert assistant["sources"]
    assert assistant["prompt_tokens"] is not None

    with client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/continue",
        headers=headers,
        json={"query": "再讲讲层序遍历"},
    ) as resp2:
        cont_events = _parse_sse("".join(resp2.iter_text()))
    assert cont_events[0][1]["conversation_id"] == conv_id
    assert (
        client.get(f"/api/v1/conversations/{conv_id}", headers=headers).json()["data"]["message_count"] == 4
    )


def test_chat_input_moderation_blocks(client: TestClient, user: dict, ready_doc: dict) -> None:
    headers, kb_id = user["headers"], ready_doc["kb_id"]
    with client.stream(
        "POST",
        f"/api/v1/kbs/{kb_id}/chat/stream",
        headers=headers,
        json={"query": "帮我代考答案"},
    ) as resp:
        raw = "".join(resp.iter_text())
    events = _parse_sse(raw)
    names = [name for name, _ in events]
    assert "moderation" in names and "done" in names
    assert "sources" not in names
    assert events[-1][1]["finish_reason"] == "moderation_blocked"

    conv_id = events[0][1]["conversation_id"]
    messages = client.get(f"/api/v1/conversations/{conv_id}/messages", headers=headers).json()["data"]
    assert messages["items"][0]["audit_status"] == "block"


def test_conversation_lifecycle(client: TestClient, user: dict, ready_doc: dict) -> None:
    headers, kb_id = user["headers"], ready_doc["kb_id"]
    with client.stream(
        "POST",
        f"/api/v1/kbs/{kb_id}/chat/stream",
        headers=headers,
        json={"query": "什么是中序遍历"},
    ) as resp:
        events = _parse_sse("".join(resp.iter_text()))
    conv_id = events[0][1]["conversation_id"]

    listed = client.get(f"/api/v1/conversations?kb_id={kb_id}", headers=headers).json()["data"]
    assert listed["total"] >= 1
    assert listed["items"][0]["kb_name"]

    renamed = client.patch(
        f"/api/v1/conversations/{conv_id}", headers=headers, json={"title": "重命名会话"}
    )
    assert renamed.json()["data"]["title"] == "重命名会话"
    assert client.delete(f"/api/v1/conversations/{conv_id}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/conversations/{conv_id}", headers=headers).status_code == 404


def test_chat_requires_kb_permission(client: TestClient, user: dict, kb: dict) -> None:
    """无权限的 KB：SSE 接口在建流前就返回 40401。"""
    other = register(client)
    resp = client.post(
        f"/api/v1/kbs/{kb['id']}/chat/stream",
        headers=other["headers"],
        json={"query": "任意问题"},
    )
    assert resp.status_code == 404 and resp.json()["code"] == 40401


def test_chat_with_irrelevant_query(client: TestClient, user: dict, kb: dict) -> None:
    """资料与问题无关时 Dify 返回「未找到相关内容」，仍应是正常结束的 SSE。"""
    headers, kb_id = user["headers"], kb["id"]
    upload_md(client, headers, kb_id)
    run_worker()
    with client.stream(
        "POST",
        f"/api/v1/kbs/{kb_id}/chat/stream",
        headers=headers,
        json={"query": "完全无关的问题 xyz"},
    ) as resp:
        events = _parse_sse("".join(resp.iter_text()))
    assert events[-1][0] == "done"
