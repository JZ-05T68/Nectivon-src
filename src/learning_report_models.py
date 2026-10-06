"""Domain models for Learning Report and Evidence Grounding (Phase 7).

Provides:
1. QuestionEvidence: Fully traceable question practice record linked to authentic sources.
2. LearningReport: Complete diagnostic learning report package for offline export.
3. Markdown serialization strictly based on real SQLite learning records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.time_display import format_beijing_time


@dataclass
class QuestionEvidence:
    """Stored question practice evidence; source fields may be absent."""

    question_id: str
    question_text: str
    source_name: str
    source_url: str
    year: int | None
    question_number: str | None
    subject: str
    applicable_scope: str
    is_correct: bool
    user_answer: str
    reference_answer: str = ""
    error_type: str | None = None
    user_note: str = ""
    method_reinforcement: str = ""
    school_name: str | None = None
    major_direction: str | None = None
    contest_name: str | None = None
    contest_tier: str | None = None
    exam_or_contest_name: str | None = None
    attempted_at: str = ""
    submission_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize evidence into dictionary."""
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "year": self.year,
            "question_number": self.question_number,
            "subject": self.subject,
            "applicable_scope": self.applicable_scope,
            "is_correct": self.is_correct,
            "user_answer": self.user_answer,
            "reference_answer": self.reference_answer,
            "error_type": self.error_type,
            "user_note": self.user_note,
            "method_reinforcement": self.method_reinforcement,
            "school_name": self.school_name,
            "major_direction": self.major_direction,
            "contest_name": self.contest_name,
            "contest_tier": self.contest_tier,
            "exam_or_contest_name": self.exam_or_contest_name,
            "attempted_at": self.attempted_at,
            "submission_count": self.submission_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionEvidence:
        """Construct evidence from dictionary."""
        return cls(
            question_id=str(data["question_id"]),
            question_text=str(data.get("question_text", "")),
            source_name=str(data.get("source_name") or ""),
            source_url=str(data.get("source_url") or ""),
            year=data.get("year"),
            question_number=data.get("question_number"),
            subject=str(data.get("subject", "通用")),
            applicable_scope=str(data.get("applicable_scope") or ""),
            is_correct=bool(data["is_correct"]),
            user_answer=str(data.get("user_answer", "")),
            reference_answer=str(data.get("reference_answer", "")),
            error_type=data.get("error_type"),
            user_note=str(data.get("user_note", "")),
            method_reinforcement=str(data.get("method_reinforcement", "")),
            school_name=data.get("school_name"),
            major_direction=data.get("major_direction"),
            contest_name=data.get("contest_name"),
            contest_tier=data.get("contest_tier"),
            exam_or_contest_name=data.get("exam_or_contest_name"),
            attempted_at=str(data.get("attempted_at", "")),
            submission_count=int(data.get("submission_count", 1)),
        )


