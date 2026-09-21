"""内容审核：本地规则 + 阿里云内容安全适配 + 统一入口（docs/04 §6）。"""

from app.services.moderation.moderator import (
    Moderator,
    Verdict,
    get_moderator,
    set_moderator,
)
from app.services.moderation.rules import LABEL_SAFE, WORD_LISTS, decide, mask_words, scan

__all__ = [
    "LABEL_SAFE",
    "WORD_LISTS",
    "Moderator",
    "Verdict",
    "decide",
    "get_moderator",
    "mask_words",
    "scan",
    "set_moderator",
]
