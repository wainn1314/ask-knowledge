"""Schema 自检：从 docs/03-数据库设计.md 提取 DDL，做语法与多租户约定校验。

用法：  python scripts/check_schema.py
可选依赖：  pip install sqlglot   （缺失时跳过语法解析，仅做结构化检查）
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "03-数据库设计.md"


def extract_sql(path: pathlib.Path) -> str:
    """抽取 markdown 中所有 ```sql 代码块内容。"""
    blocks: list[str] = []
    inside = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```sql"):
            inside = True
            continue
        if inside and line.startswith("```"):
            inside = False
            continue
        if inside:
            blocks.append(line)
    return "\n".join(blocks)


def check_syntax(sql: str) -> list[str]:
    try:
        import sqlglot
    except ImportError:
        print("  [skip] 未安装 sqlglot，跳过语法解析（pip install sqlglot）")
        return []
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception as exc:  # noqa: BLE001 - 需要把解析错误原样报出
        return [f"SQL 解析失败: {type(exc).__name__}: {exc}"]
    print(f"  [pass] sqlglot 解析通过，语句数={len(statements)}")
    return []


def check_structure(sql: str) -> list[str]:
    errors: list[str] = []
    tables: dict[str, str] = {
        m.group(1): m.group(2)
        for m in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, re.S)
    }
    if not tables:
        return ["未解析到任何 CREATE TABLE"]

    indexes = re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+) ON (\w+)", sql)

    # 1) 多租户：除 tenant 外每张业务表必须有 tenant_id
    for name, body in tables.items():
        if name != "tenant" and not re.search(r"\btenant_id\b", body):
            errors.append(f"表 {name} 缺失 tenant_id")

    # 2) 索引引用的表与列必须存在
    for idx, tbl in indexes:
        body = tables.get(tbl)
        if body is None:
            errors.append(f"索引 {idx} 引用不存在的表 {tbl}")
            continue
        cols = set(re.findall(r"^\s{4}(\w+)\s", body, re.M))
        m = re.search(rf"CREATE (?:UNIQUE )?INDEX {idx} ON \w+\(([^)]*)\)", sql)
        for col in (c.strip().split()[0] for c in m.group(1).split(",")) if m else []:
            col = col.replace("lower(", "").rstrip(")")
            if col and col not in cols:
                errors.append(f"索引 {idx} 引用 {tbl}.{col} 不存在")

    # 3) Dify 映射字段齐全
    for field, tbl in [
        ("dify_dataset_id", "kb"),
        ("dify_document_id", "document"),
        ("dify_conversation_id", "conversation"),
        ("dify_workflow_run_id", "quiz"),
        ("dify_message_id", "message"),
    ]:
        if not re.search(rf"\b{field}\b", tables.get(tbl, "")):
            errors.append(f"表 {tbl} 缺失 Dify 映射字段 {field}")

    # 4) 软删除约定
    for name in ["kb", "document", "quiz", "wrong_book", "conversation"]:
        if not re.search(r"\bis_deleted\b", tables.get(name, "")):
            errors.append(f"表 {name} 缺失 is_deleted")

    print(f"  [pass] 表={len(tables)} 索引={len(indexes)} 部分索引={sql.count('WHERE ')}")
    print(f"  [info] 表清单: {', '.join(sorted(tables))}")
    return errors


def main() -> int:
    if not DOC.exists():
        print(f"FAIL: 找不到 {DOC}")
        return 1
    sql = extract_sql(DOC)
    print(f"来源: {DOC.relative_to(ROOT)}  DDL 字符数={len(sql)}")
    errors = check_syntax(sql) + check_structure(sql)
    if errors:
        print("\nFAIL:")
        for e in errors:
            print("  -", e)
        return 1
    print("\nOK: DDL 语法与多租户/索引/Dify 映射/软删约定校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
