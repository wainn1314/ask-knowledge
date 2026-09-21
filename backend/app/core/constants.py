"""业务常量与枚举（与 docs/03 数据库设计的 VARCHAR 取值一一对应）。"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """租户内角色。"""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class KbRole(StrEnum):
    """知识库成员角色。"""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class Visibility(StrEnum):
    PRIVATE = "private"
    TENANT = "tenant"


class RetrievalMode(StrEnum):
    SEMANTIC = "semantic"
    FULL_TEXT = "full_text"
    HYBRID = "hybrid"


class DocStatus(StrEnum):
    """文档状态机：pending → parsing → auditing → indexing → ready / failed / blocked。"""

    PENDING = "pending"
    PARSING = "parsing"
    AUDITING = "auditing"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    BLOCKED = "blocked"


class AuditStatus(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    BLOCK = "block"
    REVIEW = "review"


class AuditResult(StrEnum):
    PASS = "pass"
    BLOCK = "block"
    REVIEW = "review"
    ERROR = "error"


class AuditScene(StrEnum):
    UPLOAD = "upload"
    PARSE = "parse"
    QUERY = "query"
    ANSWER = "answer"


class TaskType(StrEnum):
    PARSE = "parse"
    REPARSE = "reparse"
    DELETE_INDEX = "delete_index"


class TaskStage(StrEnum):
    PARSE = "parse"
    AUDIT = "audit"
    INDEX = "index"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD = "dead"


class QuestionType(StrEnum):
    SINGLE = "single"
    MULTI = "multi"
    JUDGE = "judge"
    SHORT = "short"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    MIXED = "mixed"


class QuizStatus(StrEnum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class WrongStatus(StrEnum):
    UNMASTERED = "unmastered"
    REVIEWING = "reviewing"
    MASTERED = "mastered"


class UsageScene(StrEnum):
    CHAT = "chat"
    QUIZ = "quiz"
    QUIZ_GRADE = "quiz_grade"
    MODERATION = "moderation"
    PARSE = "parse"


class DifyIndexingStatus(StrEnum):
    WAITING = "waiting"
    PARSING = "parsing"
    CLEANING = "cleaning"
    SPLITTING = "splitting"
    INDEXING = "indexing"
    COMPLETED = "completed"
    ERROR = "error"


class ParseMode(StrEnum):
    """文档解析通道（`document.parse_mode`，前端「解析」列展示）。

    - `builtin` / `mineru_local` / `mineru_api`：本后端解析出 Markdown 后 create-by-text
    - `dify`：本地不解析，原文件直传 Dify（create-by-file），走自带提取器 / 知识库流水线
    """

    BUILTIN = "builtin"
    MINERU_LOCAL = "mineru_local"
    MINERU_API = "mineru_api"
    DIFY = "dify"


class DifyApp(StrEnum):
    """三个 Dify 应用（各自独立 API Key，见 docs/04 §2-§4）。"""

    CHAT = "chat"
    QUIZ = "quiz"
    QUIZ_GRADE = "quiz_grade"



SHORT_ANSWER_PASS_SCORE = 60.0  # 简答题及格线（< 该分记错题）
