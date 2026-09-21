"""审核策略与本地词库规则（docs/04 §6：upload / parse / query / answer 四个卡点）。

- `POLICY[scene].risk_action` ：命中风险后的动作（block 拦截 / review 标记复核）
- `POLICY[scene].error_action`：审核服务异常时的兜底（block = fail-closed，pass = fail-open）
- `WORD_LISTS`               ：本地内置词库（演示与兜底）；生产请接阿里云内容安全或加载官方词库
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.core.constants import AuditResult, AuditScene

LABEL_SAFE = "safe"
# 命中优先级（label 取值）
LABEL_PRIORITY: tuple[str, ...] = ("politics", "illegal", "violence", "abuse")

# 本地词库：仅收录通用示范词，生产环境必须替换为合规词库（阿里云 green-cip / 官方词表）
WORD_LISTS: dict[str, set[str]] = {
    "politics": set(),  # ⚠️ 预留：生产接入合规词库，避免在代码库硬编码敏感词
    "illegal": {
        "代考",
        "代写论文",
        "买卖答案",
        "答案买卖",
        "作弊器",
        "赌博",
        "赌球",
        "毒品",
        "枪支",
        "诈骗",
        "洗钱",
        "假证",
        "违禁品",
        "外挂",
    },
    "violence": {"杀人", "砍人", "自杀", "自残", "血腥", "爆炸物"},
    "abuse": {"傻逼", "智障", "废物", "去死", "滚出", "垃圾东西"},
}


@dataclass(frozen=True, slots=True)
class ScenePolicy:
    risk_action: str
    error_action: str
    description: str = ""


_POLICY: dict[str, ScenePolicy] = {
    AuditScene.UPLOAD.value: ScenePolicy(
        AuditResult.BLOCK.value, AuditResult.BLOCK.value, "上传即审：命中直接阻断入库"
    ),
    AuditScene.PARSE.value: ScenePolicy(
        AuditResult.BLOCK.value, AuditResult.BLOCK.value, "解析后送审：命中则文档置 blocked"
    ),
    AuditScene.QUERY.value: ScenePolicy(
        AuditResult.BLOCK.value, AuditResult.PASS.value, "提问送审：命中拦截；服务异常放行"
    ),
    AuditScene.ANSWER.value: ScenePolicy(
        AuditResult.REVIEW.value, AuditResult.PASS.value, "回答送审：标记复核，不打断流式输出"
    ),
}
_DEFAULT_POLICY = ScenePolicy(AuditResult.REVIEW.value, AuditResult.PASS.value, "未登记场景")


def get_policy(scene: str) -> ScenePolicy:
    return _POLICY.get(scene, _DEFAULT_POLICY)


# ── 文本归一化与扫描 ───────────────────────────────────────────────────
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\ufeff]")
_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)
_EVASION = str.maketrans({"0": "o", "3": "e", "@": "a", "$": "s"})


def normalize(text: str) -> str:
    """NFKC + 小写 + 去零宽/分隔符 + 常见变体还原，用于对抗「傻 逼 / 傻*逼」式绕过。"""
    value = unicodedata.normalize("NFKC", text or "")
    value = _ZERO_WIDTH.sub("", value).lower()
    return _PUNCT.sub("", value.translate(_EVASION))


def scan(text: str) -> tuple[str, list[str]]:
    """返回 (命中类别, 命中词)；未命中为 (`safe`, [])。"""
    normalized = normalize(text)
    if not normalized:
        return LABEL_SAFE, []
    for label in LABEL_PRIORITY:
        words = sorted(w for w in WORD_LISTS.get(label, set()) if normalize(w) in normalized)
        if words:
            return label, words
    return LABEL_SAFE, []


def decide(scene: str, label: str) -> str:
    """按场景策略把命中结果映射为动作（pass / review / block）。"""
    if label == LABEL_SAFE:
        return AuditResult.PASS.value
    return get_policy(scene).risk_action


def mask_words(text: str, words: list[str], mask: str = "*") -> str:
    """把命中词替换为掩码（落库/回显时脱敏）。"""
    result = text or ""
    for word in words:
        if not word:
            continue
        result = re.sub(re.escape(word), mask * len(word), result, flags=re.IGNORECASE)
    return result


def excerpt(text: str, words: list[str], width: int = 80) -> str:
    """取命中词周边摘要，便于人工复核（仍做脱敏）。"""
    if not text:
        return ""
    normalized = text
    index = -1
    for word in words:
        index = normalized.lower().find(word.lower())
        if index >= 0:
            break
    if index < 0:
        snippet = normalized[: width * 2]
    else:
        start = max(0, index - width // 2)
        snippet = normalized[start : index + width]
    return mask_words(snippet.strip(), words)
