"""Service for Generating, Persisting, and Exporting Learning Reports (Phase 7).

Governs:
1. Learning report synthesis strictly from authentic SQLite records.
2. Complete provenance traceability from Attempt -> ErrorAnalysis -> Question Source.
3. Zero-data protection: refuses to forge reports or guess weaknesses without real data.
4. Historical tag preservation across user profile mutations.
5. Markdown export that does not infer missing question-source attribution.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.database import Database
from src.error_analysis_service import ErrorAnalysisService
from src.learning_report_models import LearningReport, QuestionEvidence
from src.training_profile_models import EducationType
from src.training_profile_service import TrainingProfileService
from src.training_session_service import TrainingSessionService

LOGGER = logging.getLogger(__name__)


class LearningReportService:
    """Service handling learning report generation, snapshot persistence, and export."""

    def __init__(
        self,
        database: Database | Path | str,
        session_service: TrainingSessionService | None = None,
        error_service: ErrorAnalysisService | None = None,
        profile_service: TrainingProfileService | None = None,
    ) -> None:
        if isinstance(database, (str, Path)):
            self._db = Database(Path(database))
        else:
            self._db = database
        self._ensure_schema()
        self._session_service = session_service or TrainingSessionService(self._db)
        self._error_service = error_service or ErrorAnalysisService(self._db)
        self._profile_service = profile_service or TrainingProfileService(
            self._db.database_path
        )

    def _ensure_schema(self) -> None:
        """Initialize SQLite tables for learning report snapshots and questions."""
        with self._db._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_questions (
                    id TEXT PRIMARY KEY,
                    training_type TEXT,
                    target TEXT,
                    question_text TEXT,
                    source_name TEXT,
                    source_url TEXT,
                    year INTEGER,
                    exam_or_contest_name TEXT,
                    question_number TEXT,
                    subject TEXT,
                    applicable_scope TEXT,
                    verification_status TEXT,
                    question_fingerprint TEXT,
                    verified_at TEXT,
                    reference_answer TEXT,
                    school_name TEXT,
                    major_direction TEXT,
                    contest_name TEXT,
                    contest_tier TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_learning_reports (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    target TEXT,
                    education_type TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    markdown_content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def generate_report(
        self,
        user_id: str,
        subject: str,
        target: str | None = None,
    ) -> LearningReport:
        """Generate a complete diagnostic learning report grounded in authentic evidence."""
        # 1. Zero data verification: check whether attempts exist for user and subject
        with self._db._connection() as conn:
            attempts_query = (
                """
                SELECT a.*, s.training_type, s.target as session_target,
                       s.subject as session_subject, s.questions_json
                FROM targeted_training_attempts a
                JOIN targeted_training_sessions s ON a.session_id = s.id
                WHERE s.user_id = ? AND (s.subject = ? OR a.question_id IN (
                    SELECT id FROM targeted_training_questions WHERE subject = ?
                ))
                """
            )
            params: list[Any] = [user_id, subject, subject]
            if target:
                attempts_query += " AND s.target = ?"
                params.append(target)
            attempts_query += " ORDER BY a.attempted_at ASC"

            attempt_rows = conn.execute(attempts_query, tuple(params)).fetchall()

        if not attempt_rows:
            return LearningReport(
                report_id=f"rep_{uuid4().hex[:10]}",
                user_id=user_id,
                subject=subject,
                target=target,
                has_sufficient_data=False,
                message="暂无足够学习数据，无法生成学习报告",
            )

        # 2. Extract profile snapshot
        profile = self._profile_service.get_profile()
        profile_summary: dict[str, Any] = {}
        if profile.education_type == EducationType.BASIC:
            edu_type_label = "基础教育"
            if profile.basic:
                profile_summary = {
                    "province": profile.basic.province,
                    "city": profile.basic.city,
                    "stage": profile.basic.stage,
                    "grade": profile.basic.grade,
                    "in_strong_base": profile.basic.in_strong_base,
                    "strong_base_school": profile.basic.strong_base_school,
                    "strong_base_subject": profile.basic.strong_base_subject,
                    "in_competition": profile.basic.in_competition,
                    "competition_subjects": profile.basic.competition_subjects,
                }
        elif profile.education_type == EducationType.HIGHER:
            edu_type_label = "高等教育"
            if profile.higher:
                profile_summary = {
                    "school_name": profile.higher.school,
                    "level": profile.higher.education_level,
                    "major_name": profile.higher.major or "",
                    "discipline_category": profile.higher.discipline_category or "",
                    "first_level_discipline": profile.higher.discipline_first_level or "",
                }
        else:
            edu_type_label = "基础教育"

        # 3. Assemble traceable question evidences
        question_evidences: list[QuestionEvidence] = []
        distinct_sessions: set[str] = set()
        correct_count = 0
        error_count = 0

        # Pre-fetch questions map from targeted_training_questions
        with self._db._connection() as conn:
            q_rows = conn.execute(
                "SELECT * FROM targeted_training_questions WHERE subject = ?",
                (subject,),
            ).fetchall()
            questions_db_map = {r["id"]: dict(r) for r in q_rows}

        for row in attempt_rows:
            distinct_sessions.add(row["session_id"])
            is_corr = bool(row["is_correct"])
            if is_corr:
                correct_count += 1
            else:
                error_count += 1

            qid = row["question_id"]
            q_meta = questions_db_map.get(qid, {})
            if not q_meta and row["questions_json"]:
                try:
                    q_list = json.loads(row["questions_json"])
                    for q_item in q_list:
                        if q_item.get("id") == qid:
                            q_meta = q_item
                            break
                except Exception:
                    pass

            q_text = q_meta.get("question_text", "（试题题干未载入）")
            src_name = str(q_meta.get("source_name") or "").strip()
            src_url = str(q_meta.get("source_url") or "").strip()
            yr = q_meta.get("year")
            q_num = q_meta.get("question_number")
            scope = str(q_meta.get("applicable_scope") or "").strip()
            school = q_meta.get("school_name")
            major = q_meta.get("major_direction")
            c_name = q_meta.get("contest_name")
            c_tier = q_meta.get("contest_tier")
            exam_or_c = q_meta.get("exam_or_contest_name")

            evidence = QuestionEvidence(
                question_id=qid,
                question_text=q_text,
                source_name=src_name,
                source_url=src_url,
                year=yr,
                question_number=q_num,
                subject=subject,
                applicable_scope=scope,
                is_correct=is_corr,
                user_answer=row["user_answer"],
                reference_answer=row["reference_answer"] or "",
                error_type=row["error_type"],
                user_note=row["error_analysis"] or "",
                method_reinforcement=row["method_reinforcement"] or "",
                school_name=school,
                major_direction=major,
                contest_name=c_name,
                contest_tier=c_tier,
                exam_or_contest_name=exam_or_c,
                attempted_at=row["attempted_at"] or "",
                submission_count=row["submission_count"] or 1,
            )
            question_evidences.append(evidence)

        total_attempts = len(question_evidences)
        accuracy_rate = (
            round((correct_count / total_attempts) * 100.0, 1)
            if total_attempts > 0
            else 0.0
        )

        # 4. Error profile and weakness analysis
        err_profile = self._error_service.generate_error_profile(user_id, subject)
        frequent_errors_dicts = [
            {
                "target": t.target,
                "error_type": t.error_type,
                "count": t.count,
                "tag_label": t.tag_label,
            }
            for t in err_profile.frequent_errors
        ]

        # 5. Mastery level
        if target:
            mst = self._error_service.get_mastery_state(user_id, subject, target)
            mastery_level_val = mst.level.value
            consec_correct = mst.consecutive_correct
        else:
            all_msts = self._error_service.list_mastery_states_by_subject(
                user_id, subject
            )
            if not all_msts:
                mastery_level_val = "未建立"
                consec_correct = 0
            else:
                # If any is needs_improvement, highlight needs_improvement
                if any(m.level.value == "待加强" for m in all_msts):
                    mastery_level_val = "待加强"
                elif all(m.level.value == "阶段掌握" for m in all_msts):
                    mastery_level_val = "阶段掌握"
                else:
                    mastery_level_val = "训练中"
                consec_correct = max((m.consecutive_correct for m in all_msts), default=0)

        # 6. Method summaries from training results
        with self._db._connection() as conn:
            res_rows = conn.execute(
                """
                SELECT r.method_summary_json FROM targeted_training_results r
                JOIN targeted_training_sessions s ON r.session_id = s.id
                WHERE s.user_id = ?
                ORDER BY r.completed_at DESC LIMIT 5
                """,
                (user_id,),
            ).fetchall()
            method_summaries: list[str] = []
            for r in res_rows:
                if r["method_summary_json"]:
                    try:
                        ms_list = json.loads(r["method_summary_json"])
                        for m in ms_list:
                            if m not in method_summaries:
                                method_summaries.append(m)
                    except Exception:
                        pass

        # 7. Time range determination
        if attempt_rows:
            earliest = attempt_rows[0]["attempted_at"][:10]
            latest = attempt_rows[-1]["attempted_at"][:10]
            time_range = f"{earliest} 至 {latest}"
        else:
            time_range = "全部历史"

        report = LearningReport(
            report_id=f"rep_{uuid4().hex[:10]}",
            user_id=user_id,
            subject=subject,
            target=target,
            education_type=edu_type_label,
            user_profile_summary=profile_summary,
            time_range=time_range,
            generated_at=datetime.now(UTC).isoformat(),
            total_sessions=len(distinct_sessions),
            total_attempts=total_attempts,
            correct_count=correct_count,
            error_count=error_count,
            accuracy_rate=accuracy_rate,
            error_distribution_by_type=err_profile.error_distribution_by_type,
            frequent_errors=frequent_errors_dicts,
            primary_weakness=err_profile.primary_weakness,
            mastery_level=mastery_level_val,
            consecutive_correct=consec_correct,
            question_evidences=question_evidences,
            method_summaries=method_summaries,
            has_sufficient_data=True,
            message="学习报告已基于真实训练记录成功生成",
        )

        return report

    def save_report(self, report: LearningReport) -> str | None:
        """Persist learning report snapshot to SQLite database."""
        if not report.has_sufficient_data:
            return None
        with self._db._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO targeted_training_learning_reports (
                    id, user_id, subject, target, education_type, report_json,
                    markdown_content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.user_id,
                    report.subject,
                    report.target,
                    report.education_type,
                    json.dumps(report.to_dict(), ensure_ascii=False),
                    report.to_markdown(),
                    report.generated_at,
                ),
            )
        return report.report_id

    def get_report(self, report_id: str) -> LearningReport | None:
        """Fetch saved learning report by report_id."""
        with self._db._connection() as conn:
            row = conn.execute(
                "SELECT report_json FROM targeted_training_learning_reports WHERE id = ?",
                (report_id,),
            ).fetchone()
            if not row:
                return None
            data = json.loads(row["report_json"])
            return LearningReport.from_dict(data)

    def list_historical_reports(
        self, user_id: str, subject: str | None = None
    ) -> list[LearningReport]:
        """List historical reports for a user."""
        query = "SELECT report_json FROM targeted_training_learning_reports WHERE user_id = ?"
        params: list[Any] = [user_id]
        if subject:
            query += " AND subject = ?"
            params.append(subject)
        query += " ORDER BY created_at DESC"

        with self._db._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [
                LearningReport.from_dict(json.loads(r["report_json"]))
                for r in rows
            ]

    def export_markdown(
        self,
        report: LearningReport,
        output_path: Path | None = None,
    ) -> str:
        """Export markdown representation, optionally writing to local file."""
        md = report.to_markdown()
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(md, encoding="utf-8")
        return md
