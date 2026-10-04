"""Retrieve only verifiable variants from the user's imported local materials.

Online question retrieval and synthetic question generation are disabled.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from src.database import Database
from src.eligibility_rules import EligibilityRuleEngine
from src.learning_workflow_service import QuestionOrganizationService
from src.local_training_source_policy import is_locatable_local_training_question
from src.question_source_models import (
    QuestionRetrievalResult,
    QuestionSourceCandidate,
    QuestionSourceError,
    VerifiedQuestion,
)
from src.question_source_verification import QuestionSourceVerifier
from src.targeted_training_models import TrainingTask
from src.training_profile_models import LearnerProfile


class QuestionSourceRetrievalService:
    """Service handling local retrieval, source verification, and audit logging."""

    def __init__(
        self,
        database: Database | Path | str,
        verifier: QuestionSourceVerifier | None = None,
        organization_service: QuestionOrganizationService | None = None,
        eligibility_engine: EligibilityRuleEngine | None = None,
    ) -> None:
        if isinstance(database, (str, Path)):
            self._db = Database(Path(database))
        else:
            self._db = database
        self._eligibility_engine = eligibility_engine or EligibilityRuleEngine()
        self._verifier = verifier or QuestionSourceVerifier(
            self._db, eligibility_engine=self._eligibility_engine
        )
        self._org = organization_service or QuestionOrganizationService(self._db)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensure targeted training questions table exists for local provenance audit."""
        with self._db._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_questions (
                    id TEXT PRIMARY KEY,
                    task_id INTEGER,
                    training_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    question_text TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    year INTEGER,
                    exam_or_contest_name TEXT NOT NULL,
                    question_number TEXT,
                    subject TEXT NOT NULL,
                    applicable_scope TEXT NOT NULL,
                    school_name TEXT,
                    major_direction TEXT,
                    contest_name TEXT,
                    contest_tier TEXT,
                    verification_status TEXT NOT NULL,
                    rejection_reason TEXT,
                    difficulty_level TEXT,
                    question_fingerprint TEXT,
                    document_id INTEGER,
                    page_id INTEGER,
                    page_number INTEGER,
                    question_item_id INTEGER,
                    family_id INTEGER,
                    verified_at TEXT NOT NULL
                )
                """
            )
            cursor = conn.execute("PRAGMA table_info(targeted_training_questions)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            for col in (
                "school_name",
                "major_direction",
                "contest_name",
                "contest_tier",
                "reference_answer",
            ):
                if col not in existing_cols:
                    conn.execute(
                        f"ALTER TABLE targeted_training_questions ADD COLUMN {col} TEXT"
                    )

    def retrieve_and_verify(
        self,
        task: TrainingTask,
        profile: LearnerProfile | None = None,
        offset: int = 0,
        limit: int | None = None,
        source_mode: str = "local_only",
        existing_questions: list[VerifiedQuestion] | None = None,
        include_strong_base: bool | None = None,
        include_competition: bool | None = None,
    ) -> QuestionRetrievalResult:
        """Return every verified local variation; never query external sources.

        Every legacy mode, including the ambiguous ``all`` mode, is refused.
        ``limit`` and ``offset`` remain in the signature for old callers but
        never cap the number of verified local questions returned.
        """
        if source_mode != "local_only":
            raise QuestionSourceError("联网题源检索已停用；训练只从本地资料库选题。")
        del offset, limit, existing_questions
        # Every candidate must be an imported, individually locatable question.
        candidates = self._fetch_local_candidates(task)
        verified_questions: list[VerifiedQuestion] = []
        rejected_reasons: list[str] = []

        for candidate in candidates:
            result = self._verifier.verify_candidate(
                candidate,
                profile=profile,
                task=task,
                include_strong_base=include_strong_base,
                include_competition=include_competition,
            )
            if result.is_verified and result.verified_question:
                question = result.verified_question
                if not is_locatable_local_training_question(question):
                    rejected_reasons.append("题目缺少可定位的本地原始文档、页面或题号")
                    continue
                fingerprint = question.question_fingerprint
                if not any(q.question_fingerprint == fingerprint for q in verified_questions):
                    verified_questions.append(question)
            elif result.reason:
                rejected_reasons.append(result.reason)

        count = len(verified_questions)
        prompt_message = (
            f"已从本地资料库找到 {count} 道符合条件的训练题。"
            if count
            else "本地资料库暂未找到可核验的变式训练题；可以先导入其他试卷，再重新本地查询。"
        )
        self._persist_verified_questions(task, verified_questions)
        return QuestionRetrievalResult(
            training_type=task.training_type,
            target=task.target,
            subject=task.subject,
            questions=verified_questions,
            total_candidates=len(candidates),
            verified_count=count,
            rejected_count=len(rejected_reasons),
            rejected_reasons=rejected_reasons,
            prompt_message=prompt_message,
            can_refresh=False,
            can_retry=count == 0,
            page_offset=0,
            total_available_verified=count,
        )

    def retrieve_local_only(
        self,
        task: TrainingTask,
        profile: LearnerProfile | None = None,
        offset: int = 0,
        limit: int | None = None,
        include_strong_base: bool | None = None,
        include_competition: bool | None = None,
    ) -> QuestionRetrievalResult:
        """Return all verified variants from imported local materials."""
        return self.retrieve_and_verify(
            task=task,
            profile=profile,
            offset=offset,
            limit=limit,
            source_mode="local_only",
            include_strong_base=include_strong_base,
            include_competition=include_competition,
        )

    def retrieve_external_supplement(
        self,
        task: TrainingTask,
        profile: LearnerProfile | None = None,
        existing_questions: list[VerifiedQuestion] | None = None,
        limit: int | None = None,
        include_strong_base: bool = False,
        include_competition: bool = False,
    ) -> QuestionRetrievalResult:
        """Deprecated compatibility entry; always raises QuestionSourceError."""
        return self.retrieve_and_verify(
            task=task,
            profile=profile,
            offset=0,
            limit=limit,
            source_mode="external_supplement",
            existing_questions=existing_questions,
            include_strong_base=include_strong_base,
            include_competition=include_competition,
        )

    def retrieve_external_only(
        self,
        task: TrainingTask,
        profile: LearnerProfile | None = None,
        offset: int = 0,
        limit: int | None = None,
        include_strong_base: bool = False,
        include_competition: bool = False,
    ) -> QuestionRetrievalResult:
        """Deprecated compatibility entry; always raises QuestionSourceError."""
        return self.retrieve_and_verify(
            task=task,
            profile=profile,
            offset=offset,
            limit=limit,
            source_mode="external_only",
            include_strong_base=include_strong_base,
            include_competition=include_competition,
        )

    def _calculate_candidate_priority(
        self, row: Any, task: TrainingTask, is_family_member: bool
    ) -> int:
        """Calculate matching priority (1:同方法, 2:同题型, 3:同知识点, 4:相似结构)."""
        stem = str(row["stem_text"] or "")
        note = f"{row['correction_note'] or ''} {row['teacher_comment'] or ''}"
        target = task.target.strip()

        # 1. 同方法题（如果当前是方法族，或者同方法标签）
        if task.training_type == "方法族":
            if is_family_member or target in note or target in stem:
                return 1
        elif any(kw in note or kw in stem for kw in ("法", "模型", "定理", "定则", "分析法")):
            if target in note or target in stem:
                return 1

        # 2. 同题型题（如果当前是题型族）
        if task.training_type == "题型族":
            if (
                is_family_member
                or target in stem
                or target in str(row["source_document_title_snapshot"] or "")
            ):
                return 2

        # 3. 同知识点题（同关键词/知识点标签）
        target_keywords = [w for w in re.split(r"类|法|题|型|分析", target) if len(w) >= 2]
        if any(kw in stem or kw in str(row["doc_title"] or "") for kw in target_keywords):
            return 3

        # 4. 相似结构题
        return 4

    def _fetch_local_candidates(
        self, task: TrainingTask
    ) -> list[QuestionSourceCandidate]:
        """Fetch candidates from local Nectivon SQLite database ordered by strict priority."""
        ranked_candidates: list[tuple[int, int, QuestionSourceCandidate]] = []
        seen_qids: set[int] = set()
        seed_qids: set[int] = set()
        seed_document_ids: set[int] = set()
        seed_stems: set[str] = set()
        document_integrity: dict[int, bool] = {}

        with self._db._connection() as conn:
            # A family member is the source example that defined the training
            # target, not a variation question.  Returning it as the first
            # local result makes "举一反三" silently repeat the original.
            # Keep its identity/stem only as an exclusion set so a duplicate
            # import of the same question cannot bypass this rule.
            if task.family_id is not None:
                rows = conn.execute(
                    """
                    SELECT qi.id, qi.document_id, qi.page_id, qi.question_number, qi.subject,
                           qi.stem_text, qi.source_document_title_snapshot,
                           qi.correction_note, qi.teacher_comment, qi.student_answer,
                           d.title AS doc_title, d.filename, d.source_path, d.sha256,
                           p.page_number, p.image_path, p.extracted_text,
                           p.ocr_text, p.markdown_content
                    FROM question_family_members qfm
                    JOIN question_items qi ON qfm.question_id = qi.id
                    LEFT JOIN documents d ON qi.document_id = d.id
                    LEFT JOIN pages p ON qi.page_id = p.id
                    WHERE qfm.family_id = ?
                    ORDER BY qi.id ASC
                    """,
                    (task.family_id,),
                ).fetchall()

                for row in rows:
                    seed_qids.add(int(row["id"]))
                    if row["document_id"] is not None:
                        seed_document_ids.add(int(row["document_id"]))
                    normalized_stem = re.sub(
                        r"\s+", "", str(row["stem_text"] or "")
                    )
                    if normalized_stem:
                        seed_stems.add(normalized_stem)

            # 2. 如果关联题目较少，在同类学科或同题干关键词中检索其他本地题目
            target_clean = task.target.strip()

            search_rows = conn.execute(
                """
                SELECT qi.id, qi.document_id, qi.page_id, qi.question_number, qi.subject,
                       qi.stem_text, qi.source_document_title_snapshot,
                       qi.correction_note, qi.teacher_comment, qi.student_answer,
                       d.title AS doc_title, d.filename, d.source_path, d.sha256,
                       p.page_number, p.image_path, p.extracted_text,
                       p.ocr_text, p.markdown_content
                FROM question_items qi
                LEFT JOIN documents d ON qi.document_id = d.id
                LEFT JOIN pages p ON qi.page_id = p.id
                WHERE (qi.stem_text LIKE ? OR qi.source_document_title_snapshot LIKE ?)
                ORDER BY qi.id ASC
                """,
                (f"%{target_clean}%", f"%{target_clean}%"),
            ).fetchall()

            for row in search_rows:
                qid = int(row["id"])
                normalized_stem = re.sub(r"\s+", "", str(row["stem_text"] or ""))
                if (
                    qid in seed_qids
                    or (
                        row["document_id"] is not None
                        and int(row["document_id"]) in seed_document_ids
                    )
                    or (
                        normalized_stem and normalized_stem in seed_stems
                    )
                ):
                    continue
                if qid not in seen_qids:
                    cand = self._row_to_candidate(row, task, document_integrity)
                    if cand:
                        prio = self._calculate_candidate_priority(
                            row, task, is_family_member=False
                        )
                        ranked_candidates.append((prio, qid, cand))
                        seen_qids.add(qid)

        # 严格按匹配优先级排序（同方法 -> 同题型 -> 同知识点 -> 相似结构）
        ranked_candidates.sort(key=lambda x: (x[0], x[1]))
        return [cand for _, _, cand in ranked_candidates]

    def _row_to_candidate(
        self, row: Any, task: TrainingTask, document_integrity: dict[int, bool]
    ) -> QuestionSourceCandidate | None:
        """Convert a database row into a structured QuestionSourceCandidate."""
        stem = (row["stem_text"] or "").strip()
        if not stem:
            return None

        doc_id = row["document_id"]
        page_id = row["page_id"]
        image_path = Path(str(row["image_path"] or ""))
        if doc_id is None or page_id is None or not image_path.is_file():
            return None
        doc_id = int(doc_id)
        if doc_id not in document_integrity:
            source_path = Path(str(row["source_path"] or ""))
            expected_hash = str(row["sha256"] or "").lower()
            if not source_path.is_file() or len(expected_hash) != 64:
                document_integrity[doc_id] = False
            else:
                try:
                    actual_hash = hashlib.sha256()
                    with source_path.open("rb") as source_file:
                        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                            actual_hash.update(chunk)
                    document_integrity[doc_id] = actual_hash.hexdigest() == expected_hash
                except OSError:
                    document_integrity[doc_id] = False
        if not document_integrity[doc_id]:
            return None

        doc_title = (
            row["doc_title"]
            or row["source_document_title_snapshot"]
            or row["filename"]
            or "未命名试卷"
        )
        page_num = row["page_number"] or 1
        q_num = str(row["question_number"] or "原题").strip()

        # 推断年份：优先从试卷名称中提取4位或2位年份
        year = self._extract_year_from_title(doc_title)

        # 构造精准原题定位 URL（指向本地浏览资料页面）
        source_url = f"pages/3_浏览资料.py?document={doc_id}&page={page_num}#q_{row['id']}"

        # 汇总页面原始内容以便核验
        page_text = (
            f"{row['extracted_text'] or ''} "
            f"{row['ocr_text'] or ''} "
            f"{row['markdown_content'] or ''}"
        )

        # Only reviewed correction material can serve as a reference answer.
        # A student's answer may be wrong and must never be relabeled as one.
        ref_ans = ""
        try:
            ref_ans = (
                str(row["correction_note"] or "")
                or str(row["teacher_comment"] or "")
            ).strip()
        except (IndexError, KeyError):
            ref_ans = ""

        # The question's own human-reviewed subject is the evidence.  Do not
        # infer it from a title or copy the requested subject onto another item.
        subject = str(row["subject"] or "").strip()
        if not subject or not task.subject or subject != task.subject:
            return None

        return QuestionSourceCandidate(
            question_text=stem,
            source_name=f"{doc_title} · 第{page_num}页",
            source_url=source_url,
            year=year,
            exam_or_contest_name=doc_title,
            question_number=q_num,
            subject=subject,
            applicable_scope="基础教育 · 普通",
            is_regular_exam=True,
            document_id=doc_id,
            page_id=page_id,
            page_number=page_num,
            question_item_id=row["id"],
            family_id=task.family_id,
            raw_source_text=page_text,
            reference_answer=ref_ans,
        )


    @staticmethod
    def _extract_year_from_title(title: str) -> int | None:
        """Extract a year only when the source title explicitly contains one."""
        match_4 = re.search(r"(20\d{2}|19\d{2})", title)
        if match_4:
            return int(match_4.group(1))

        match_2 = re.search(r"(\b|[^0-9])([2][0-6])(\b|[^0-9])", title)
        if match_2:
            return 2000 + int(match_2.group(2))

        return None

    def _persist_verified_questions(
        self, task: TrainingTask, questions: list[VerifiedQuestion]
    ) -> None:
        """Persist verified training questions into SQLite audit table."""
        if not questions:
            return

        with self._db._connection() as conn:
            for q in questions:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO targeted_training_questions (
                        id, task_id, training_type, target, question_text,
                        source_name, source_url, year, exam_or_contest_name,
                        question_number, subject, applicable_scope,
                        school_name, major_direction, contest_name, contest_tier,
                        verification_status, rejection_reason, difficulty_level,
                        question_fingerprint, document_id, page_id, page_number,
                        question_item_id, family_id, reference_answer, verified_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        q.id,
                        getattr(task, "id", None),
                        task.training_type,
                        task.target,
                        q.question_text,
                        q.source_name,
                        q.source_url,
                        q.year,
                        q.exam_or_contest_name,
                        q.question_number,
                        q.subject,
                        q.applicable_scope,
                        q.school_name,
                        q.major_direction,
                        q.contest_name,
                        q.contest_tier,
                        str(q.verification_status),
                        q.rejection_reason,
                        q.difficulty_level,
                        q.question_fingerprint,
                        q.document_id,
                        q.page_id,
                        q.page_number,
                        q.question_item_id,
                        q.family_id,
                        q.reference_answer,
                        q.verified_at,
                    ),
                )
