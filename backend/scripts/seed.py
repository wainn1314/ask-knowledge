"""开发种子数据：默认租户 + 管理员 + 示例知识库（含示例文档，幂等可重复执行）。

用法（在仓库根目录执行）：
    python backend/scripts/seed.py
    python backend/scripts/seed.py --email me@example.com --password secret123 --no-doc
"""

from __future__ import annotations

import argparse
import io
import pathlib
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select  # noqa: E402

from app.api.deps import CurrentUser, KbContext  # noqa: E402
from app.core.constants import KbRole, Role  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.engine import init_db  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.models.knowledge import Kb  # noqa: E402
from app.models.org import AppUser, Tenant  # noqa: E402
from app.services import document_service, kb_service  # noqa: E402
from app.schemas.kb import KbCreateIn  # noqa: E402
from app.workers.runner import process_one  # noqa: E402

SAMPLE_KB_NAME = "示例知识库 · 数据结构"
SAMPLE_DOC = """# 二叉树遍历

二叉树遍历是指按某种顺序访问树中每个节点一次且仅一次。常见有四种遍历方式：

1. 前序遍历：根 → 左 → 右
2. 中序遍历：左 → 根 → 右
3. 后序遍历：左 → 右 → 根
4. 层序遍历：按层从左到右依次访问

## 常见性质

- 中序遍历二叉搜索树可得到递增序列；
- 任意二叉树，已知中序 + 前序（或后序）即可唯一还原；
- 层序遍历使用队列实现，时间复杂度 O(n)。

## 时间复杂度

| 遍历方式 | 时间复杂度 | 空间复杂度 |
|---|---|---|

| 递归实现 | O(n) | O(h) |
| 迭代实现 | O(n) | O(h) |
"""


def _ensure_tenant(db) -> Tenant:  # noqa: ANN001
    tenant = db.scalar(select(Tenant).where(Tenant.code == "default"))
    if tenant is None:
        tenant = Tenant(name="默认租户", code="default", status=1)
        db.add(tenant)
        db.flush()
        print(f"[+] 创建租户: {tenant.name} ({tenant.code})")
    return tenant


def _ensure_owner(db, tenant: Tenant, email: str, password: str) -> AppUser:  # noqa: ANN001
    user = db.scalar(
        select(AppUser).where(AppUser.tenant_id == tenant.id, func.lower(AppUser.email) == email.lower())
    )
    if user is not None:
        print(f"[=] 已存在用户: {user.email}（role={user.role}）")
        return user
    user = AppUser(
        tenant_id=str(tenant.id),
        email=email.lower(),
        password_hash=hash_password(password),
        nickname="管理员",
        role=Role.OWNER.value,
        status=1,
    )
    db.add(user)
    db.flush()
    print(f"[+] 创建管理员: {user.email}  密码: {password}")
    return user


def _user_ctx(user: AppUser, tenant: Tenant) -> CurrentUser:
    return CurrentUser(
        id=str(user.id),
        tenant_id=str(tenant.id),
        tenant_code=tenant.code,
        role=user.role,
        email=user.email,
        nickname=user.nickname,
    )


def _ensure_kb(db, user: AppUser, tenant: Tenant, with_doc: bool) -> Kb:  # noqa: ANN001
    kb = db.scalar(
        select(Kb).where(
            Kb.tenant_id == tenant.id,
            func.lower(Kb.name) == SAMPLE_KB_NAME.lower(),
            Kb.is_deleted.is_(False),
        )
    )
    ctx_user = _user_ctx(user, tenant)
    if kb is None:
        kb = kb_service.create_kb(
            db,
            ctx_user,
            KbCreateIn(name=SAMPLE_KB_NAME, description="开箱即用的演示知识库（含一棵二叉树的遍历资料）"),
        )
        print(f"[+] 创建知识库: {kb.name} (dify_dataset_id={kb.dify_dataset_id})")
    else:
        print(f"[=] 已存在知识库: {kb.name}")

    if not with_doc:
        return kb

    ctx = KbContext(kb=kb, role=KbRole.OWNER.value, user=ctx_user)
    result = document_service.upload_documents(
        db, ctx, [("二叉树遍历.md", SAMPLE_DOC.encode("utf-8"))]
    )
    if result["accepted"]:
        print(f"[+] 已入队示例文档: {result['accepted'][0]['filename']}")
    else:
        print(f"[=] 示例文档未重复上传: {result['rejected']}")
    return kb


def _drain_worker(limit: int = 5) -> int:
    """执行解析 → 审核 → 入库（内联 worker 关闭时脚本自己跑一遍，便于立刻可问答）。"""
    done = 0
    for _ in range(limit):
        with session_scope() as db:
            task = process_one(db, "seed-worker")
        if task is None:
            break
        done += 1
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化开发种子数据")
    parser.add_argument("--email", default="owner@example.com")
    parser.add_argument("--password", default="secret123")
    parser.add_argument("--no-doc", action="store_true", help="只建库，不上传示例文档")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        tenant = _ensure_tenant(db)
        user = _ensure_owner(db, tenant, args.email, args.password)
        _ensure_kb(db, user, tenant, with_doc=not args.no_doc)

    processed = _drain_worker()
    if processed:
        print(f"[+] worker 处理任务: {processed} 个（文档已解析并入库）")
    print("\n完成。启动服务：uvicorn app.main:app --reload --port 8000（工作目录 backend/）")
    print(f"登录：POST /api/v1/auth/login  {{\"email\": \"{args.email}\", \"password\": \"{args.password}\"}}")


if __name__ == "__main__":
    main()