@dataclass
class LearningReport:
    """Comprehensive diagnostic learning report generated strictly from authentic records."""

    report_id: str
    user_id: str
    subject: str
    target: str | None = None
    education_type: str = "基础教育"
    user_profile_summary: dict[str, Any] = field(default_factory=dict)
    time_range: str = "全部历史"
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    total_sessions: int = 0
    total_attempts: int = 0
    correct_count: int = 0
    error_count: int = 0
    accuracy_rate: float = 0.0
    error_distribution_by_type: dict[str, int] = field(default_factory=dict)
    frequent_errors: list[dict[str, Any]] = field(default_factory=list)
    primary_weakness: str | None = None
    mastery_level: str = "未建立"
    consecutive_correct: int = 0
    question_evidences: list[QuestionEvidence] = field(default_factory=list)
    method_summaries: list[str] = field(default_factory=list)
    has_sufficient_data: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize report into clean dictionary."""
        return {
            "report_id": self.report_id,
            "user_id": self.user_id,
            "subject": self.subject,
            "target": self.target,
            "education_type": self.education_type,
            "user_profile_summary": self.user_profile_summary,
            "time_range": self.time_range,
            "generated_at": self.generated_at,
            "total_sessions": self.total_sessions,
            "total_attempts": self.total_attempts,
            "correct_count": self.correct_count,
            "error_count": self.error_count,
            "accuracy_rate": self.accuracy_rate,
            "error_distribution_by_type": self.error_distribution_by_type,
            "frequent_errors": self.frequent_errors,
            "primary_weakness": self.primary_weakness,
            "mastery_level": self.mastery_level,
            "consecutive_correct": self.consecutive_correct,
            "question_evidences": [e.to_dict() for e in self.question_evidences],
            "method_summaries": self.method_summaries,
            "has_sufficient_data": self.has_sufficient_data,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearningReport:
        """Reconstruct report from dictionary."""
        evidences = [
            QuestionEvidence.from_dict(e)
            for e in data.get("question_evidences", [])
        ]
        return cls(
            report_id=str(data["report_id"]),
            user_id=str(data["user_id"]),
            subject=str(data["subject"]),
            target=data.get("target"),
            education_type=str(data.get("education_type", "基础教育")),
            user_profile_summary=data.get("user_profile_summary", {}),
            time_range=str(data.get("time_range", "全部历史")),
            generated_at=str(data.get("generated_at", "")),
            total_sessions=int(data.get("total_sessions", 0)),
            total_attempts=int(data.get("total_attempts", 0)),
            correct_count=int(data.get("correct_count", 0)),
            error_count=int(data.get("error_count", 0)),
            accuracy_rate=float(data.get("accuracy_rate", 0.0)),
            error_distribution_by_type=data.get("error_distribution_by_type", {}),
            frequent_errors=data.get("frequent_errors", []),
            primary_weakness=data.get("primary_weakness"),
            mastery_level=str(data.get("mastery_level", "未建立")),
            consecutive_correct=int(data.get("consecutive_correct", 0)),
            question_evidences=evidences,
            method_summaries=data.get("method_summaries", []),
            has_sufficient_data=bool(data.get("has_sufficient_data", True)),
            message=str(data.get("message", "")),
        )

    def to_markdown(self) -> str:
        """Export comprehensive markdown document grounded in authentic evidence."""
        if not self.has_sufficient_data:
            return (
                f"# Nectivon 针对训练学习报告（{self.subject}）\n\n"
                "> [!NOTE]\n"
                f"> **提示**：{self.message or '暂无足够训练数据，无法生成学习报告。'}\n"
            )

        target_title = self.target or f"{self.subject}学科综合"
        lines: list[str] = [
            f"# Nectivon 针对训练学习报告 · {self.subject} · {target_title}",
            "",
            "> 本报告根据本地训练记录生成。题源名称与链接仅转述已保存字段；"
            "未记录的字段会明确标记，报告本身不构成原始题源核验。",
            "",
            "## 1. 基础配置与用户画像",
            "",
            f"- **所属学科**：{self.subject}",
            f"- **训练目标**：{target_title}",
            f"- **教育类型**：{self.education_type}",
            f"- **报告生成时间**：{format_beijing_time(self.generated_at)} (GMT+8)",
            f"- **统计时间范围**：{self.time_range}",
        ]

        # Add profile details
        p = self.user_profile_summary
        if p:
            if self.education_type == "基础教育":
                loc = f"{p.get('province', '')} {p.get('city', '')}".strip()
                lines.append(f"- **学段年级**：{loc} · {p.get('stage', '')} {p.get('grade', '')}")
                if p.get("in_strong_base"):
                    lines.append(
                        f"- **强基计划方向**：{p.get('strong_base_school', '')} · "
                        f"{p.get('strong_base_subject', '')}"
                    )
                if p.get("in_competition"):
                    comp_desc = ", ".join(
                        f"{k}（{v}）" for k, v in p.get("competition_subjects", {}).items()
                    )
                    lines.append(f"- **学科竞赛备赛**：{comp_desc}")
            else:
                lines.append(
                    f"- **高校与专业**：{p.get('school_name', '')} · "
                    f"{p.get('level', '')} · {p.get('major_name', '')}"
                )

        # 2. 训练做题统计
        lines.extend([
            "",
            "## 2. 训练做题与正确率统计",
            "",
            "| 统计指标 | 统计数值 | 说明 |",
            "| :--- | :--- | :--- |",
            f"| 累计训练会话 | {self.total_sessions} 次 | 针对该目标开展的独立训练会话总数 |",
            f"| 累计完成题数 | {self.total_attempts} 题 | 用户正式提交解答的真实试题总数 |",
            f"| 判定正确题数 | {self.correct_count} 题 | 核心思路与标准答案一致并通过核验的题数 |",
            f"| 出现失误题数 | {self.error_count} 题 | 暴露知识盲区或解题失误的题数 |",
            f"| 综合正确率 | **{self.accuracy_rate}%** | 基于真实做题记录计算的客观正确率 |",
        ])

        # 3. 掌握度状态与进阶评估
        lines.extend([
            "",
            "## 3. 目标掌握度进阶状态",
            "",
            f"- **当前掌握度等级**：`【{self.mastery_level}】`",
            f"- **连续正确次数**：{self.consecutive_correct} 次",
            "- **掌握度进阶说明**：",
            "  - `未建立`：尚无有效练习记录。",
            "  - `训练中`：已开启练习，正在积累解题证据。",
            "  - `待加强`：近期出现失误或方法盲区，建议针对性巩固。",
            "  - `阶段掌握`：已连续多次正确解答，形成稳定的解题方法路径。",
        ])

        # 4. 错因诊断与薄弱项分析
        lines.extend([
            "",
            "## 4. 错因诊断与薄弱方向分析",
            "",
        ])
        if self.error_count == 0:
            lines.append("🎉 **当前训练目标未记录失误，解题掌握情况良好！**")
        else:
            lines.append(f"- **主要薄弱方向**：**{self.primary_weakness or '暂未形成明显倾向'}**")
            if self.frequent_errors:
                lines.append("- **高频失误预警标签（出现 $\\ge 2$ 次）**：")
                for ftag in self.frequent_errors:
                    lbl = ftag.get("tag_label", f"{ftag.get('target')} - {ftag.get('error_type')}")
                    lines.append(f"  - ⚠️ {lbl}")

            if self.error_distribution_by_type:
                lines.extend([
                    "",
                    "| 错因分类 | 失误次数 | 占比 |",
                    "| :--- | :--- | :--- |",
                ])
                for etype, count in self.error_distribution_by_type.items():
                    ratio = (
                        round((count / self.error_count) * 100.0, 1)
                        if self.error_count > 0
                        else 0.0
                    )
                    lines.append(f"| {etype} | {count} 次 | {ratio}% |")

        # 5. 核心方法小结沉淀
        if self.method_summaries:
            lines.extend([
                "",
                "## 5. 核心解题方法小结沉淀",
                "",
            ])
            for idx, summary in enumerate(self.method_summaries, 1):
                lines.append(f"{idx}. {summary}")

        # 6. 训练试题复盘与已保存题源字段
        lines.extend([
            "",
            "## 6. 训练试题复盘与题源记录（按做题时间追溯）",
            "",
        ])
        for idx, qe in enumerate(self.question_evidences, 1):
            status_icon = "✅ 正确" if qe.is_correct else "❌ 失误"
            exam_title = qe.exam_or_contest_name or qe.source_name or "来源未记录"
            tags_part = (
                f"[{qe.applicable_scope}] · {exam_title}"
                if qe.applicable_scope
                else exam_title
            )
            if qe.year:
                tags_part += f"（{qe.year}年）"
            if qe.question_number:
                tags_part += f" · 第{qe.question_number}题"
            if qe.school_name:
                tags_part += f" · {qe.school_name}"
            if qe.contest_name:
                tags_part += f" · {qe.contest_name}"

            if qe.source_name and qe.source_url:
                source_record = f"[{qe.source_name}]({qe.source_url})（需对照原始资料核验）"
            elif qe.source_name:
                source_record = f"{qe.source_name}（未记录可定位链接）"
            elif qe.source_url:
                source_record = f"来源名称未记录（已记录链接：{qe.source_url}）"
            else:
                source_record = "来源未记录"

            lines.extend([
                f"### 试题 {idx} · {status_icon} · {tags_part}",
                "",
                f"- **题目题干**：{qe.question_text}",
                f"- **题源记录**：{source_record}",
                f"- **你的解答**：`{qe.user_answer}`（提交次数：{qe.submission_count} 次）",
                f"- **标准答案**：{qe.reference_answer or '暂无简答文本'}",
            ])
            if not qe.is_correct:
                note_str = qe.user_note if qe.user_note else "无反思笔记"
                err_label = qe.error_type or "未指定"
                lines.append(f"- **归因错因**：`{err_label}` ｜ 反思笔记：{note_str}")
            if qe.method_reinforcement:
                lines.append(f"- **方法强化提示**：{qe.method_reinforcement}")
            lines.append("")

        lines.extend([
            "---",
            "*报告导出说明：本报告根据本地训练记录导出；题源字段仅为已保存信息，"
            "缺失项明确标记。请对照原始资料人工核验来源。*",
            "",
        ])
        return "\n".join(lines)
