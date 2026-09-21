"""应用配置：读取 .env / 环境变量（pydantic-settings）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/ -> 仓库根
BACKEND_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = BACKEND_DIR.parent
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    """全部可配置项。字段名小写下划线，对应环境变量大写（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 基础 ──────────────────────────────────────────────────────────
    app_env: str = "dev"
    app_name: str = "Ask Knowledge AI Learning Partner"
    app_secret_key: str = "dev-only-secret-change-me-at-least-32-bytes"  # 生产必须替换（≥32 字节）
    api_prefix: str = "/api/v1"
    access_token_expire_minutes: int = 1440
    refresh_token_expire_minutes: int = 10080
    jwt_algorithm: str = "HS256"

    # ── 数据库 ────────────────────────────────────────────────────────
    # 本机开发默认 SQLite（零外部依赖）；生产用 postgresql+psycopg://...
    database_url: str = f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"
    db_echo: bool = False

    # ── 日志 ──────────────────────────────────────────────────────────
    #: 控制台日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str = "INFO"
    #: 把同样的日志落盘（相对路径按 backend/ 解析）。留空 = 只输出控制台。
    #: 排障必需：Dify 报 400/403 时控制台一滚就没了，落盘后才翻得到**原始 body**
    #: （形如 `Dify POST /datasets/xx/retrieve -> 400 {"code":"invalid_param",...}`）。
    log_file: str = ""
    log_file_max_mb: int = 5
    log_file_backup_count: int = 3

    # ── 存储 ──────────────────────────────────────────────────────────
    storage_backend: str = "local"  # local | oss（oss 待接入）
    storage_local_root: str = str(DATA_DIR)
    max_upload_size_mb: int = 15
    max_upload_batch: int = 10
    allowed_extensions: str = "pdf,doc,docx,ppt,pptx,md,txt,png,jpg,jpeg"

    # ── 业务默认参数（可被知识库覆盖） ─────────────────────────────────
    default_chunk_size: int = 512  # 注意：Dify 侧单位是「字符」
    default_chunk_overlap: int = 50
    default_top_k: int = 5
    default_score_threshold: float = 0.5
    quiz_cache_minutes: int = 30
    #: 出题时下发给 App B 的【资料】长度上限（字符）：题必须出自这些原文，太长会挤占模型上下文
    quiz_material_max_chars: int = 6000

    # ── Dify ──────────────────────────────────────────────────────────
    # http = 调真实 Dify（混合架构主路径）；mock = 离线演示实现（契约与 http 完全一致）
    dify_mode: str = "mock"  # http | mock
    dify_base_url: str = "http://127.0.0.1/v1"
    dify_app_api_key: str = ""  # App A：chat-qa（Chatflow 问答）
    # 问答编排：app = 把 kb_ids 交给 Dify 应用内检索（需应用绑定该变量）；
    # rag = 后端先按知识库 dataset 检索，再把资料注入查询（advanced-chat 应用必须用这个）
    dify_chat_mode: str = "app"  # app | rag
    dify_quiz_workflow_api_key: str = ""  # App B：workflow-quiz（出题）
    dify_grade_workflow_api_key: str = ""  # App C：workflow-quiz-grade（简答判分）
    dify_dataset_api_key: str = ""  # 知识库 API（datasets）
    dify_timeout_seconds: float = 60.0
    dify_workflow_timeout_seconds: float = 120.0
    dify_indexing_timeout_seconds: float = 600.0
    dify_app_id: str = ""  # 审核回调里用于校验 app_id（可空 = 不校验）

    # ── Dify 知识库接口配额（订阅侧限流，见 services/dify/quota.py）──────
    #: Dify 对「知识库」类接口（/datasets/*）有每分钟请求上限，超限直接 403
    #: （`cloud_edition_billing_rate_limit_check`）。当前订阅实测 10 次/分钟，
    #: 这里留余量给同 Key 的其它调用方；0 = 关闭闸门（不建议）。
    dify_knowledge_rate_limit_per_minute: int = 8
    #: 拿不到名额时最多等多少秒再放弃（放弃后由调用方走缓存兜底 / 报 42901）
    dify_knowledge_wait_seconds: float = 15.0
    #: 检索结果缓存秒数（出题/问答/检索测试重复查询直接命中，省配额也更快）
    dify_retrieval_cache_seconds: float = 60.0
    #: 分块列表缓存秒数（文档预览 / 分块数回填）
    dify_segments_cache_seconds: float = 30.0
    #: Dify 侧文档列表缓存秒数
    dify_documents_cache_seconds: float = 5.0

    # ── 入库轮询 ──────────────────────────────────────────────────────
    #: 查 Dify indexing-status 的间隔：该接口同样吃「知识库」配额，
    #: 1 秒一次会在几十秒内打爆配额（出题/问答随后的检索就 403）
    indexing_poll_interval_seconds: float = 5.0

    # ── 单知识库模式（用户不能建库，上传的文档统一进既有知识库）──────────
    #: true = 后端拒绝 POST /kbs、前端隐藏「新建知识库」；
    #: 所有上传都写入 default_kb_dataset_id 指向的**既有** Dify 数据集（绝不新建 dataset）
    single_kb_mode: bool = False
    #: 唯一知识库在应用内的显示名（只影响本地记录名，不会去改 Dify 侧数据集名）
    default_kb_name: str = "默认知识库"
    #: 既有 Dify 数据集 ID（= Dify 里的「Untitled 1」），单知识库模式下所有文档都写这里
    default_kb_dataset_id: str = ""

    # ── 文档解析 ──────────────────────────────────────────────────────
    parser_mode: str = "builtin"  # builtin | mineru_local | mineru_api
    #: 交给 **Dify 侧**解析的扩展名（逗号分隔）：这些类型不在本地解析，上传后直传原文件
    #: （`create-by-file`），由 Dify 自带提取器 / 知识库流水线负责解析与分块。
    #: 典型用途：PDF —— 本地 builtin 未装 pypdf 时抽不出文本，扫描件/作品集更无能为力。
    #: 留空 = 全部走本地解析（builtin / MinerU）。
    dify_parse_extensions: str = "pdf"
    mineru_base_url: str = "http://127.0.0.1:8888"
    mineru_api_token: str = ""
    mineru_timeout_seconds: float = 1800.0

    # ── 内容审核 ──────────────────────────────────────────────────────
    audit_enabled: bool = True
    audit_provider: str = "local"  # local | aliyun
    audit_cache_minutes: int = 1440
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_green_endpoint: str = "green-cip.cn-shanghai.aliyuncs.com"
    moderation_callback_token: str = "change-me-moderation-token"

    # ── Worker ────────────────────────────────────────────────────────
    worker_inline: bool = True
    worker_poll_interval_seconds: float = 2.0
    worker_concurrency: int = 2
    task_max_attempt: int = 3

    # ── 派生属性 ──────────────────────────────────────────────────────
    @property
    def allowed_ext_set(self) -> set[str]:
        return {e.strip().lower().lstrip(".") for e in self.allowed_extensions.split(",") if e.strip()}

    @property
    def dify_parse_ext_set(self) -> set[str]:
        """交给 Dify 侧解析的扩展名集合（见 `dify_parse_extensions`）。"""
        return {
            e.strip().lower().lstrip(".")
            for e in self.dify_parse_extensions.split(",")
            if e.strip()
        }

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def data_dir(self) -> Path:
        p = Path(self.storage_local_root).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
