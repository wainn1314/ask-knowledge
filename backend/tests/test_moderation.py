"""内容审核测试：本地规则、策略路由、Dify 回调（docs/04 §6、docs/05 §9）。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.constants import AuditResult, AuditScene
from app.models.ops import AuditLog
from app.db.session import session_scope
from app.services.moderation import Moderator, rules
from tests.conftest import register


def test_normalize_defeats_simple_evasion() -> None:
    assert "买卖答案" in rules.normalize("买 卖 答 案")
    assert rules.normalize("代考！") == "代考"
    assert rules.normalize("ＡＢＣ") == "abc"
    label, words = rules.scan("请帮我买 卖 答 案")
    assert label == "illegal" and words


def test_scan_and_priority() -> None:
    assert rules.scan("这是一段正常的教学材料")[0] == rules.LABEL_SAFE
    # 违法 > 辱骂（LABEL_PRIORITY 顺序）
    label, _ = rules.scan("你这个废物，还想代考")
    assert label == "illegal"


def test_scene_policies() -> None:
    assert rules.decide(AuditScene.UPLOAD.value, "illegal") == AuditResult.BLOCK.value
    assert rules.decide(AuditScene.PARSE.value, "illegal") == AuditResult.BLOCK.value
    assert rules.decide(AuditScene.QUERY.value, "illegal") == AuditResult.BLOCK.value
    assert rules.decide(AuditScene.ANSWER.value, "illegal") == AuditResult.REVIEW.value
    assert rules.decide(AuditScene.ANSWER.value, rules.LABEL_SAFE) == AuditResult.PASS.value
    # 审核异常兜底：上传/解析 fail-closed，问答 fail-open
    assert rules.get_policy(AuditScene.UPLOAD.value).error_action == AuditResult.BLOCK.value
    assert rules.get_policy(AuditScene.QUERY.value).error_action == AuditResult.PASS.value


def test_moderator_local_verdict_and_cache() -> None:
    moderator = Moderator(provider="local", cache_minutes=5)
    blocked = moderator.check_text("教你代考", AuditScene.UPLOAD.value)
    assert blocked.blocked and blocked.label == "illegal"
    assert blocked.excerpt and "代考" not in blocked.excerpt  # 命中词脱敏

    safe = moderator.check_text("二分查找的时间复杂度是 O(log n)", AuditScene.QUERY.value)
    assert safe.passed and safe.label == rules.LABEL_SAFE

    review = moderator.check_text("教人赌博的内容", AuditScene.ANSWER.value)
    assert review.needs_review

    again = moderator.check_text("二分查找的时间复杂度是 O(log n)", AuditScene.QUERY.value)
    assert again.cached is True


def test_moderator_disabled_and_raise() -> None:
    moderator = Moderator(provider="local", enabled=False)
    assert moderator.check_text("代考", AuditScene.UPLOAD.value).provider == "disabled"

    enabled = Moderator(provider="local", cache_minutes=0)
    verdict = enabled.check_text("代考", AuditScene.UPLOAD.value)
    try:
        verdict.raise_if_blocked()
        raise AssertionError("应当抛出 50301")
    except Exception as exc:  # noqa: BLE001 - BizError
        assert getattr(exc, "code", None) == 50301


def test_moderation_callback_endpoints(client: TestClient, user: dict) -> None:
    token = {"X-Moderation-Token": "test-moderation-token"}
    ping = client.post("/api/v1/moderation", headers=token, json={"point": "ping"})
    assert ping.status_code == 200 and ping.json() == {
        "flagged": False,
        "action": "direct_output",
        "preset_response": "ok",
    }

    hit = client.post(
        "/api/v1/moderation",
        headers=token,
        json={"point": "app.moderation.input", "params": {"query": "帮我代考", "user_id": "default:u1"}},
    )
    assert hit.json()["flagged"] is True and hit.json()["action"] == "direct_output"

    output_hit = client.post(
        "/api/v1/moderation",
        headers=token,
        json={"point": "app.moderation.output", "params": {"answer": "去赌博吧", "user_id": "default:u1"}},
    )
    assert output_hit.json()["flagged"] is True

    empty = client.post(
        "/api/v1/moderation", headers=token, json={"point": "app.moderation.input", "params": {}}
    )
    assert empty.json()["flagged"] is False

    assert client.post("/api/v1/moderation", json={"point": "ping"}).status_code == 403

    # 命中会写 audit_log（按 tenant_code 前缀定位租户）
    with session_scope() as db:
        logs = db.scalars(select(AuditLog).where(AuditLog.object_type == "moderation")).all()
    assert logs, "审核命中应落库 audit_log"
    assert all(row.result in {"block", "review"} for row in logs)


def test_audit_logs_and_usage_are_admin_only(client: TestClient, user: dict) -> None:
    headers = user["headers"]
    assert client.get("/api/v1/moderation/audit-logs", headers=headers).status_code == 403
    assert client.get("/api/v1/usage", headers=headers).status_code == 403

    admin = register(client)
    from sqlalchemy import select as sa_select

    from app.core.constants import Role
    from app.db.session import session_scope as scope
    from app.models.org import AppUser

    with scope() as db:
        row = db.scalar(sa_select(AppUser).where(AppUser.id == admin["user_id"]))
        assert row is not None
        row.role = Role.OWNER.value

    logs = client.get("/api/v1/moderation/audit-logs?scene=upload", headers=admin["headers"])
    assert logs.status_code == 200 and "items" in logs.json()["data"]
    usage = client.get("/api/v1/usage?group_by=day", headers=admin["headers"])
    assert usage.status_code == 200
    assert usage.json()["data"]["group_by"] == "day"
    # group_by 只接受 day / user
    assert client.get("/api/v1/usage?group_by=week", headers=admin["headers"]).status_code == 400

    single = client.get("/api/v1/moderation/audit-logs", headers=admin["headers"]).json()["data"]["items"]
    if single:
        detail = client.get(f"/api/v1/moderation/audit-logs/{single[0]['id']}", headers=admin["headers"])
        assert detail.status_code == 200 and "raw_response" in detail.json()["data"]
    assert (
        client.get(
            "/api/v1/moderation/audit-logs/11111111-1111-1111-1111-111111111111",
            headers=admin["headers"],
        ).status_code
        == 404
    )
