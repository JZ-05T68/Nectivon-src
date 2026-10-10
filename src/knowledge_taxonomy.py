"""Shared local subject hierarchy for AI drafts and the knowledge star map."""

from __future__ import annotations

from src.learning_subject_policy import HIGH_SCHOOL_SUBJECTS, UNIVERSITY_SUBJECTS

_ALIASES = {
    "计算机科学与技术": "计算机",
    "计算机科学": "计算机",
    "网络空间安全": "网络安全",
    "网路安全": "网络安全",
    "信息安全": "网络安全",
}

# These are knowledge-management directions, rather than degree catalogue codes.
SECONDARY_SUBJECTS: dict[str, tuple[str, ...]] = {
    "计算机": (
        "计算机基础", "程序设计", "数据结构与算法", "计算机组成原理", "操作系统",
        "计算机网络", "数据库", "软件工程", "人工智能",
    ),
    "心理学": (
        "普通心理学", "认知心理学", "发展心理学", "社会心理学", "教育心理学",
        "人格心理学", "临床与咨询心理学", "心理测量与统计", "实验心理学",
    ),
    "网络安全": (
        "网络安全基础", "密码学", "网络攻防", "Web 安全", "系统安全",
        "应用安全", "数据安全与隐私", "安全运营与应急响应", "数字取证",
    ),
}
FEATURED_SUBJECTS = tuple(SECONDARY_SUBJECTS)
PRIMARY_SUBJECTS = tuple(dict.fromkeys((
    *FEATURED_SUBJECTS, *HIGH_SCHOOL_SUBJECTS,
    *(_ALIASES.get(subject, subject) for subject in UNIVERSITY_SUBJECTS),
)))
_SECONDARY_ALIASES = {
    "数据结构": "数据结构与算法", "算法": "数据结构与算法",
    "数据库系统": "数据库", "Web安全": "Web 安全", "web安全": "Web 安全",
    "心理统计": "心理测量与统计",
}


def normalize_classification(subject: object, subdiscipline: object = "") -> tuple[str, str]:
    """Accept only known parent/child pairs from an untrusted AI response."""

    if not isinstance(subject, str) or not isinstance(subdiscipline, str):
        return "", ""
    primary, separator, child = subject.strip().partition("/")
    primary = _ALIASES.get(primary, primary)
    if primary not in PRIMARY_SUBJECTS:
        return "", ""
    secondary = subdiscipline.strip() or (child.strip() if separator else "")
    secondary = _SECONDARY_ALIASES.get(secondary, secondary)
    if secondary not in SECONDARY_SUBJECTS.get(primary, ()):
        secondary = ""
    return primary, secondary


def subject_tag(subject: str) -> str:
    """Normalize existing subject labels without losing custom user subjects."""

    primary, separator, secondary = subject.strip().partition("/")
    primary = _ALIASES.get(primary, primary)
    if primary in PRIMARY_SUBJECTS:
        # Existing manually entered directions remain authoritative, even if custom.
        secondary = _SECONDARY_ALIASES.get(secondary.strip(), secondary.strip())
        return f"{primary}/{secondary}" if separator and secondary else primary
    candidate = _SECONDARY_ALIASES.get(subject.strip(), subject.strip())
    for parent, children in SECONDARY_SUBJECTS.items():
        if candidate in children:
            return f"{parent}/{candidate}"
    return subject.strip()


def taxonomy_prompt() -> str:
    """Describe the same finite hierarchy that the local renderer consumes."""

    lines = ["一级学科可选：" + "、".join(PRIMARY_SUBJECTS)]
    lines.extend(f"{parent}的二级方向：{'、'.join(children)}"
                 for parent, children in SECONDARY_SUBJECTS.items())
    return "\n".join(lines)
