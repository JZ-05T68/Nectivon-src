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
    "数学": (
        "数与代数", "函数", "几何", "概率与统计", "微积分", "线性代数", "离散数学",
    ),
    "物理": (
        "力学", "热学", "电磁学", "光学", "原子与核物理", "量子物理",
    ),
    "化学": (
        "化学基础", "无机化学", "有机化学", "分析化学", "物理化学",
    ),
    "生物": (
        "细胞生物学", "遗传学", "生理学", "生物进化", "生物技术",
    ),
    "地理": (
        "自然地理", "人文地理", "区域地理", "地图与地理信息",
    ),
    "语文": (
        "语言文字", "古诗文", "现代文阅读", "写作", "文学常识",
    ),
    "历史": (
        "中国古代史", "中国近现代史", "世界古代史", "世界近现代史", "史料分析",
    ),
    "政治": (
        "经济与社会", "政治与法治", "哲学与文化", "逻辑与思维",
    ),
    "英语": (
        "词汇", "语法", "阅读理解", "听力理解", "写作", "翻译", "口语",
    ),
    "日语": (
        "文字与词汇", "语法", "阅读理解", "听力理解", "写作", "翻译", "口语",
    ),
    "德语": ("词汇", "语法", "阅读理解", "听力理解", "写作", "翻译", "口语"),
    "法语": ("词汇", "语法", "阅读理解", "听力理解", "写作", "翻译", "口语"),
    "电气工程": (
        "电路理论", "电机学", "电力系统", "电力电子", "高电压技术",
    ),
    "电子科学与技术": (
        "模拟电子技术", "数字电子技术", "半导体器件", "微电子", "电子测量",
    ),
    "信息与通信工程": (
        "信号与系统", "数字信号处理", "通信原理", "无线通信", "信息论与编码",
    ),
    "控制科学与工程": (
        "自动控制原理", "现代控制理论", "系统建模", "检测与传感", "机器人控制",
    ),
    "机械工程": (
        "机械设计", "机械制造", "机械动力学", "机电系统", "工程制图",
    ),
    "土木工程": (
        "工程结构", "岩土工程", "工程施工", "桥梁工程", "建筑材料",
    ),
    "材料科学与工程": (
        "材料结构与性能", "金属材料", "无机非金属材料", "高分子材料", "材料加工",
    ),
    "环境科学与工程": (
        "环境化学", "水污染控制", "大气污染控制", "固体废物处理", "环境评价",
    ),
    "理论经济学": ("微观经济学", "宏观经济学", "政治经济学", "经济史"),
    "应用经济学": ("金融学", "财政学", "国际贸易", "计量经济学", "产业经济学"),
    "工商管理学": ("会计学", "企业管理", "市场营销", "人力资源管理", "运营管理"),
    "法学": ("法理学", "宪法与行政法", "民商法", "刑法", "诉讼法", "国际法"),
    "基础医学": ("人体解剖学", "组织与胚胎学", "医学生理学", "病理学", "医学免疫学"),
    "临床医学": ("诊断学", "内科学", "外科学", "妇产科学", "儿科学"),
    "药学": ("药物化学", "药理学", "药剂学", "药物分析"),
    "教育学": ("教育原理", "课程与教学", "教育史", "教育研究方法", "教育技术"),
    "哲学": ("中国哲学", "外国哲学", "伦理学", "逻辑学", "美学"),
    "艺术学": ("艺术理论", "美术", "音乐", "戏剧与影视", "设计"),
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
    parents = [parent for parent, children in SECONDARY_SUBJECTS.items()
               if candidate in children]
    # Shared directions such as grammar cannot establish a language on their own.
    if len(parents) == 1:
        return f"{parents[0]}/{candidate}"
    return subject.strip()


def taxonomy_prompt() -> str:
    """Describe the same finite hierarchy that the local renderer consumes."""

    lines = ["一级学科可选：" + "、".join(PRIMARY_SUBJECTS)]
    lines.extend(f"{parent}的二级方向：{'、'.join(children)}"
                 for parent, children in SECONDARY_SUBJECTS.items())
    return "\n".join(lines)
