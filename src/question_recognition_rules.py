"""Shared first-pass AI requirements for mathematical transcription and layout."""

import re
from typing import Final

LATEX_SYMBOLS: Final = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\varepsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi",
    "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma",
    "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi",
    "Π": r"\Pi", "Σ": r"\Sigma", "Υ": r"\Upsilon", "Φ": r"\Phi",
    "Ψ": r"\Psi", "Ω": r"\Omega", "∫": r"\int", "∬": r"\iint",
    "∭": r"\iiint", "∮": r"\oint", "∑": r"\sum", "∏": r"\prod",
    "∞": r"\infty", "∂": r"\partial", "∇": r"\nabla", "→": r"\to",
    "ϵ": r"\epsilon", "ϑ": r"\vartheta", "ϕ": r"\phi", "ϱ": r"\varrho",
    "ς": r"\varsigma", "≠": r"\neq", "≤": r"\leq", "≥": r"\geq",
    "≈": r"\approx", "≡": r"\equiv", "∈": r"\in", "∉": r"\notin",
    "⊂": r"\subset", "⊆": r"\subseteq", "⊃": r"\supset", "⊇": r"\supseteq",
    "∪": r"\cup", "∩": r"\cap", "∅": r"\emptyset", "∀": r"\forall",
    "∃": r"\exists", "¬": r"\neg", "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow",
    "←": r"\leftarrow", "↔": r"\leftrightarrow", "↦": r"\mapsto",
    "±": r"\pm", "∓": r"\mp", "×": r"\times", "÷": r"\div",
    "⋅": r"\cdot", "∘": r"\circ", "∠": r"\angle", "⊥": r"\perp", "∥": r"\parallel",
    "ℝ": r"\mathbb{R}", "ℤ": r"\mathbb{Z}", "ℕ": r"\mathbb{N}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}",
}

MATH_NOTATION_RULES: Final = (
    "【首次输出的数学排版要求】\n"
    "凡属数学语言的内容，都必须在首次识别输出中使用标准 LaTeX，"
    "由 KaTeX 直接渲染；这适用于任何年级、学科和试卷，不能只处理初中算式。"
    "普通叙述保留原文，完整数学表达式整体包在成对的 $...$ 内；"
    "独立长公式可用 $$...$$，不要把一个表达式拆成多个零散公式。\n"
    "范围包括数学变量、希腊字母、上下标、指数幂次、分数分式、根式、"
    "绝对值、向量、矩阵、集合、区间、不等式、三角函数、对数、极限、"
    "导数、积分、求和、连乘及其他可见数学记号。"
    "例如使用 \\alpha、\\beta、\\theta、\\pi、\\sin、\\cos、\\tan、"
    "\\log、\\ln、\\lim、\\int、\\sum、\\prod、\\sqrt、\\dfrac。\n"
    "上标必须识别为指数，不能落成普通同行数字或漏掉幂次；"
    "^ 表示指数，完整指数放在花括号内，如 $x^{2}$、$(x+1)^{2023}$、"
    "$a^{n+1}$、$10^{-3}$；下标用 $a_{n}$。"
    "三角函数用 $\\sin x$、$\\cos\\theta$，积分用"
    " $\\int_{0}^{1}x^{2}\\,\\mathrm{d}x$。"
    "分式用 \\dfrac，保留分子分母的范围和原有括号、正负号、积分限。\n"
    "只改变排版，不解题，不根据常识补写、修正或猜测看不清的符号；"
    "无法辨认的部分明确待核对。数学命令不得裸露在普通文字中，"
    "也不得输出不配对的 $ 或 KaTeX 不支持的 \\ce 命令。"
    "如果输出 JSON，字符串中的反斜杠必须按 JSON 规则双写转义，"
    "换行写为 \\n。\n"
)

CHOICE_LAYOUT_RULES: Final = (
    "【首次输出的选择题排版要求】\n"
    "选择题题干单独成段，A、B、C、D 四个选项各独占一行/段，"
    "绝不能横排或挤在题干末尾，即使原卷横排或选项很短也要分行。"
    "选择题 JSON 必须分别输出题干 stem 和 options 数组，"
    "options 每一项形如 {\"label\":\"A\",\"text\":\"完整选项内容\"}，"
    "按 A、B、C、D 顺序保存；不得漏掉原图中看得清的选项。"
    "后续组成题干与各选项时使用两个换行（JSON 中为 \\n\\n）。"
    "各选项完整保留；长选项可自然折行，但不能与下一选项同行、截断、概括或省略。"
    "该规则适用于所有学科，缺失选项不得编造。"
    "选项标号 A./B./C./D. 放在公式外，选项内的数学表达式仍用 LaTeX。\n"
)

QUESTION_GRANULARITY_RULES: Final = (
    "【切分单位与公共题干：所有页面统一执行】\n"
    "选择题、填空题按原卷题号直接生成可独立作答的 atomic 叶子，children=[]；"
    "A/B/C/D 是选项，不是子题。同一道题的多个空格、集合、分号或评分点，"
    "不能据此擅自拆成几个新题。\n"
    "大题按原卷可独立作答的小问递归切分，每个小问一个 atomic 叶子；"
    "父题仅组织小问和共同材料，不作为一条学习整理记录。"
    "有原卷明确的小问编号或独立小问布局才创建对应层级，"
    "不要按句子长度、手写作答编号或解答步骤拆题。"
    "没有可拆小问的单独作答题保留为一个 atomic，不凭空制造子题。\n"
    "每个含 children 的父题必须先按语义判断 has_shared_stem=true 或 false："
    "只有各小问实际共用的背景、定义、已知条件、材料才是公共题干；"
    "题号、分值、‘计算’‘化简’‘解答下列各题’等栏目说明不算共同条件。"
    "有公共题干时只保存共同原文并关联所需的小问；"
    "几个独立小问题干各自完整保存，父题 stem 和 shared_stem 都为空字符串。"
    "第一小问的条件必须留在第一小问，不能当成父题条件，不能传给兄弟小问。\n"
    "公共图形与公共文字分别判断：没有公共文字也可能共用一张图；"
    "共用图放父题 visual_regions(role=shared)，各小问自己的图留在各自节点。"
    "每个小问独立保存题干、选项和原图关联，完整题号保留父题号及子题号。\n"
)

_QUESTION_HEADINGS: Final = frozenset({
    "计算", "计算下列各题", "计算下列各式", "化简", "化简下列各式",
    "解答", "解答下列各题", "解答下列问题", "解下列各题",
})


def is_question_heading_only(text: str) -> bool:
    """Recognize an exact section heading without interpreting any conditions."""

    value = re.sub(r"\s+", "", text)
    value = re.sub(
        r"^(?:\d+[.．、])?(?:[（(]\d+(?:\.\d+)?分[）)])?", "", value
    )
    return value.rstrip("：:。.．") in _QUESTION_HEADINGS
