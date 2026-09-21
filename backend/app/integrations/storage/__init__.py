"""存储层：本地磁盘（默认）/ OSS（预留）。"""

from app.integrations.storage.base import (
    LocalStorage,
    StorageBackend,
    StoredFile,
    build_storage,
    get_storage,
    mime_for_ext,
    set_storage,
    sniff_ext,
)

__all__ = [
    "LocalStorage",
    "StorageBackend",
    "StoredFile",
    "build_storage",
    "get_storage",
    "mime_for_ext",
    "set_storage",
    "sniff_ext",
]

