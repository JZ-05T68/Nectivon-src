"""Per-course learning-goal configuration over the existing three layers (P1-4).

The three-layer workflow already exists; this module only lets the student
declare WHY they study a course — 课程达标 / 系统掌握 / 考研重点 — and maps
that goal onto a recommended整理深度.  It never auto-deletes, overwrites or
re-organizes anything: changing a goal changes guidance text only, and all
existing questions, explanations and corrections stay untouched.

Storage is application metadata beside the database (``learning_goals.json``),
never inside user material directories.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

GOAL_PASS = "课程达标"
GOAL_MASTER = "系统掌握"
GOAL_EXAM = "考研重点"
GOALS: tuple[str, ...] = (GOAL_PASS, GOAL_MASTER, GOAL_EXAM)

# Goal → recommended depth of the EXISTING workflow (not a new one).
GOAL_RECOMMENDED_LAYER: dict[str, int] = {GOAL_PASS: 1, GOAL_MASTER: 2, GOAL_EXAM: 3}

_LAYER_GUIDANCE: dict[int, str] = {
    1: "推荐整理深度：第一层（逐题核对、订正、讲解）。完成第一层即达到本课目标。",
    2: "推荐整理深度：第一层 + 第二层归纳（题型族、方法族）。系统掌握建议整理到第二层。",
    3: (
        "推荐整理深度：第一层 + 第二层 + 按需进入第三层掌握训练。"
        "考研重点以第二层归纳为基座，弱项进入第三层训练。"
    ),
}

DEFAULT_GOAL = GOAL_MASTER


class LearningGoalError(ValueError):
    """Invalid goal configuration input."""


def goals_path(database_path: Path | str) -> Path:
    return Path(database_path).parent / "learning_goals.json"


def read_goals(database_path: Path | str) -> dict[str, str]:
    """Return the saved {subject: goal} map (missing file → empty)."""

    path = goals_path(database_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearningGoalError("已保存的课程学习目标配置无法读取。") from exc
    if not isinstance(data, dict) or not isinstance(data.get("goals"), dict):
        raise LearningGoalError("课程学习目标配置格式无效。")
    return {
        str(subject).strip(): goal
        for subject, goal in data["goals"].items()
        if str(subject).strip() and goal in GOALS
    }


def save_goal(database_path: Path | str, subject: str, goal: str) -> None:
    """Persist one course goal atomically; other courses are preserved."""

    subject_clean = str(subject).strip()
    if not subject_clean:
        raise LearningGoalError("请先选择或填写课程名称。")
    if goal not in GOALS:
        raise LearningGoalError("学习目标必须是 课程达标 / 系统掌握 / 考研重点。")
    path = goals_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    goals = read_goals(database_path) if path.exists() else {}
    goals[subject_clean] = goal
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        json.dump({"goals": goals}, temp, ensure_ascii=False, indent=2)
        temp.flush()
        os.fsync(temp.fileno())
        temporary_path = Path(temp.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def goal_for(database_path: Path | str, subject: str) -> str:
    """Saved goal for one course, falling back to the neutral default."""

    return read_goals(database_path).get(str(subject).strip(), DEFAULT_GOAL)


def recommended_layer(goal: str) -> int:
    """Map a goal onto the existing workflow's recommended layer."""

    return GOAL_RECOMMENDED_LAYER.get(goal, GOAL_RECOMMENDED_LAYER[DEFAULT_GOAL])


def layer_guidance(goal: str) -> str:
    """One-line guidance shown next to the course selector."""

    return _LAYER_GUIDANCE[recommended_layer(goal)]
