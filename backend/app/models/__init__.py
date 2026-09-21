"""全部 ORM 模型（导入本包即完成模型注册，供 create_all / metadata 使用）。"""

from app.models.chat import Conversation, Message
from app.models.documents import Document, ParseTask
from app.models.knowledge import Kb, KbMember
from app.models.learning import AnswerRecord, Question, Quiz, WrongBook
from app.models.ops import AuditLog, UsageRecord
from app.models.org import AppUser, Tenant

__all__ = [
    # §2 租户与用户
    "Tenant",
    "AppUser",
    # §3 知识库与权限
    "Kb",
    "KbMember",
    # §4 文档与任务
    "Document",
    "ParseTask",
    # §5 出题 / 作答 / 错题本
    "Quiz",
    "Question",
    "AnswerRecord",
    "WrongBook",
    # §6 对话
    "Conversation",
    "Message",
    # §7 审核与用量
    "AuditLog",
    "UsageRecord",
]
