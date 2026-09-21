"""存储抽象：本地磁盘实现（`STORAGE_BACKEND=local`），OSS 实现预留同契约。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# 文件头 → 规范扩展名（防扩展名伪造，docs/05 §4 内容校验）
_MAGIC: list[tuple[bytes, str]] = [
    (b"%PDF", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),  # doc / ppt / xls 通用 OLE 容器
]
_ZIP_EXT = {"docx", "pptx"}
_TEXT_EXT = {"md", "txt"}


@dataclass(slots=True)
class StoredFile:
    rel_path: str
    sha256: str
    size: int
    ext: str


class StorageBackend(Protocol):
    def save_bytes(self, *, rel_dir: str, filename: str, content: bytes) -> StoredFile: ...
    def save_text(self, *, rel_dir: str, filename: str, text: str) -> StoredFile: ...
    def read_bytes(self, rel_path: str) -> bytes: ...
    def read_text(self, rel_path: str) -> str: ...
    def exists(self, rel_path: str) -> bool: ...
    def delete(self, rel_path: str) -> None: ...
    def abs_path(self, rel_path: str) -> Path: ...


def sniff_ext(content: bytes, filename: str) -> str | None:
    """按文件头 + 扩展名联合判定真实类型；不匹配返回 None（视为伪造/不支持）。"""
    declared = Path(filename).suffix.lower().lstrip(".")
    if declared in _TEXT_EXT:
        # 文本类：允许任意内容，但扩展名必须在白名单（由调用方校验）
        return declared
    head = content[:8]
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            if ext == "ole":
                return declared if declared in {"doc", "ppt", "xls"} else None
            if declared in {"jpg", "jpeg"} and ext == "jpg":
                return declared
            return ext if declared in {ext, "jpeg" if ext == "jpg" else ext} else None
    if head.startswith(b"PK\x03\x04"):
        return declared if declared in _ZIP_EXT else None
    return None


#: 扩展名 → MIME（直传原文件给 Dify 时用；未收录的回落 octet-stream）
_MIME_BY_EXT: dict[str, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "md": "text/markdown",
    "txt": "text/plain",
    "csv": "text/csv",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def mime_for_ext(ext: str, filename: str | None = None) -> str:
    """按扩展名（或文件名）推断 MIME；未知类型返回 `application/octet-stream`。"""
    key = (ext or "").lower().lstrip(".")
    if not key and filename:
        key = Path(filename).suffix.lower().lstrip(".")
    return _MIME_BY_EXT.get(key, "application/octet-stream")


class LocalStorage:
    """本地磁盘存储：`{root}/uploads/{tenant}/{kb}/{doc}{ext}`、`{root}/parsed/{tenant}/{kb}/{doc}/markdown.md`。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 写入 ────────────────────────────────────────────────────────────
    def save_bytes(self, *, rel_dir: str, filename: str, content: bytes) -> StoredFile:
        ext = Path(filename).suffix.lower().lstrip(".")
        digest = hashlib.sha256(content).hexdigest()
        target_dir = self.root / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest[:16]}.{ext}" if ext else target_dir / digest[:16]
        target.write_bytes(content)
        return StoredFile(
            rel_path=target.relative_to(self.root).as_posix(),
            sha256=digest,
            size=len(content),
            ext=ext,
        )

    def save_text(self, *, rel_dir: str, filename: str, text: str) -> StoredFile:
        return self.save_bytes(rel_dir=rel_dir, filename=filename, content=text.encode("utf-8"))

    # ── 读取 ────────────────────────────────────────────────────────────
    def abs_path(self, rel_path: str) -> Path:
        return (self.root / rel_path).resolve()

    def read_bytes(self, rel_path: str) -> bytes:
        return self.abs_path(rel_path).read_bytes()

    def read_text(self, rel_path: str) -> str:
        return self.abs_path(rel_path).read_text(encoding="utf-8", errors="replace")

    def exists(self, rel_path: str) -> bool:
        return self.abs_path(rel_path).exists()

    def delete(self, rel_path: str) -> None:
        path = self.abs_path(rel_path)
        if path.is_file():
            path.unlink(missing_ok=True)


def build_storage() -> LocalStorage:
    from app.core.config import settings

    return LocalStorage(settings.storage_local_root)


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = build_storage()
    return _storage


def set_storage(storage: LocalStorage | None) -> None:
    """测试用：替换全局存储后端（传 None 复位）。"""
    global _storage
    _storage = storage


__all__ = [
    "LocalStorage",
    "StorageBackend",
    "StoredFile",
    "build_storage",
    "get_storage",
    "set_storage",
    "sniff_ext",
]
