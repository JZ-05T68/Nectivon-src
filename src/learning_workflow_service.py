"""Three-layer learning workflow services (v0.8.6 duty C vertical slice).

One module hosts the four small services so the whole vertical slice can be
reviewed as a unit; each class is independent and may be split out later
without touching callers.

- :class:`QuestionService` — layer 1, single-question organization.  Printed
  stem, student answer, teacher verdict (√/× with an explicit ``uncertain``
  state), teacher comments and corrections live in separate columns; AI
  output is a draft beside the user's words, never a replacement.
- :class:`QuestionOrganizationService` — layer 2, families.  New questions
  are matched against existing type/method/conclusion families with a local
  jieba token-overlap heuristic only (no network, no new frameworks, and no
  rapidfuzz: the product runtime deliberately excludes that dependency), and
  every change to a shared conclusion is journalled in
  ``conclusion_revisions`` so the knowledge structure can be audited as it
  evolves.
- :class:`MasteryService` — layer 3, mastery and training.  会做 and 会讲
  are recorded separately; review scheduling is a deliberately simple
  interval scheme.
- :class:`OutputCollectionService` — the one output layer.  Collections
  reference structured learning assets through the selection/range contract
  (``item_layer`` + ``item_id`` + ``range_spec``) and render through the
  :class:`CollectionRenderer` protocol; the Markdown renderer is real, the
  Word renderer is an explicit contract stub.
- :class:`TwoWingsInterface` — a deliberately neutral skeleton.  The official
  两翼 definition has not been located (see docs/V086_ARCHITECTURE_NOTES.md);
  no product semantics are invented here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Protocol

import jieba

from src.database import Database, DatabaseError, _tokenize_for_fts
from src.learning_subject_policy import is_foreign_language_subject

LOGGER = logging.getLogger(__name__)

_QUESTION_KINDS: Final = ("error", "good", "typical", "method")
_VERDICTS: Final = ("correct", "incorrect", "uncertain")
_CONFIDENCE: Final = ("confirmed", "probable", "uncertain")
_FAMILY_KINDS: Final = ("type", "method", "conclusion")
_REVISION_KINDS: Final = ("narrowed", "broadened", "corrected", "merged")
_COLLECTION_KINDS: Final = (
    "error_book",
    "good_book",
    "review_pack",
    "topic_pack",
    "conclusion_handbook",
    "weakness_report",
)
_OUTCOMES: Final = ("correct", "incorrect", "partial")


class LearningWorkflowError(DatabaseError):
    """A learning-workflow operation was refused or could not complete."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dump_tags(values: list[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def _load_tags(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LearningWorkflowError(message)


def question_form_content_changed(
    question: QuestionItem,
    *,
    stem_text: str,
    student_answer: str,
    teacher_verdict: str | None,
    correction_note: str,
    analysis_note: str = "",
    reason_tags: list[str],
    method_tags: list[str],
    solution_method: str = "",
    status: str,
) -> bool:
    """Whether a question-edit form submit changes any user-owned field.

    V086-R1 FIX-1: ``user_edited`` must mean "the user actually changed
    content", not "a save happened".  A no-change submit must keep the
    row's provenance untouched, so the edit form compares the submitted
    values against the stored row through this helper (unit-tested).
    """

    return (
        (stem_text or "").strip() != (question.stem_text or "").strip()
        or (student_answer or "").strip() != (question.student_answer or "").strip()
        or teacher_verdict != question.teacher_verdict
        or (correction_note or "").strip() != (question.correction_note or "").strip()
        or (analysis_note or "").strip() != (question.analysis_note or "").strip()
        or list(reason_tags) != list(question.reason_tags)
        or list(method_tags) != list(question.method_tags)
        or (solution_method or "").strip() != (question.solution_method or "").strip()
        or status != question.status
    )


def _optional_json_object(raw: object) -> dict | None:
    """Parse a nullable JSON object column; corrupt JSON degrades to None."""

    if not raw:
        return None
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


# --------------------------------------------------------------- layer 1
@dataclass(frozen=True, slots=True)
class QuestionItem:
    """One organized question with its separated human/AI layers.

    Source fields are ``None`` after the source document was deleted —
    ``None`` means "the learning asset outlived its source" (V086-305/208),
    never "bad data".  The snapshot fields keep the answer to "where did
    this come from" alive after the source is gone; rows created before the
    snapshot existed may carry empty snapshots, which the UI reports as
    "来源未记录" instead of inventing a source.
    """

    id: int
    document_id: int | None
    page_id: int | None
    question_number: str
    question_kind: str
    stem_text: str
    stem_confidence: str
    student_answer: str
    teacher_verdict: str | None
    teacher_comment: str
    correction_note: str
    reason_tags: list[str]
    method_tags: list[str]
    source_region: dict | None
    ai_draft: dict | None
    user_edited: bool
    status: str
    analysis_note: str = ""
    solution_method: str = ""
    math_display: dict | None = None
    subject: str = ""
    source_document_title_snapshot: str = ""
    source_page_label_snapshot: str = ""
    source_printed_page_number: int | None = None
    shared_context: str = ""
    shared_page_refs: tuple[int, ...] = ()
    shared_image_refs: tuple[str, ...] = ()
    shared_answer_refs: tuple[str, ...] = ()

    @property
    def source_available(self) -> bool:
        """Whether the original document/page is still reachable."""

        return self.document_id is not None and self.page_id is not None


class QuestionService:
    """Create and update single-question organization records (layer 1)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_question_item(
        self,
        *,
        document_id: int,
        page_id: int,
        question_kind: str,
        subject: str = "",
        question_number: str = "",
        stem_text: str = "",
        stem_confidence: str = "uncertain",
        student_answer: str = "",
        teacher_verdict: str | None = None,
        teacher_comment: str = "",
        correction_note: str = "",
        analysis_note: str = "",
        reason_tags: list[str] | None = None,
        method_tags: list[str] | None = None,
        solution_method: str = "",
        source_region: dict | None = None,
        ai_draft: dict | None = None,
    ) -> QuestionItem:
        """Persist one organized question; unknown facts stay uncertain."""

        _require(question_kind in _QUESTION_KINDS, "题目类型必须是 error/good/typical/method。")
        _require(
            stem_confidence in _CONFIDENCE,
            "题干置信度必须是 confirmed/probable/uncertain。",
        )
        _require(
            teacher_verdict is None or teacher_verdict in _VERDICTS,
            "教师判定必须是 correct/incorrect/uncertain 或空（未判定）。",
        )
        stem_confidence_value = stem_confidence
        if not stem_text:
            stem_confidence_value = "uncertain"
        elif stem_confidence == "confirmed":
            stem_confidence_value = "confirmed"
        timestamp = _utc_now()
        with self._database._connection() as connection:
            page = connection.execute(
                """
                SELECT p.id, p.document_id, p.page_number, p.printed_page_number,
                       d.title AS document_title
                FROM pages p JOIN documents d ON d.id = p.document_id
                WHERE p.id = ?
                """,
                (page_id,),
            ).fetchone()
            if page is None or int(page["document_id"]) != document_id:
                raise LearningWorkflowError(f"页面不存在或不属于该文档：page_id={page_id}")
            # Snapshot the source identity at creation time (V086-305/208):
            # even after the source is deleted, the asset still says which
            # document and page it came from. Only a minimal citation is
            # kept — never a copy of the source content.
            document_title = str(page["document_title"] or "")
            page_label = f"第 {int(page['page_number'])} 页"
            printed_page_number = (
                int(page["printed_page_number"])
                if page["printed_page_number"] is not None
                else None
            )
            cursor = connection.execute(
                """
                INSERT INTO question_items(
                    document_id, page_id, question_number, question_kind, subject,
                    stem_text, search_stem_text, stem_confidence, student_answer,
                    teacher_verdict, teacher_comment, correction_note, reason_tags,
                    method_tags, source_region, ai_draft, status, created_at, updated_at,
                    source_document_title_snapshot, source_page_label_snapshot,
                    source_printed_page_number, analysis_note, solution_method
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft',
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    document_id,
                    page_id,
                    question_number.strip(),
                    question_kind,
                    subject.strip(),
                    stem_text,
                    _tokenize_for_fts(stem_text),
                    stem_confidence_value,
                    student_answer,
                    teacher_verdict,
                    teacher_comment,
                    correction_note,
                    _dump_tags(reason_tags or []),
                    _dump_tags(method_tags or []),
                    json.dumps(source_region, ensure_ascii=False) if source_region else None,
                    json.dumps(ai_draft, ensure_ascii=False) if ai_draft else None,
                    timestamp,
                    timestamp,
                    document_title,
                    page_label,
                    printed_page_number,
                    analysis_note,
                    solution_method,
                ),
            )
            question_id = int(cursor.lastrowid)
        return self.get_question_item(question_id)

    def update_question_item(
        self,
        question_id: int,
        *,
        subject: str | None = None,
        stem_text: str | None = None,
        student_answer: str | None = None,
        teacher_verdict: str | None = None,
        teacher_comment: str | None = None,
        correction_note: str | None = None,
        analysis_note: str | None = None,
        reason_tags: list[str] | None = None,
        method_tags: list[str] | None = None,
        solution_method: str | None = None,
        status: str | None = None,
        user_edited: bool = True,
    ) -> QuestionItem:
        """Update user-owned fields; explicit None keeps the current value."""

        existing = self.get_question_item(question_id)
        _require(
            status in (None, "draft", "organized", "archived"),
            "状态必须是 draft/organized/archived。",
        )
        _require(
            teacher_verdict is None or teacher_verdict in _VERDICTS,
            "教师判定必须是 correct/incorrect/uncertain 或空。",
        )
        with self._database._connection() as connection:
            connection.execute(
                """
                UPDATE question_items SET
                    subject = ?, stem_text = ?, search_stem_text = ?,
                    student_answer = ?, teacher_verdict = ?,
                    teacher_comment = ?, correction_note = ?, analysis_note = ?,
                    reason_tags = ?, method_tags = ?, solution_method = ?,
                    math_display_json = '', status = ?, user_edited = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    existing.subject if subject is None else subject.strip(),
                    existing.stem_text if stem_text is None else stem_text,
                    _tokenize_for_fts(existing.stem_text if stem_text is None else stem_text),
                    existing.student_answer if student_answer is None else student_answer,
                    existing.teacher_verdict if teacher_verdict is None else teacher_verdict,
                    existing.teacher_comment if teacher_comment is None else teacher_comment,
                    existing.correction_note if correction_note is None else correction_note,
                    existing.analysis_note if analysis_note is None else analysis_note,
                    _dump_tags(existing.reason_tags if reason_tags is None else reason_tags),
                    _dump_tags(existing.method_tags if method_tags is None else method_tags),
                    existing.solution_method if solution_method is None else solution_method,
                    existing.status if status is None else status,
                    1 if user_edited else 0,
                    _utc_now(),
                    question_id,
                ),
            )
        return self.get_question_item(question_id)

    def save_ai_reference(self, question_id: int, reference: dict) -> QuestionItem:
        """Fill empty reference fields atomically; preserve human content and its provenance."""

        fields = {"correction_note": "correction", "analysis_note": "analysis",
                  "method_tags": "method_tags", "solution_method": "solution_method"}
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT * FROM question_items WHERE id = ?", (question_id,),
            ).fetchone()
            _require(row is not None, "题目不存在。")
            draft = _optional_json_object(row["ai_draft"]) or {}
            if draft.get("learning_reference"):
                return self.get_question_item(question_id)
            applied = []
            values = []
            for column, field in fields.items():
                current = row[column]
                empty = not (
                    _load_tags(current) if column == "method_tags" else str(current).strip()
                )
                value = reference.get(field)
                if empty and value:
                    current = _dump_tags(value) if column == "method_tags" else str(value)
                    applied.append(column)
                values.append(current)
            draft["learning_reference"] = {
                "origin": "AI_REFERENCE", "status": "pending_review", "generated_at": _utc_now(),
                "applied_fields": applied,
                "content": {field: reference.get(field) for field in fields.values()},
                "original_response": reference.get("original_reference"),
            }
            connection.execute(
                "UPDATE question_items SET correction_note=?, analysis_note=?, method_tags=?, "
                "solution_method=?, ai_draft=?, math_display_json='', updated_at=? WHERE id=?",
                (*values, json.dumps(draft, ensure_ascii=False), _utc_now(), question_id),
            )
        return self.get_question_item(question_id)

    def record_reference_failure(self, question_id: int, message: str, reference: dict) -> None:
        """Retain a rejected model draft for inspection without applying its contents."""

        existing = self.get_question_item(question_id)
        draft = dict(existing.ai_draft or {})
        draft["learning_reference_attempt"] = {
            "status": "rejected", "message": message, "content": reference,
            "generated_at": _utc_now(),
        }
        with self._database._connection() as connection:
            connection.execute("UPDATE question_items SET ai_draft=? WHERE id=?",
                               (json.dumps(draft, ensure_ascii=False), question_id))

    def update_subject_for_document(self, document_id: int, subject: str) -> int:
        """Apply one explicit human subject choice to this document's questions."""

        clean_subject = subject.strip()
        _require(bool(clean_subject), "请先选择或填写学科。")
        with self._database._connection() as connection:
            cursor = connection.execute(
                "UPDATE question_items SET subject = ?, user_edited = 1, "
                "updated_at = ? WHERE document_id = ?",
                (clean_subject, _utc_now(), document_id),
            )
        return int(cursor.rowcount)

    def update_visual_material(
        self, question_id: int, visual_material: dict | None
    ) -> QuestionItem:
        """Update only the ``ai_draft.visual_material`` binding block.

        Geography G2-A: binding confirmation (「这就是本题那张图」) is a
        provenance event, not a content edit — it must NOT flip
        ``user_edited`` (which means the user changed learning content).
        The rest of ``ai_draft`` is preserved verbatim.
        """

        existing = self.get_question_item(question_id)
        draft = dict(existing.ai_draft) if isinstance(existing.ai_draft, dict) else {}
        if visual_material is None:
            draft.pop("visual_material", None)
        else:
            draft["visual_material"] = visual_material
        with self._database._connection() as connection:
            connection.execute(
                "UPDATE question_items SET ai_draft = ?, updated_at = ? WHERE id = ?",
                (json.dumps(draft, ensure_ascii=False), _utc_now(), question_id),
            )
        return self.get_question_item(question_id)

    def backfill_visual_material_from_candidates(
        self, question_id: int, candidate_store
    ) -> dict[str, object]:
        """G2-A remaining-B: legacy questions get a visual binding DRAFT.

        Safety rules (G2-B §50): only when the question has a source page,
        the page has a stored candidate list, exactly one candidate matches
        this question's number, and that candidate actually carries visual
        information.  The backfill NEVER confirms a binding — provenance
        stays an AI draft; the user confirms in layer 1 as usual.  When
        anything is ambiguous the backfill is skipped, never guessed.
        """

        existing = self.get_question_item(question_id)
        if existing.page_id is None:
            return {"status": "skipped", "reason": "这道题没有来源页，无法回填。"}
        if isinstance(existing.ai_draft, dict) and existing.ai_draft.get(
            "visual_material"
        ):
            return {"status": "skipped", "reason": "这道题已有关联材料，未做重复回填。"}
        candidates = candidate_store.page_candidates(existing.page_id)
        if not candidates:
            return {"status": "skipped", "reason": "来源页没有可用的题目候选记录。"}
        matches = [
            candidate
            for candidate in candidates
            if candidate.number == existing.question_number
        ]
        if len(matches) != 1:
            return {
                "status": "skipped",
                "reason": "题号在候选里不唯一或不存在，为安全跳过。",
            }
        candidate = matches[0]
        has_visual_info = bool(candidate.visual_notes) or bool(candidate.figure_refs)
        if candidate.visual_dependency == "none" or not has_visual_info:
            return {"status": "skipped", "reason": "该候选没有明确的图表依赖信息。"}
        block = {
            "dependency": candidate.visual_dependency,
            "material_notes": candidate.visual_notes,
            "figure_refs": list(candidate.figure_refs),
            "binding_confirmed": False,
            "binding_provenance": "AI BINDING DRAFT（存量回填，未经你确认）",
            "source_page_id": existing.page_id,
            "regions": list(candidate.visual_regions),
        }
        self.update_visual_material(question_id, block)
        return {"status": "backfilled", "visual_material": block}

    def sync_visual_regions_from_candidates(self, page_id: int, candidates: list) -> int:
        """Sync new image references without rewriting human question content.

        Only unique printed numbers on the same source page can match. User
        overrides that disable a visual dependency are respected. A new crop
        remains an AI proposal even when the page binding was confirmed.
        """

        from src.question_candidate_service import canonical_question_number, iter_atomic_leaves
        from src.question_visual_regions import normalize_regions

        entries: dict[str, list[dict]] = {}
        duplicates: set[str] = set()
        for _, candidate, _, ancestors in iter_atomic_leaves(candidates):
            number = canonical_question_number(candidate.number)
            if not number or number in duplicates:
                continue
            if number in entries:
                entries.pop(number)
                duplicates.add(number)
                continue
            regions = list(candidate.visual_regions)
            regions.extend(
                {**region, "role": "shared"}
                for ancestor in ancestors for region in ancestor.visual_regions
                if region.get("role") in ("stem", "shared")
            )
            entries[number] = [r for r in normalize_regions(regions)
                               if r.get("page_id") == page_id]
        count = 0
        for question in self.list_question_items():
            if question.page_id != page_id:
                continue
            regions = entries.get(canonical_question_number(question.question_number), [])
            if not regions:
                continue
            draft = question.ai_draft or {}
            old = draft.get("visual_material", {})
            if not isinstance(old, dict):
                continue
            if old.get("dependency") == "none" and str(
                old.get("binding_provenance", ""),
            ).startswith("USER OVERRIDE"):
                continue
            if old.get("regions") == regions:
                continue
            block = {
                "binding_confirmed": False,
                "binding_provenance": "AI BINDING DRAFT（原图裁切，位置待核对）",
                **old, "dependency": "required", "regions": regions,
                "source_page_id": page_id,
                "region_provenance": "AI_IMAGE_REGIONS_DRAFT",
            }
            self.update_visual_material(question.id, block)
            count += 1
        return count

    def get_question_item(self, question_id: int) -> QuestionItem:
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT * FROM question_items WHERE id = ?", (question_id,)
            ).fetchone()
        if row is None:
            raise LearningWorkflowError(f"找不到整理题目：{question_id}")
        return self._from_row(row)

    def sync_image_recognized_stems(self, page_id: int, candidates: list) -> int:
        """Refresh unchanged AI stems; preserve actual human edits and notes.

        Some historical records have a user_edited flag from a status-only
        save. Exact equality to the original AI snapshot identifies those
        unchanged stems without trusting that old flag. Replaced AI wording
        stays in the draft history for review.
        """

        from src.question_candidate_service import canonical_question_number, iter_atomic_leaves

        fresh = {}
        duplicates = set()
        for _, candidate, _, ancestors in iter_atomic_leaves(candidates):
            number = canonical_question_number(candidate.number)
            if number in fresh:
                duplicates.add(number)
            fresh[number] = (candidate, ancestors)
        refreshed = 0
        for question in self.list_question_items():
            number = canonical_question_number(question.question_number)
            if question.page_id != page_id or number not in fresh or number in duplicates:
                continue
            candidate, ancestors = fresh[number]
            draft = dict(question.ai_draft or {})
            if all(not parent.user_edited and parent.split_source != "manual"
                   for parent in ancestors):
                page = self._database.get_page(page_id)
                if page is not None and page.image_path.is_file():
                    draft["image_recognized_shared_context"] = {
                        "text": "\n\n".join(
                            f"{parent.number}\n{parent.stem}"
                            for parent in ancestors
                            if parent.has_shared_stem is not False and parent.stem.strip()
                        ),
                        "source_page_id": page_id,
                        "source_image_sha256": hashlib.sha256(
                            page.image_path.read_bytes(),
                        ).hexdigest(),
                    }
            original = draft.get("candidate", {})
            if not isinstance(original, dict):
                original = {}
            ai_owned = (
                draft.get("origin") == "ai_question_candidate_split"
                and not original.get("user_edited")
                and question.stem_text == original.get("stem")
            )
            draft["image_recognized_original"] = {
                "stem": candidate.stem, "read_at": candidate.extracted_at,
                "source": "page_image", "human_stem_preserved": not ai_owned,
            }
            if ai_owned and candidate.stem != question.stem_text:
                history = list(draft.get("previous_ai_stems", []))
                history.append({"stem": question.stem_text, "replaced_at": _utc_now()})
                draft["previous_ai_stems"] = history[-20:]
                draft["candidate"] = {**original, "stem": candidate.stem,
                                      "recognition_source": "page_image"}
                # Stale formatting caches must not override the fresh source.
                draft.pop("math_display", None)
                with self._database._connection() as connection:
                    connection.execute(
                        "UPDATE question_items SET stem_text=?, search_stem_text=?, "
                        "ai_draft=?, math_display_json='', updated_at=? WHERE id=?",
                        (candidate.stem, _tokenize_for_fts(candidate.stem),
                         json.dumps(draft, ensure_ascii=False), _utc_now(), question.id),
                    )
                refreshed += 1
            else:
                with self._database._connection() as connection:
                    connection.execute(
                        "UPDATE question_items SET ai_draft=? WHERE id=?",
                        (json.dumps(draft, ensure_ascii=False), question.id),
                    )
        return refreshed

    def delete_question_item(self, question_id: int) -> None:
        """Remove one question item from the learning library (V086-R1 FIX-3).

        Scope: only this question row.  Original page images, OCR text and
        the source document are never touched.  Child learning assets
        (wing entries, mastery records, family membership, per-question
        evidence) are removed through the schema's ``ON DELETE CASCADE``
        rules, and the question FTS mirror is maintained by the
        ``question_items_fts_delete`` trigger.
        """

        with self._database._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM question_items WHERE id = ?", (question_id,)
            )
            if cursor.rowcount == 0:
                raise LearningWorkflowError(f"找不到整理题目：{question_id}")

    def invalidate_ai_derived_fields(
        self, question_id: int, *, reason: str
    ) -> dict[str, object]:
        """Upstream-invalidation (fix round §25): AI-derived, not-yet-user-
        confirmed fields must not outlive the evidence they were derived
        from.

        When the user deletes/negates an upstream visual reading:

        - a never-user-edited item whose student_answer came through a
          closed provenance gate (pure AI transcription) is hard-cleared,
          together with AI-drafted tags;
        - an item the user has edited keeps every byte the user may have
          typed — it only gets an explicit ``stale`` note in ``ai_draft``
          so the UI can ask for a re-check.  User-confirmed content is
          never silently overwritten.
        """

        existing = self.get_question_item(question_id)
        draft = existing.ai_draft if isinstance(existing.ai_draft, dict) else {}
        user_edited = bool(existing.user_edited)
        gate_open = bool(draft.get("user_confirmed")) or (
            draft.get("handwriting_presence") == "confirmed"
        )
        cleared: list[str] = []
        stale_fields: list[str] = []
        new_draft = dict(draft)
        new_draft["invalidated_at"] = _utc_now()
        new_draft["invalidated_reason"] = reason
        hard_clear_answer = (not user_edited) and (not gate_open) and bool(
            existing.student_answer.strip()
        )
        if hard_clear_answer:
            cleared.append("student_answer")
        elif existing.student_answer.strip():
            stale_fields.append("student_answer")
            new_draft["stale_fields"] = stale_fields
        if not user_edited:
            if existing.reason_tags:
                cleared.append("reason_tags")
            if existing.method_tags:
                cleared.append("method_tags")
        elif existing.reason_tags or existing.method_tags:
            stale = list(new_draft.get("stale_fields") or [])
            stale.extend(t for t in ("reason_tags", "method_tags") if t not in stale)
            new_draft["stale_fields"] = stale
        with self._database._connection() as connection:
            connection.execute(
                """
                UPDATE question_items SET
                    student_answer = ?,
                    reason_tags = ?,
                    method_tags = ?,
                    ai_draft = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    "" if "student_answer" in cleared else existing.student_answer,
                    "[]" if "reason_tags" in cleared else _dump_tags(existing.reason_tags),
                    "[]" if "method_tags" in cleared else _dump_tags(existing.method_tags),
                    json.dumps(new_draft, ensure_ascii=False),
                    _utc_now(),
                    question_id,
                ),
            )
        return {
            "question_id": question_id,
            "user_edited": user_edited,
            "cleared_fields": cleared,
            "stale_fields": stale_fields,
        }

    def list_questions_for_page(self, page_id: int) -> list[QuestionItem]:
        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM question_items WHERE page_id = ? ORDER BY id",
                (page_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_questions_for_document(self, document_id: int) -> list[QuestionItem]:
        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM question_items WHERE document_id = ? ORDER BY id",
                (document_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def find_duplicate_source(
        self,
        *,
        document_id: int,
        page_id: int,
        question_kind: str,
        source_sha256: str,
    ) -> QuestionItem | None:
        """Return an existing item created from the same page source (V086-209).

        The duplicate guard is keyed by (document, page, kind, source
        fingerprint) so clicking 「加入学习整理」 twice can never produce two
        identical shells; a genuinely different source on the same page
        still creates a new item.
        """

        for question in self.list_questions_for_document(document_id):
            if question.page_id != page_id or question.question_kind != question_kind:
                continue
            draft = question.ai_draft if isinstance(question.ai_draft, dict) else {}
            if draft.get("source_sha256") == source_sha256:
                return question
        return None

    def list_question_items(
        self, *, question_kind: str | None = None, limit: int | None = None,
        include_first_layer_only: bool = True,
    ) -> list[QuestionItem]:
        """List organized questions (newest first), optionally by kind.

        The learning hub needs a browsable library, not only per-document
        queries; ``question_kind`` filters error/good/typical/method and
        ``limit`` bounds the page size for UI rendering.
        """

        if question_kind is not None and question_kind not in _QUESTION_KINDS:
            raise LearningWorkflowError(f"题目类型无效：{question_kind}")
        sql = "SELECT * FROM question_items"
        parameters: list[object] = []
        if question_kind is not None:
            sql += " WHERE question_kind = ?"
            parameters.append(question_kind)
        sql += " ORDER BY id DESC"
        if limit is not None:
            if limit <= 0:
                raise LearningWorkflowError("limit 必须是正整数")
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._database._connection() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [self._from_row(row) for row in rows
                if include_first_layer_only or not is_foreign_language_subject(row["subject"])]

    def search_questions(self, term: str) -> list[QuestionItem]:
        """FTS search over organized question stems (same tokenizer as pages)."""

        escaped = term.replace('"', '""')
        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT q.* FROM question_items q JOIN question_search s ON s.rowid = q.id "
                "WHERE question_search MATCH ? ORDER BY q.id",
                (f'"{escaped}"',),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def link_evidence(
        self,
        question_id: int,
        *,
        evidence_item_id: int | None,
        region_json: dict | None = None,
    ) -> int:
        """Compatibility wrapper for the v29 per-subquestion evidence model.

        The pre-v29 table stored an ``evidence_item_id`` and an arbitrary
        ``region_json`` blob.  v29 deliberately replaced that shell with a
        zero-copy reference to a source material or page.  Existing callers
        can still bind a page region here: an evidence-basket item contributes
        its page id, while a manual region uses the organized question's page
        (or an explicit ``page_id`` in the mapping).  The region coordinates
        remain a UI concern; they are no longer duplicated into this table.
        """

        _require(evidence_item_id is not None or region_json, "必须提供证据项或页面区域。")
        question = self.get_question_item(question_id)
        page_id = question.page_id
        with self._database._connection() as connection:
            if evidence_item_id is not None:
                evidence_row = connection.execute(
                    "SELECT page_id FROM evidence_items WHERE id = ?",
                    (evidence_item_id,),
                ).fetchone()
                _require(
                    evidence_row is not None,
                    f"证据项不存在：{evidence_item_id}",
                )
                page_id = int(evidence_row["page_id"])
        if region_json and region_json.get("page_id") is not None:
            page_id = int(region_json["page_id"])
        _require(page_id is not None, "页面区域证据缺少可回源页。")

        from src.question_group_service import set_question_evidence

        return set_question_evidence(
            self._database,
            question_item_id=question_id,
            evidence_type="page_region",
            source_id=None,
            page_id=page_id,
            status="user_confirmed",
            confidence="confirmed",
        )

    def _from_row(self, row) -> QuestionItem:
        shared_context = ""
        shared_page_refs: tuple[int, ...] = ()
        shared_image_refs: tuple[str, ...] = ()
        shared_answer_refs: tuple[str, ...] = ()
        try:
            from src.question_structure_service import context_for_question_item

            context = context_for_question_item(self._database, int(row["id"]))
            if context is not None:
                shared_context = context.context_text
                shared_page_refs = context.page_refs
                shared_image_refs = context.image_refs
                shared_answer_refs = context.answer_refs
        except Exception:  # noqa: BLE001 - legacy rows must remain readable
            LOGGER.debug("题目共享上下文解析失败", exc_info=True)
        draft = _optional_json_object(row["ai_draft"]) or {}
        image_context = draft.get("image_recognized_shared_context", {})
        if isinstance(image_context, dict) and "text" in image_context:
            page = self._database.get_page(int(row["page_id"])) if row["page_id"] else None
            if (page is not None and page.image_path.is_file()
                    and image_context.get("source_page_id") == page.id
                    and image_context.get("source_image_sha256")
                    == hashlib.sha256(page.image_path.read_bytes()).hexdigest()):
                shared_context = str(image_context["text"])
                shared_page_refs = tuple(dict.fromkeys((*shared_page_refs, page.id)))
        return QuestionItem(
            id=int(row["id"]),
            document_id=int(row["document_id"]) if row["document_id"] is not None else None,
            page_id=int(row["page_id"]) if row["page_id"] is not None else None,
            question_number=str(row["question_number"]),
            question_kind=str(row["question_kind"]),
            subject=(str(row["subject"]) if "subject" in row.keys() else ""),
            stem_text=str(row["stem_text"]),
            stem_confidence=str(row["stem_confidence"]),
            student_answer=str(row["student_answer"]),
            teacher_verdict=row["teacher_verdict"],
            teacher_comment=str(row["teacher_comment"]),
            correction_note=str(row["correction_note"]),
            analysis_note=str(row["analysis_note"]) if "analysis_note" in row.keys() else "",
            reason_tags=_load_tags(str(row["reason_tags"])),
            method_tags=_load_tags(str(row["method_tags"])),
            solution_method=(
                str(row["solution_method"]) if "solution_method" in row.keys() else ""
            ),
            math_display=(
                _optional_json_object(row["math_display_json"])
                if "math_display_json" in row.keys() else None
            ),
            source_region=_optional_json_object(row["source_region"]),
            ai_draft=_optional_json_object(row["ai_draft"]),
            user_edited=bool(row["user_edited"]),
            status=str(row["status"]),
            source_document_title_snapshot=(
                str(row["source_document_title_snapshot"])
                if "source_document_title_snapshot" in row.keys()
                else ""
            ),
            source_page_label_snapshot=(
                str(row["source_page_label_snapshot"])
                if "source_page_label_snapshot" in row.keys()
                else ""
            ),
            source_printed_page_number=(
                int(row["source_printed_page_number"])
                if "source_printed_page_number" in row.keys()
                and row["source_printed_page_number"] is not None
                else None
            ),
            shared_context=shared_context,
            shared_page_refs=shared_page_refs,
            shared_image_refs=shared_image_refs,
            shared_answer_refs=shared_answer_refs,
        )


# --------------------------------------------------------------- layer 2
@dataclass(frozen=True, slots=True)
class QuestionFamily:
    id: int
    family_kind: str
    title: str
    description: str
    derivation: str
    variant_pattern: str
    confusion_notes: str
    status: str
    member_count: int


def _keywords(text: str) -> set[str]:
    return {token for token in jieba.cut_for_search(text) if token.strip()}


# ------------------------------------------------------- G2-B confidence
#: Student-facing labels for the confidence tiers (G2-B §7-§11: the UI
#: shows words like 高把握/建议确认, never raw scores like 0.83).
CONFIDENCE_TIER_LABELS: Final[dict[str, str]] = {
    "high": "已自动归纳",
    "medium": "建议你确认",
    "low": "暂未找到合适分类",
}

#: Upper similarity band: a proposal at least this similar to an existing
#: same-kind family is attached automatically (with an undo path), because
#: the local Dice overlap and an exact/similar title agree strongly.
HIGH_CONFIDENCE_SCORE: Final[float] = 70.0

#: Pure-topic words that must not become METHOD family titles by
#: themselves (G2-B §13/§14: topic ≠ type ≠ method).
_TOPIC_WORDS: Final[frozenset[str]] = frozenset(
    {
        "人口", "河流", "地形", "气候", "农业", "城市", "交通", "旅游",
        "工业", "资源", "江苏", "南京", "苏州", "无锡", "扬州", "常州",
        "如皋", "南通", "长三角", "珠三角", "季风", "洋流", "等高线",
        "地图", "图表", "区域", "自然", "人文",
    }
)

#: Over-broad method titles that would swallow whole subjects (G2-B §14/§16).
_OVERBROAD_METHOD_TITLES: Final[frozenset[str]] = frozenset(
    {"比较", "读图", "分析", "综合分析", "区位分析", "比较法", "判读", "读图分析"}
)


def method_title_risk(title: str) -> list[str]:
    """Explainable risks when a proposal wants to become a *method* family.

    Returns human-readable warnings; an empty list means no topic-as-method
    or over-broad risk was detected.  Used to downgrade auto-attachment to
    a user-reviewed recommendation and shown when the user accepts a new
    method family draft.
    """

    risks: list[str] = []
    clean = title.strip()
    if not clean:
        return risks
    tokens = {token for token in _keywords(clean) if token.strip()}
    if clean in _TOPIC_WORDS:
        risks.append(
            f"「{clean}」是主题词不是方法：它说的是『讲什么』，不是『怎么做』。"
        )
    elif tokens and tokens.issubset(_TOPIC_WORDS):
        risks.append(
            f"「{clean}」几乎全由主题词组成，容易把不同解法的题混成一族。"
        )
    if clean in _OVERBROAD_METHOD_TITLES:
        risks.append(
            f"「{clean}」太宽泛：几乎所有题都能套，建议写清具体步骤或判断依据。"
        )
    return risks


#: Condition markers a transferable secondary conclusion should carry
#: (G2-B §19: region / scale / season / premise / data caliber).
_CONDITION_MARKERS: Final[tuple[str, ...]] = (
    "条件", "当", "在", "时", "仅", "只", "限于", "前提", "季节",
    "夏季", "冬季", "春季", "秋季", "区域", "地区", "尺度", "范围",
    "比例尺", "沿海", "内陆", "如果", "若", "情况下", "口径",
)

#: Conclusion-type markers (G2-B1 §20): one lint does not fit every
#: secondary conclusion — a definition/correspondence ("RS 负责看"),
#: a conditional regularity, a material-derived mechanism, and a
#: method-style tip each need different checks.
_DEFINITION_MARKERS: Final[tuple[str, ...]] = (
    "是指", "指的是", "称为", "定义", "负责", "对应关系", "对应",
    "包括", "分为", "属于", "口诀", "即",
)
_MATERIAL_SCOPE_MARKERS: Final[tuple[str, ...]] = (
    "本题材料", "根据材料", "由材料", "材料中", "图中", "如图",
    "该实验", "案例中", "该图", "下图中",
)
_METHOD_MARKERS: Final[tuple[str, ...]] = (
    "看到", "先看", "先比较", "先查", "步骤", "方法", "判读", "速判",
)

CONCLUSION_TYPE_LABELS: Final[dict[str, str]] = {
    "definition": "定义/对应关系",
    "conditional": "条件性规律",
    "material_derived": "材料衍生结论",
    "method": "方法性结论",
}


def classify_conclusion(description: str) -> str:
    """Classify one secondary conclusion for lint purposes (G2-B1 §20)."""

    text = description.strip()
    if not text:
        return "conditional"
    if any(marker in text for marker in _MATERIAL_SCOPE_MARKERS):
        return "material_derived"
    if any(marker in text for marker in _DEFINITION_MARKERS):
        return "definition"
    if any(marker in text for marker in _METHOD_MARKERS):
        return "method"
    return "conditional"


def conclusion_lint(description: str) -> str:
    """Type-aware warning for one secondary conclusion (G2-B1 §19-§23).

    - definition/method conclusions are NOT flagged for missing geography
      conditions (a functional correspondence does not need 尺度/季节);
    - conditional regularities keep the strict region/scale/season check;
    - material-derived conclusions are reminded to stay inside the
      material scope ("根据本题材料"), never upgraded to universal facts.
    """

    text = description.strip()
    if not text:
        return "这条结论还没有写明内容，无法判断它什么时候成立。"
    kind = classify_conclusion(text)
    if kind == "definition":
        return ""
    if kind == "method":
        return ""
    if kind == "material_derived":
        return (
            "这条结论来自具体材料：使用时先写明「根据本题材料」，"
            "不要当成普遍地理规律。"
        )
    if any(marker in text for marker in _CONDITION_MARKERS):
        return ""
    return "这条结论还没有写清成立条件（比如区域、尺度、季节、前提），建议补上再用。"


def conclusion_condition_risk(description: str) -> str:
    """Back-compat wrapper around :func:`conclusion_lint` (G2-B §19)."""

    return conclusion_lint(description)


#: Absolutized phrases (G2-B §30: 一般 ≠ 必然, 通常 ≠ 总是, 局地 ≠ 全国).
_ABSOLUTE_PHRASES: Final[tuple[str, ...]] = (
    "必然", "总是", "一定", "必定", "永远", "所有地区", "所有区域",
    "任何地区", "任何情况", "绝对", "唯一原因", "唯一因素", "全国都",
    "全球都", "所有时候", "任何时候都",
)


def absolute_language_risk(text: str) -> list[str]:
    """List absolutized phrases found in ``text`` (empty = no risk hit)."""

    clean = text.strip()
    return [phrase for phrase in _ABSOLUTE_PHRASES if phrase in clean]


#: Provenance labels for wing entries (G2-B §39/§77: the student must see
#: whether a boundary statement comes from their own materials or is an
#: AI supplement awaiting review — never hidden in advanced metadata).
def wing_provenance_label(origin: str, confidence: str, status: str) -> str:
    """Student-facing provenance line for one wing entry."""

    if origin == "ai_draft":
        if status == "confirmed":
            return "AI 补充 · 你已核对"
        if confidence == "uncertain":
            return "AI 补充，把握有限，建议核对"
        return "AI 补充，建议核对"
    # origin == "user"
    if status == "confirmed":
        return "你已确认"
    if confidence == "uncertain":
        return "来自你的整理 · 待核对"
    return "来自你的整理"


class QuestionOrganizationService:
    """Type/method/conclusion families with auditable revisions (layer 2)."""

    SIMILARITY_THRESHOLD: Final[float] = 55.0
    #: Overnight real-corpus round (§20): 25 questions produced 68 families
    #: with a 100% singleton rate because AI-suggested titles differ in
    #: wording ("函数与导数求极值" vs "利用导数研究函数极值").  A proposed
    #: family that is sufficiently similar to an existing same-kind family
    #: MUST reuse it instead of fragmenting the layer.
    REUSE_SIMILARITY_THRESHOLD: Final[float] = 42.0

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_family(
        self,
        *,
        family_kind: str,
        title: str,
        description: str = "",
        derivation: str = "",
        variant_pattern: str = "",
        confusion_notes: str = "",
    ) -> int:
        _require(
            family_kind in _FAMILY_KINDS,
            "族类型必须是 type/method/conclusion。",
        )
        _require(bool(title.strip()), "族标题不能为空。")
        timestamp = _utc_now()
        with self._database._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO question_families(
                    family_kind, title, description, derivation,
                    variant_pattern, confusion_notes, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    family_kind,
                    title.strip(),
                    description,
                    derivation,
                    variant_pattern,
                    confusion_notes,
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def find_candidate_families(
        self, question_id: int, *, limit: int = 5
    ) -> list[tuple[int, str, str, float]]:
        """Rank existing families against one question's stem/tags locally."""

        question = QuestionService(self._database).get_question_item(question_id)
        if is_foreign_language_subject(question.subject):
            return []
        probe = " ".join([question.stem_text, *question.method_tags, *question.reason_tags])
        probe_keywords = _keywords(probe)
        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT id, family_kind, title, description FROM question_families "
                "WHERE status != 'retired' ORDER BY id"
            ).fetchall()
        candidates: list[tuple[int, str, str, float]] = []
        for row in rows:
            family_text = f"{row['title']} {row['description']}"
            family_keywords = _keywords(family_text)
            # Dice coefficient over jieba tokens; the packaged runtime must
            # stay free of rapidfuzz (enforced by test_windows_packaging).
            shared = len(probe_keywords & family_keywords)
            if probe_keywords and family_keywords:
                score = 200.0 * shared / (len(probe_keywords) + len(family_keywords))
            else:
                score = 0.0
            if score >= self.SIMILARITY_THRESHOLD:
                candidates.append(
                    (int(row["id"]), str(row["family_kind"]), str(row["title"]), round(score, 1))
                )
        candidates.sort(key=lambda item: item[3], reverse=True)
        return candidates[:limit]

    def assign_to_family(
        self,
        question_id: int,
        family_id: int,
        *,
        relation: str = "member",
        provenance: str = "user_manual",
    ) -> None:
        """Assign one question to a family, recording why (§46/§47).

        ``provenance`` keeps second-layer induction auditable: e.g.
        ``ai_auto_organize`` (AI draft suggestion at save time) vs
        ``user_manual`` (user picked the family by hand).  Existing rows
        keep their original provenance on conflict.
        """

        _require(
            relation in ("member", "variant", "counterexample"),
            "关系必须是 member/variant/counterexample。",
        )
        timestamp = _utc_now()
        with self._database._connection() as connection:
            family = connection.execute(
                "SELECT id FROM question_families WHERE id = ? AND status != 'retired'",
                (family_id,),
            ).fetchone()
            _require(family is not None, f"题型族不存在或已停用：{family_id}")
            question = connection.execute(
                "SELECT id, subject FROM question_items WHERE id = ?", (question_id,)
            ).fetchone()
            _require(question is not None, f"整理题目不存在：{question_id}")
            _require(not is_foreign_language_subject(question["subject"]),
                     "外语学科只做第一层整理，不加入归纳族。")
            connection.execute(
                """
                INSERT INTO question_family_members(
                    family_id, question_id, relation, created_at, provenance
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(family_id, question_id)
                DO UPDATE SET relation = excluded.relation
                """,
                (family_id, question_id, relation, timestamp, provenance),
            )

    def revise_family(
        self,
        family_id: int,
        *,
        revision_kind: str,
        note: str,
        trigger_question_id: int | None = None,
        new_title: str | None = None,
        new_description: str | None = None,
    ) -> None:
        """Journal every shared-conclusion change; retired families stay readable."""

        _require(
            revision_kind in _REVISION_KINDS,
            "修订类型必须是 narrowed/broadened/corrected/merged。",
        )
        _require(bool(note.strip()), "修订说明不能为空。")
        timestamp = _utc_now()
        with self._database._connection() as connection:
            family = connection.execute(
                "SELECT id, title, description FROM question_families WHERE id = ?",
                (family_id,),
            ).fetchone()
            _require(family is not None, f"题型族不存在：{family_id}")
            connection.execute(
                """
                INSERT INTO conclusion_revisions(
                    family_id, trigger_question_id, revision_kind, note, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (family_id, trigger_question_id, revision_kind, note.strip(), timestamp),
            )
            connection.execute(
                """
                UPDATE question_families SET
                    title = ?, description = ?, status = 'revised', updated_at = ?
                WHERE id = ?
                """,
                (
                    (new_title or str(family["title"])).strip(),
                    new_description if new_description is not None else str(family["description"]),
                    timestamp,
                    family_id,
                ),
            )

    def get_family(self, family_id: int) -> QuestionFamily:
        with self._database._connection() as connection:
            row = connection.execute(
                """
                SELECT f.*, (
                    SELECT COUNT(*) FROM question_family_members m
                    WHERE m.family_id = f.id
                ) AS member_count
                FROM question_families f WHERE f.id = ?
                """,
                (family_id,),
            ).fetchone()
        if row is None:
            raise LearningWorkflowError(f"题型族不存在：{family_id}")
        return QuestionFamily(
            id=int(row["id"]),
            family_kind=str(row["family_kind"]),
            title=str(row["title"]),
            description=str(row["description"]),
            derivation=str(row["derivation"]),
            variant_pattern=str(row["variant_pattern"]),
            confusion_notes=str(row["confusion_notes"]),
            status=str(row["status"]),
            member_count=int(row["member_count"]),
        )

    def list_families(
        self, *, family_kind: str | None = None
    ) -> list[QuestionFamily]:
        """List active/revised families (newest first), optionally by kind."""

        if family_kind is not None and family_kind not in _FAMILY_KINDS:
            raise LearningWorkflowError(f"族类型无效：{family_kind}")
        sql = (
            "SELECT f.*, (SELECT COUNT(*) FROM question_family_members m "
            "WHERE m.family_id = f.id) AS member_count FROM question_families f "
            "WHERE f.status != 'retired'"
        )
        parameters: list[object] = []
        if family_kind is not None:
            sql += " AND f.family_kind = ?"
            parameters.append(family_kind)
        sql += " ORDER BY f.id DESC"
        with self._database._connection() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [
            QuestionFamily(
                id=int(row["id"]),
                family_kind=str(row["family_kind"]),
                title=str(row["title"]),
                description=str(row["description"]),
                derivation=str(row["derivation"]),
                variant_pattern=str(row["variant_pattern"]),
                confusion_notes=str(row["confusion_notes"]),
                status=str(row["status"]),
                member_count=int(row["member_count"]),
            )
            for row in rows
            if not row["member_count"] or self.list_family_members(int(row["id"]))
        ]

    def list_family_members(self, family_id: int) -> list[tuple[str, QuestionItem, str]]:
        """Return (relation, question, provenance) triples for one family.

        ``provenance`` records why the member joined (§47): AI auto-
        organize suggestion, manual user assignment, or legacy empty
        (assigned before provenance existed).
        """

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT q.*, m.relation AS member_relation,
                       m.provenance AS member_provenance
                FROM question_family_members m
                JOIN question_items q ON q.id = m.question_id
                WHERE m.family_id = ? ORDER BY m.created_at, m.question_id
                """,
                (family_id,),
            ).fetchall()
        return [
            (
                str(row["member_relation"]),
                self._member_from_row(row),
                str(row["member_provenance"] or ""),
            )
            for row in rows
            if not is_foreign_language_subject(row["subject"])
        ]

    def list_families_for_question(self, question_id: int) -> list[QuestionFamily]:
        """Answer "这道题被归入哪里" for the learning hub UI."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT f.*, (SELECT COUNT(*) FROM question_family_members m
                    WHERE m.family_id = f.id) AS member_count
                FROM question_family_members fm
                JOIN question_families f ON f.id = fm.family_id
                WHERE fm.question_id = ? ORDER BY f.id
                """,
                (question_id,),
            ).fetchall()
        return [
            QuestionFamily(
                id=int(row["id"]),
                family_kind=str(row["family_kind"]),
                title=str(row["title"]),
                description=str(row["description"]),
                derivation=str(row["derivation"]),
                variant_pattern=str(row["variant_pattern"]),
                confusion_notes=str(row["confusion_notes"]),
                status=str(row["status"]),
                member_count=int(row["member_count"]),
            )
            for row in rows
        ]

    def family_revision_history(self, family_id: int) -> list[dict[str, object]]:
        """Return the auditable revision trail of one family."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT revision_kind, note, trigger_question_id, created_at
                FROM conclusion_revisions WHERE family_id = ? ORDER BY id
                """,
                (family_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------- automatic organizing
    def _find_family_by_title(
        self, connection, family_kind: str, title: str
    ) -> int | None:
        row = connection.execute(
            "SELECT id FROM question_families "
            "WHERE family_kind = ? AND title = ? AND status != 'retired'",
            (family_kind, title.strip()),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def _find_similar_family(
        self, connection, family_kind: str, title: str, description: str
    ) -> int | None:
        """Return an existing same-kind family similar to the proposal (§20)."""

        probe_keywords = _keywords(f"{title} {description}")
        if not probe_keywords:
            return None
        rows = connection.execute(
            "SELECT id, title, description FROM question_families "
            "WHERE family_kind = ? AND status != 'retired'",
            (family_kind,),
        ).fetchall()
        best_id: int | None = None
        best_score = 0.0
        for row in rows:
            family_keywords = _keywords(f"{row['title']} {row['description']}")
            if not family_keywords:
                continue
            shared = len(probe_keywords & family_keywords)
            score = 200.0 * shared / (len(probe_keywords) + len(family_keywords))
            if score > best_score:
                best_score = score
                best_id = int(row["id"])
        if best_score >= self.REUSE_SIMILARITY_THRESHOLD:
            return best_id
        return None

    def auto_organize_question(
        self,
        question_id: int,
        *,
        type_family: dict[str, str] | None = None,
        method_families: list[dict[str, str]] | None = None,
        secondary_conclusion: dict[str, str] | None = None,
    ) -> dict[str, list[int]]:
        """Attach one question to families, creating only what is missing.

        Idempotency contract (overnight round §21): running this twice —
        including every repeated first-layer save — never duplicates a
        family or a membership.  AI suggestions are preferred; when a
        suggestion is absent the local jieba-token candidates are tried
        first and a keyword fallback family is created only when the
        question carries method tags.  A question may belong to several
        type families and several method families at the same time, and a
        secondary conclusion is created only when one was actually
        proposed — it is never fabricated to fill the UI.
        """

        created = {"type": [], "method": [], "conclusion": []}
        # G2-B §21: an explicit user rejection ("移出这个族" / "都不合适")
        # is respected by EVERY auto path forever.  The family may be
        # re-suggested as a recommendation with an honest reason, but it is
        # never silently re-attached.
        rejected = self.rejected_family_ids(question_id)
        with self._database._connection() as connection:
            question_row = connection.execute(
                "SELECT id, subject, method_tags, stem_text FROM question_items WHERE id = ?",
                (question_id,),
            ).fetchone()
            if question_row is None:
                raise LearningWorkflowError(f"整理题目不存在：{question_id}")
            if is_foreign_language_subject(question_row["subject"]):
                return created
            existing_type = connection.execute(
                """
                SELECT f.id FROM question_family_members fm
                JOIN question_families f ON f.id = fm.family_id
                WHERE fm.question_id = ? AND f.family_kind = 'type'
                """,
                (question_id,),
            ).fetchall()
            existing_method = connection.execute(
                """
                SELECT f.id FROM question_family_members fm
                JOIN question_families f ON f.id = fm.family_id
                WHERE fm.question_id = ? AND f.family_kind = 'method'
                """,
                (question_id,),
            ).fetchall()
            existing_conclusion = connection.execute(
                """
                SELECT f.id FROM question_family_members fm
                JOIN question_families f ON f.id = fm.family_id
                WHERE fm.question_id = ? AND f.family_kind = 'conclusion'
                """,
                (question_id,),
            ).fetchall()
            existing_type_ids = {int(row["id"]) for row in existing_type}
            existing_method_ids = {int(row["id"]) for row in existing_method}
            existing_conclusion_ids = {int(row["id"]) for row in existing_conclusion}

            def attach(family_id: int, relation: str = "member") -> bool:
                # §47: AI auto-organization provenance is recorded per
                # assignment so second-layer conclusions stay auditable
                # against what they were derived from.  G2-B §21: a family
                # the user removed is never silently re-attached.
                if family_id in rejected:
                    return False
                connection.execute(
                    """
                    INSERT INTO question_family_members(
                        family_id, question_id, relation, created_at, provenance
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(family_id, question_id)
                    DO UPDATE SET relation = excluded.relation
                    """,
                    (
                        family_id,
                        question_id,
                        relation,
                        _utc_now(),
                        "ai_auto_organize（保存题目时 AI 草稿建议）",
                    ),
                )
                return True

            def ensure_family(
                kind: str, title: str, description: str, derivation: str = ""
            ) -> int | None:
                title = title.strip()
                if not title:
                    return None
                family_id = self._find_family_by_title(connection, kind, title)
                if family_id is None:
                    # §20 anti-fragmentation: a wording-variant of an existing
                    # family reuses it instead of creating a singleton clone.
                    family_id = self._find_similar_family(
                        connection, kind, title, description
                    )
                if family_id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO question_families(
                            family_kind, title, description, derivation,
                            variant_pattern, confusion_notes, status,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, '', '', 'active', ?, ?)
                        """,
                        (kind, title, description, derivation, _utc_now(), _utc_now()),
                    )
                    family_id = int(cursor.lastrowid)
                return family_id

            # -- type family ------------------------------------------------
            type_title = str((type_family or {}).get("title") or "").strip()
            if existing_type_ids:
                if type_title:
                    matched = self._find_family_by_title(connection, "type", type_title)
                    if matched is not None and matched not in existing_type_ids:
                        if attach(matched):
                            created["type"].append(matched)
            elif type_title:
                family_id = ensure_family(
                    "type", type_title, str((type_family or {}).get("description") or "")
                )
                if attach(family_id):
                    created["type"].append(family_id)
            else:
                for candidate_id, kind, _title, _score in self.find_candidate_families(
                    question_id, limit=1
                ):
                    if kind == "type" and candidate_id not in existing_type_ids:
                        if attach(candidate_id):
                            created["type"].append(candidate_id)

            # -- method families --------------------------------------------
            proposed_methods = [
                {
                    "title": str(item.get("title") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                }
                for item in (method_families or [])
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ]
            if not proposed_methods:
                method_tags = _load_tags(str(question_row["method_tags"]))
                proposed_methods = [
                    {"title": tag, "description": ""} for tag in method_tags
                ]
            known_method_titles: set[str] = set()
            for member_method_id in existing_method_ids:
                row = connection.execute(
                    "SELECT title FROM question_families WHERE id = ?",
                    (member_method_id,),
                ).fetchone()
                if row is not None:
                    known_method_titles.add(str(row["title"]))
            for proposal in proposed_methods:
                if proposal["title"] in known_method_titles:
                    continue
                matched = self._find_family_by_title(connection, "method", proposal["title"])
                if matched is None:
                    # §20 anti-fragmentation: same reuse rule as type families.
                    matched = self._find_similar_family(
                        connection, "method", proposal["title"], proposal["description"]
                    )
                if matched is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO question_families(
                            family_kind, title, description, derivation,
                            variant_pattern, confusion_notes, status,
                            created_at, updated_at
                        ) VALUES ('method', ?, ?, '', '', '', 'active', ?, ?)
                        """,
                        (
                            proposal["title"],
                            proposal["description"],
                            _utc_now(),
                            _utc_now(),
                        ),
                    )
                    matched = int(cursor.lastrowid)
                if matched not in existing_method_ids and attach(matched):
                    created["method"].append(matched)
                known_method_titles.add(proposal["title"])

            # -- secondary conclusion (optional by design) -------------------
            if secondary_conclusion and not existing_conclusion_ids:
                title = str(secondary_conclusion.get("title") or "").strip()
                if title:
                    family_id = ensure_family(
                        "conclusion",
                        title,
                        str(secondary_conclusion.get("description") or ""),
                        str(secondary_conclusion.get("derivation") or ""),
                    )
                    if attach(family_id):
                        created["conclusion"].append(family_id)
        return created

    def _best_similar_family(
        self,
        connection,
        family_kind: str,
        title: str,
        description: str,
        *,
        floor: float = 0.0,
    ) -> tuple[int | None, float]:
        """Best (family_id, score) of the same kind above ``floor`` (§20).

        Splitting the score from the id lets the G2-B confidence bands
        classify the same explainable Dice overlap the layer already uses;
        :meth:`_find_similar_family` keeps the strict reuse threshold.
        """

        probe_keywords = _keywords(f"{title} {description}")
        if not probe_keywords:
            return None, 0.0
        rows = connection.execute(
            "SELECT id, title, description FROM question_families "
            "WHERE family_kind = ? AND status != 'retired'",
            (family_kind,),
        ).fetchall()
        best_id: int | None = None
        best_score = 0.0
        for row in rows:
            family_keywords = _keywords(f"{row['title']} {row['description']}")
            if not family_keywords:
                continue
            shared = len(probe_keywords & family_keywords)
            score = 200.0 * shared / (len(probe_keywords) + len(family_keywords))
            if score > best_score:
                best_score = score
                best_id = int(row["id"])
        if best_id is not None and best_score >= floor:
            return best_id, best_score
        return None, best_score

    # ------------------------------------------- G2-B confidence workflow
    def rejected_family_ids(self, question_id: int) -> set[int]:
        """Families the user explicitly removed for this question (§21)."""

        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT family_id FROM user_family_rejections "
                "WHERE question_id = ? AND family_id IS NOT NULL",
                (question_id,),
            ).fetchall()
        return {int(row["family_id"]) for row in rows}

    def remove_from_family(
        self, question_id: int, family_id: int, *, note: str = ""
    ) -> None:
        """User removes one question from a family and the refusal persists.

        The membership row is deleted and the rejection is recorded, so
        every future auto-organize pass refuses to re-attach this family
        for this question (§21/§72).  Re-adding by hand stays possible via
        the family page; that new assignment carries user provenance.
        """

        timestamp = _utc_now()
        with self._database._connection() as connection:
            family = connection.execute(
                "SELECT id FROM question_families WHERE id = ?", (family_id,)
            ).fetchone()
            _require(family is not None, f"题型族不存在：{family_id}")
            connection.execute(
                "DELETE FROM question_family_members "
                "WHERE question_id = ? AND family_id = ?",
                (question_id, family_id),
            )
            connection.execute(
                """
                INSERT INTO user_family_rejections(
                    question_id, family_id, note, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(question_id, family_id) DO NOTHING
                """,
                (question_id, family_id, note, timestamp),
            )

    def organize_with_confidence(
        self,
        question_id: int,
        *,
        type_family: dict[str, str] | None = None,
        method_families: list[dict[str, str]] | None = None,
        secondary_conclusion: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Product-level confidence workflow (G2-B §6-§11).

        Returns what happened in student language:

        - HIGH: the proposal matches an existing same-kind family exactly
          or far above the similarity band → attached immediately with an
          honest reason, always revocable via :meth:`remove_from_family`.
        - MEDIUM: several plausible families or only a moderate match, or
          the proposal would create a topic-word method family → stored as
          ``family_review_items`` (confidence ``medium``) for the user to
          pick; nothing is attached yet.
        - LOW: no plausible existing family → stored as a new-family draft
          (confidence ``low``); the formal family exists only after the
          user accepts it (§9: low confidence never creates formal
          families on its own).

        User rejections (§21) are honored: a rejected family is never
        auto-attached; a strong proposal hitting a rejected family is
        downgraded to a recommendation that says why it is re-suggested.
        """

        auto_ids: list[int] = []
        auto_labels: list[str] = []
        auto_reasons: dict[int, str] = {}
        recommended_ids: list[int] = []
        draft_ids: list[int] = []
        kind_labels = {"type": "题型", "method": "方法"}
        with self._database._connection() as connection:
            question_row = connection.execute(
                "SELECT id, subject, method_tags, reason_tags, stem_text "
                "FROM question_items WHERE id = ?",
                (question_id,),
            ).fetchone()
            if question_row is None:
                raise LearningWorkflowError(f"整理题目不存在：{question_id}")
            if is_foreign_language_subject(question_row["subject"]):
                return {"auto": [], "auto_labels": [], "auto_reasons": {},
                        "recommended": [], "new_drafts": []}
            existing = {
                kind: {
                    int(row["id"])
                    for row in connection.execute(
                        """
                        SELECT f.id FROM question_family_members fm
                        JOIN question_families f ON f.id = fm.family_id
                        WHERE fm.question_id = ? AND f.family_kind = ?
                        """,
                        (question_id, kind),
                    ).fetchall()
                }
                for kind in ("type", "method")
            }
            rejected = self.rejected_family_ids(question_id)

            def member_count(family_id: int) -> int:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM question_family_members "
                    "WHERE family_id = ?",
                    (family_id,),
                ).fetchone()
                return int(row["n"])

            def classify(
                kind: str, title: str, description: str
            ) -> tuple[str, int | None, float, int | None, str]:
                """(tier, matched_id, score, review_id, reason)."""

                clean = title.strip()
                exact = self._find_family_by_title(connection, kind, clean)
                similar_id, similar_score = self._best_similar_family(
                    connection, kind, clean, description, floor=0.0
                )
                matched_id = exact or similar_id
                members = member_count(matched_id) if matched_id else 0
                if matched_id is not None and matched_id in rejected:
                    return (
                        "medium",
                        matched_id,
                        similar_score,
                        None,
                        "你之前把这个族从这道题移出过，所以这次只建议不自动加入；"
                        "如果确实需要可以再确认。",
                    )
                if exact is not None:
                    if exact in existing[kind]:
                        return ("skip", exact, 100.0, None, "")
                    reason = f"已有一致的「{clean}」族"
                    reason += (
                        f"（已收着 {members} 道同类题）。" if members else "。"
                    )
                    return ("high", exact, 100.0, None, reason)
                if similar_score >= HIGH_CONFIDENCE_SCORE:
                    row = connection.execute(
                        "SELECT title FROM question_families WHERE id = ?",
                        (similar_id,),
                    ).fetchone()
                    fam_title = str(row["title"]) if row else clean
                    reason = (
                        f"这道题和「{fam_title}」族的题干特征高度相似"
                        + (f"，族里已有 {members} 道题。" if members else "。")
                    )
                    return ("high", similar_id, similar_score, None, reason)
                risks = (
                    method_title_risk(clean) if kind == "method" else []
                )
                if similar_score >= self.REUSE_SIMILARITY_THRESHOLD:
                    row = connection.execute(
                        "SELECT title FROM question_families WHERE id = ?",
                        (similar_id,),
                    ).fetchone()
                    fam_title = str(row["title"]) if row else clean
                    reason = f"这道题可能属于「{fam_title}」，但也接近别的分类，由你来定。"
                elif risks:
                    reason = "AI 想新建一个方法族，但命名需要你把关：" + risks[0]
                else:
                    reason = "现有分类里没有明显合适的，AI 建议了一个新分类，等你核对。"
                strong_enough = (
                    similar_score >= self.REUSE_SIMILARITY_THRESHOLD or bool(risks)
                )
                tier = "medium" if strong_enough else "low"
                return (tier, similar_id, similar_score, None, reason)

            proposals: list[tuple[str, str, str, str]] = []
            type_proposal = type_family or {}
            type_title = str(type_proposal.get("title") or "").strip()
            if type_title:
                proposals.append(
                    (
                        "type",
                        type_title,
                        str(type_proposal.get("description") or ""),
                        type_title,
                    )
                )
            for item in method_families or []:
                title = str(item.get("title") or "").strip()
                if title:
                    proposals.append(
                        (
                            "method",
                            title,
                            str(item.get("description") or ""),
                            title,
                        )
                    )

            for kind, title, description, _label in proposals:
                tier, matched_id, _score, _review, reason = classify(
                    kind, title, description
                )
                if tier == "skip":
                    continue
                if tier == "high" and matched_id is not None:
                    connection.execute(
                        """
                        INSERT INTO question_family_members(
                            family_id, question_id, relation, created_at, provenance
                        )
                        VALUES (?, ?, 'member', ?, ?)
                        ON CONFLICT(family_id, question_id)
                        DO UPDATE SET relation = excluded.relation
                        """,
                        (
                            matched_id,
                            question_id,
                            _utc_now(),
                            "AI 自动归类（把握较高，保存时）",
                        ),
                    )
                    auto_ids.append(matched_id)
                    auto_labels.append(f"{kind_labels[kind]}族「{title}」")
                    auto_reasons[matched_id] = reason
                    continue
                if tier in ("medium", "low"):
                    # One pending row per (question, kind, title): repeated
                    # saves never duplicate recommendations (§22 idempotency).
                    row = connection.execute(
                        """
                        SELECT id FROM family_review_items
                        WHERE question_id = ? AND suggestion_kind = ?
                          AND title = ? AND status = 'pending'
                        """,
                        (question_id, kind, title),
                    ).fetchone()
                    if row is not None:
                        continue
                    cursor = connection.execute(
                        """
                        INSERT INTO family_review_items(
                            question_id, suggestion_kind, confidence, title,
                            description, reason, matched_family_id, status,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (
                            question_id,
                            kind,
                            tier,
                            title,
                            description,
                            reason,
                            matched_id if tier == "medium" else None,
                            _utc_now(),
                            _utc_now(),
                        ),
                    )
                    review_id = int(cursor.lastrowid)
                    if tier == "medium":
                        recommended_ids.append(review_id)
                    else:
                        draft_ids.append(review_id)

            # Secondary conclusions keep the existing honest contract:
            # created only when actually proposed, never fabricated.  A
            # proposal matching an existing conclusion family reuses it.
            if secondary_conclusion and not any(
                True
                for row in connection.execute(
                    """
                    SELECT 1 FROM question_family_members fm
                    JOIN question_families f ON f.id = fm.family_id
                    WHERE fm.question_id = ? AND f.family_kind = 'conclusion'
                    """,
                    (question_id,),
                ).fetchall()
            ):
                concl_title = str(secondary_conclusion.get("title") or "").strip()
                if concl_title:
                    family_id = self._find_family_by_title(
                        connection, "conclusion", concl_title
                    )
                    if family_id is None:
                        family_id = self._find_similar_family(
                            connection,
                            "conclusion",
                            concl_title,
                            str(secondary_conclusion.get("description") or ""),
                        )
                    if family_id is None:
                        cursor = connection.execute(
                            """
                            INSERT INTO question_families(
                                family_kind, title, description, derivation,
                                variant_pattern, confusion_notes, status,
                                created_at, updated_at
                            ) VALUES ('conclusion', ?, ?, ?, '', '', 'active', ?, ?)
                            """,
                            (
                                concl_title,
                                str(secondary_conclusion.get("description") or ""),
                                str(secondary_conclusion.get("derivation") or ""),
                                _utc_now(),
                                _utc_now(),
                            ),
                        )
                        family_id = int(cursor.lastrowid)
                    if family_id not in rejected:
                        connection.execute(
                            """
                            INSERT INTO question_family_members(
                                family_id, question_id, relation, created_at, provenance
                            )
                            VALUES (?, ?, 'member', ?, ?)
                            ON CONFLICT(family_id, question_id)
                            DO UPDATE SET relation = excluded.relation
                            """,
                            (
                                family_id,
                                question_id,
                                _utc_now(),
                                "AI 自动归类（保存时的二级结论建议）",
                            ),
                        )
                        auto_ids.append(family_id)
                        auto_labels.append(f"二级结论「{concl_title}」")
                        auto_reasons[family_id] = "AI 在这道题里给出了这条可复用的结论。"
        return {
            "auto": auto_ids,
            "auto_labels": auto_labels,
            "auto_reasons": auto_reasons,
            "recommended": recommended_ids,
            "new_drafts": draft_ids,
        }

    # ------------------------------------------------- review items (§8/§9)
    def list_review_items(
        self,
        *,
        question_id: int | None = None,
        status: str | None = "pending",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        """Review rows for the UI queue.

        ``status=None`` returns every state (admin/debug); the default
        ``pending`` feeds the "待你核对" queue.  Ordering is the simple,
        explainable priority from G2-B1 §9: MEDIUM decisions (which unlock
        reuse for the whole kind) come before LOW drafts, and within the
        same tier newer suggestions come first.  Deferred items simply
        leave this default view — they are kept, never deleted (§8).
        """

        sql = (
            "SELECT r.*, q.subject, q.stem_text AS stem_text "
            "FROM family_review_items r "
            "JOIN question_items q ON q.id = r.question_id"
        )
        clauses: list[str] = []
        parameters: list[object] = []
        if question_id is not None:
            clauses.append("r.question_id = ?")
            parameters.append(question_id)
        if status:
            clauses.append("r.status = ?")
            parameters.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += (
            " ORDER BY CASE r.confidence WHEN 'medium' THEN 0 ELSE 1 END,"
            " r.id DESC LIMIT ?"
        )
        parameters.append(limit)
        with self._database._connection() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [dict(row) for row in rows if not is_foreign_language_subject(row["subject"])]

    def list_review_groups(
        self, *, status: str = "pending"
    ) -> list[dict[str, object]]:
        """Pending items grouped per question (G2-B1 §5).

        The student answers "我现在要处理哪几道题", not "还有 31 条记录".
        Groups keep the queue priority order (MEDIUM-first, newer-first);
        the item list inside a group preserves that order too.
        """

        items = self.list_review_items(status=status)
        groups: dict[int, dict[str, object]] = {}
        order: list[int] = []
        for item in items:
            qid = int(item["question_id"])
            if qid not in groups:
                groups[qid] = {
                    "question_id": qid,
                    "stem_text": item["stem_text"],
                    "items": [],
                }
                order.append(qid)
            groups[qid]["items"].append(item)
        return [groups[qid] for qid in order]

    def defer_review_item(self, review_id: int) -> None:
        """「暂时不处理」— a first-class state, NOT a rejection (§8).

        Deferred rows stay in the database, generate no
        ``user_family_rejections``, and can be reopened later.
        """

        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT status FROM family_review_items WHERE id = ?",
                (review_id,),
            ).fetchone()
            _require(row is not None, f"待核对建议不存在：{review_id}")
            _require(
                str(row["status"]) in ("pending", "deferred"),
                "只有待处理/已搁置的建议可以搁置。",
            )
            connection.execute(
                "UPDATE family_review_items SET status = 'deferred', "
                "updated_at = ? WHERE id = ?",
                (_utc_now(), review_id),
            )

    def reopen_review_item(self, review_id: int) -> None:
        """「以后再看」→ 回到待核对。"""

        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT status FROM family_review_items WHERE id = ?",
                (review_id,),
            ).fetchone()
            _require(row is not None, f"待核对建议不存在：{review_id}")
            _require(str(row["status"]) == "deferred", "只有已搁置的建议可以恢复。")
            connection.execute(
                "UPDATE family_review_items SET status = 'pending', "
                "updated_at = ? WHERE id = ?",
                (_utc_now(), review_id),
            )

    def defer_review_items(self, review_ids: list[int]) -> int:
        """Batch 「暂不处理」 for a list of pending items (G2-B1 §7).

        Deliberately the ONLY batch action: batch-accepting AI categories
        would be a high-risk shortcut that defeats the whole review flow.
        Returns how many rows were deferred.
        """

        count = 0
        for review_id in review_ids:
            try:
                self.defer_review_item(int(review_id))
                count += 1
            except LearningWorkflowError:
                continue
        return count

    def confirm_review_item(
        self,
        review_id: int,
        *,
        family_id: int | None = None,
        also_question_ids: list[int] | None = None,
    ) -> int:
        """User accepts a recommendation → real membership, user provenance.

        For a medium item the user picks one of the suggested families
        (``family_id``); for a low item the draft becomes a formal family
        through the same anti-fragmentation reuse check (§15): a close
        existing family is reused instead of creating a near-duplicate.

        ``also_question_ids`` (G2-B1 cold start): when the accepted item
        is a shared base-family candidate, every question of the cluster
        joins the family in the same user decision, and those questions'
        own pending low drafts are marked accepted as subsumed — the user
        confirms ONCE instead of grinding through N identical LOW cards.
        """

        timestamp = _utc_now()
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT * FROM family_review_items WHERE id = ?", (review_id,)
            ).fetchone()
            _require(row is not None, f"待核对建议不存在：{review_id}")
            _require(
                str(row["status"]) == "pending",
                "这条建议已经处理过了。",
            )
            for qid in [int(row["question_id"]), *(also_question_ids or [])]:
                _require(
                    not is_foreign_language_subject(
                        QuestionService(self._database).get_question_item(qid).subject,
                    ), "外语学科只做第一层整理，不加入归纳族。",
                )
            kind = str(row["suggestion_kind"])
            title = str(row["title"])
            description = str(row["description"])
            target: int | None
            origin_note: str
            if family_id is not None:
                family = connection.execute(
                    "SELECT id FROM question_families "
                    "WHERE id = ? AND family_kind = ? AND status != 'retired'",
                    (family_id, kind),
                ).fetchone()
                _require(
                    family is not None,
                    f"所选族与该建议的类型不符或不存在：{family_id}",
                )
                target = int(family["id"])
                origin_note = f"AI 推荐后你确认了「{title}」"
            else:
                _require(
                    str(row["confidence"]) == "low",
                    "这条建议是已有族的推荐，请先选择要加入的族。",
                )
                # §15 dedup: exact → reuse; close variant → reuse.
                target = self._find_family_by_title(connection, kind, title)
                if target is None:
                    target = self._find_similar_family(
                        connection, kind, title, description
                    )
                if target is not None:
                    origin_note = (
                        f"你确认了 AI 建议的「{title}」，"
                        "已有相近的族，直接归入它（没有新建重复族）"
                    )
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO question_families(
                            family_kind, title, description, derivation,
                            variant_pattern, confusion_notes, status,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, '由你确认 AI 建议创建', '', '', 'active', ?, ?)
                        """,
                        (kind, title, description, timestamp, timestamp),
                    )
                    target = int(cursor.lastrowid)
                    origin_note = f"你确认了 AI 建议的新族「{title}」"
            cluster_questions = [int(row["question_id"])]
            cluster_questions += [
                int(qid)
                for qid in (also_question_ids or [])
                if int(qid) != int(row["question_id"])
            ]
            for cluster_qid in cluster_questions:
                connection.execute(
                    """
                    INSERT INTO question_family_members(
                        family_id, question_id, relation, created_at, provenance
                    )
                    VALUES (?, ?, 'member', ?, ?)
                    ON CONFLICT(family_id, question_id)
                    DO UPDATE SET relation = excluded.relation
                    """,
                    (
                        target,
                        cluster_qid,
                        timestamp,
                        origin_note
                        if cluster_qid == int(row["question_id"])
                        else f"你确认了同批共用的「{title}」（冷启动基础族）",
                    ),
                )
            if len(cluster_questions) > 1:
                # Subsume the cluster's own pending low drafts: their
                # intent is fulfilled by the shared family, so they close
                # as accepted instead of lingering in the queue (§15).
                placeholders = ",".join("?" for _ in cluster_questions)
                connection.execute(
                    "UPDATE family_review_items SET status = 'accepted', "
                    f"matched_family_id = ?, updated_at = ? "
                    f"WHERE question_id IN ({placeholders}) "
                    "AND suggestion_kind = ? AND confidence = 'low' "
                    "AND status = 'pending'",
                    [target, timestamp, *cluster_questions, kind],
                )
            connection.execute(
                "UPDATE family_review_items SET status = 'accepted', "
                "matched_family_id = ?, updated_at = ? WHERE id = ?",
                (target, timestamp, review_id),
            )
            return int(target)

    def accept_review_item_renamed(
        self, review_id: int, *, new_title: str
    ) -> int:
        """User renames a low-confidence draft, then accepts it (§73)."""

        clean = new_title.strip()
        _require(bool(clean), "新名称不能为空。")
        with self._database._connection() as connection:
            connection.execute(
                "UPDATE family_review_items SET title = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (clean, _utc_now(), review_id),
            )
        return self.confirm_review_item(review_id)

    def reject_review_item(self, review_id: int, *, note: str = "") -> None:
        """User declines a recommendation; the refusal persists (§21)."""

        timestamp = _utc_now()
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT * FROM family_review_items WHERE id = ?", (review_id,)
            ).fetchone()
            _require(row is not None, f"待核对建议不存在：{review_id}")
            _require(str(row["status"]) == "pending", "这条建议已经处理过了。")
            connection.execute(
                "UPDATE family_review_items SET status = 'rejected', "
                "updated_at = ? WHERE id = ?",
                (timestamp, review_id),
            )
            matched = row["matched_family_id"]
            if matched is not None:
                connection.execute(
                    """
                    INSERT INTO user_family_rejections(
                        question_id, family_id, note, created_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(question_id, family_id) DO NOTHING
                    """,
                    (int(row["question_id"]), int(matched), note, timestamp),
                )
            elif str(row["confidence"]) == "low":
                connection.execute(
                    """
                    INSERT INTO user_family_rejections(
                        question_id, family_id, candidate_title,
                        candidate_kind, note, created_at
                    ) VALUES (?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        int(row["question_id"]),
                        str(row["title"]),
                        str(row["suggestion_kind"]),
                        note,
                        timestamp,
                    ),
                )

    def rejected_candidate_titles(self, question_id: int) -> set[str]:
        """Draft titles the user declined for this question (LOW path)."""

        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT candidate_title FROM user_family_rejections "
                "WHERE question_id = ? AND candidate_title != ''",
                (question_id,),
            ).fetchall()
        return {str(row["candidate_title"]) for row in rows}

    # ------------------------------------- cold start batch base (G2-B1)
    _PAGE_TITLE_MARKER_PATTERN: Final = ("第", "页", "卷")

    def propose_batch_base_family(
        self, question_ids: list[int], *, suggestion_kind: str
    ) -> tuple[int, list[int]] | None:
        """One shared candidate base family for a same-batch LOW cluster.

        Cold start (G2B-M2): the first questions of a new subject are all
        legitimately LOW — the honest fix is NOT lowering thresholds (§13)
        but helping the user build the FIRST base family in ONE decision.
        When ≥2 pending LOW drafts of the same kind share a non-trivial
        semantic core (token intersection of title+description), this
        proposes ONE candidate draft covering the cluster and returns
        ``(review_item_id, cluster_question_ids)``; the user confirms
        once, every question joins, and future similar questions reach
        MEDIUM/HIGH through normal reuse.  The title must come from the
        shared method/type core — page/document identifiers are rejected
        (§17/§18), and nothing is created without the user.
        """

        _require(
            suggestion_kind in ("type", "method"),
            "族类型必须是 type 或 method。",
        )
        drafts: list[dict[str, object]] = []
        for qid in question_ids:
            drafts.extend(
                item
                for item in self.list_review_items(question_id=int(qid))
                if str(item["suggestion_kind"]) == suggestion_kind
                and str(item["confidence"]) == "low"
                and str(item["status"]) == "pending"
            )
        if len(drafts) < 2:
            return None
        token_sets: list[tuple[dict[str, object], set[str]]] = []
        for item in drafts:
            tokens = {
                token
                for token in _keywords(
                    f"{item['title']} {item['description']}"
                )
                if len(token) >= 2
            }
            token_sets.append((item, tokens))
        shared: set[str] = set.intersection(
            *(tokens for _item, tokens in token_sets)
        )
        if len(shared) < 2:
            return None
        # Order the shared core by each token's position in the shortest
        # title STRING (sets are unordered — never join a set directly).
        shortest = min(
            drafts, key=lambda item: len(str(item["title"]))
        )
        shortest_title = str(shortest["title"])
        ordered: list[str] = []
        placed_spans: list[tuple[int, int]] = []
        for token in sorted(
            (token for token in shared if token in shortest_title),
            key=lambda token: shortest_title.find(token),
        ):
            position = shortest_title.find(token)
            # Skip tokens already covered by a placed token's span so the
            # candidate reads "区位分析", never "区位分析分析题".
            if any(start <= position < end for start, end in placed_spans):
                continue
            ordered.append(token)
            placed_spans.append((position, position + len(token)))
        title = "".join(ordered).strip()
        if len(title) < 4:
            return None
        lowered = title
        # §17/§18: a family named after where questions came from has no
        # learning value; the core must be the shared method/type.
        if any(marker in lowered for marker in self._PAGE_TITLE_MARKER_PATTERN):
            return None
        with self._database._connection() as connection:
            document_titles = [
                str(row["title"])
                for row in connection.execute(
                    "SELECT title FROM documents"
                ).fetchall()
            ]
        # §17/§18: reject when the candidate title and a document title
        # overlap as strings in EITHER direction — "如皋地理" (⊂ "01如皋地理")
        # names the source, not a method or type.
        if any(
            doc_title
            and (doc_title in title or title in doc_title)
            for doc_title in document_titles
        ):
            return None
        # One live shared proposal per (cluster, kind): re-running never
        # duplicates candidates (§22 idempotency).
        existing = self.list_review_items(status="pending")
        member_qids = {int(item["question_id"]) for item in drafts}
        for item in existing:
            if (
                str(item["suggestion_kind"]) == suggestion_kind
                and str(item["title"]) == title
            ):
                return int(item["id"]), sorted(member_qids)
        question_id = int(shortest["question_id"])
        others = len(member_qids - {question_id})
        timestamp = _utc_now()
        with self._database._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO family_review_items(
                    question_id, suggestion_kind, confidence, title,
                    description, reason, matched_family_id, status,
                    created_at, updated_at
                ) VALUES (?, ?, 'low', ?, ?, ?, NULL, 'pending', ?, ?)
                """,
                (
                    question_id,
                    suggestion_kind,
                    title,
                    f"同批 {len(member_qids)} 道题的共同点",
                    (
                        f"这批题里还有 {others} 道题的建议和它共享同一个核心，"
                        "可以一起整理为一个基础族；确认一次，整批归入。"
                    ),
                    timestamp,
                    timestamp,
                ),
            )
        return int(cursor.lastrowid), sorted(member_qids)

    def conclusion_frequency(self, limit: int = 10) -> list[dict[str, object]]:
        """Backend frequency ranking of secondary conclusions (§14)."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT f.id, f.title, f.description,
                       (SELECT COUNT(*) FROM question_family_members m
                        WHERE m.family_id = f.id) AS question_count
                FROM question_families f
                WHERE f.family_kind = 'conclusion' AND f.status != 'retired'
                ORDER BY question_count DESC, f.id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def fragmentation_stats(self) -> dict[str, int]:
        """Layer-2 health metrics for the overnight large-sample check (§20)."""

        with self._database._connection() as connection:
            families = connection.execute(
                """
                SELECT f.id, f.family_kind,
                       (SELECT COUNT(*) FROM question_family_members m
                        WHERE m.family_id = f.id) AS member_count
                FROM question_families f WHERE f.status != 'retired'
                """
            ).fetchall()
            multi_rows = connection.execute(
                """
                SELECT question_id, COUNT(*) AS family_count
                FROM question_family_members GROUP BY question_id
                HAVING COUNT(*) > 1
                """
            ).fetchall()
            question_total = connection.execute(
                "SELECT COUNT(*) FROM question_items"
            ).fetchone()[0]
        by_kind = {"type": 0, "method": 0, "conclusion": 0}
        for row in families:
            kind = str(row["family_kind"])
            if kind in by_kind:
                by_kind[kind] += 1
        singleton = sum(1 for row in families if int(row["member_count"]) <= 1)
        return {
            "question_items": int(question_total),
            "type_families": by_kind["type"],
            "method_families": by_kind["method"],
            "conclusion_families": by_kind["conclusion"],
            "families_total": len(families),
            "singleton_families": singleton,
            "multi_family_questions": len(multi_rows),
        }

    def _member_from_row(self, row) -> QuestionItem:
        """Row -> QuestionItem for join queries (mirrors QuestionService._from_row)."""

        return QuestionItem(
            id=int(row["id"]),
            document_id=int(row["document_id"]) if row["document_id"] is not None else None,
            page_id=int(row["page_id"]) if row["page_id"] is not None else None,
            question_number=str(row["question_number"]),
            question_kind=str(row["question_kind"]),
            subject=(str(row["subject"]) if "subject" in row.keys() else ""),
            stem_text=str(row["stem_text"]),
            stem_confidence=str(row["stem_confidence"]),
            student_answer=str(row["student_answer"]),
            teacher_verdict=row["teacher_verdict"],
            teacher_comment=str(row["teacher_comment"]),
            correction_note=str(row["correction_note"]),
            analysis_note=str(row["analysis_note"]) if "analysis_note" in row.keys() else "",
            reason_tags=_load_tags(str(row["reason_tags"])),
            method_tags=_load_tags(str(row["method_tags"])),
            solution_method=(
                str(row["solution_method"]) if "solution_method" in row.keys() else ""
            ),
            math_display=(
                _optional_json_object(row["math_display_json"])
                if "math_display_json" in row.keys() else None
            ),
            source_region=_optional_json_object(row["source_region"]),
            ai_draft=_optional_json_object(row["ai_draft"]),
            user_edited=bool(row["user_edited"]),
            status=str(row["status"]),
            source_document_title_snapshot=(
                str(row["source_document_title_snapshot"])
                if "source_document_title_snapshot" in row.keys()
                else ""
            ),
            source_page_label_snapshot=(
                str(row["source_page_label_snapshot"])
                if "source_page_label_snapshot" in row.keys()
                else ""
            ),
            source_printed_page_number=(
                int(row["source_printed_page_number"])
                if "source_printed_page_number" in row.keys()
                and row["source_printed_page_number"] is not None
                else None
            ),
        )


# --------------------------------------------------------------- layer 3
class MasteryService:
    """Practice records with 会做/会讲 separation and light review scheduling."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def record_practice(
        self,
        question_id: int,
        *,
        outcome: str,
        can_explain: bool | None = None,
        note: str = "",
        practiced_at: str | None = None,
    ) -> int:
        _require(outcome in _OUTCOMES, "练习结果必须是 correct/incorrect/partial。")
        _require(
            not is_foreign_language_subject(
                QuestionService(self._database).get_question_item(question_id).subject,
            ), "外语学科在第一层整理结束，不安排掌握训练。",
        )
        timestamp = practiced_at or _utc_now()
        with self._database._connection() as connection:
            question = connection.execute(
                "SELECT id FROM question_items WHERE id = ?", (question_id,)
            ).fetchone()
            _require(question is not None, f"整理题目不存在：{question_id}")
            cursor = connection.execute(
                """
                INSERT INTO mastery_records(
                    question_id, practiced_at, outcome, can_explain, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (question_id, timestamp, outcome, can_explain, note, _utc_now()),
            )
            return int(cursor.lastrowid)

    def record_explanation(self, question_id: int, *, can_explain: bool, note: str = "") -> int:
        """会讲 test: separate from 会做 so both can be tracked independently."""

        return self.record_practice(
            question_id,
            outcome="correct" if can_explain else "partial",
            can_explain=can_explain,
            note=note or ("会讲通过" if can_explain else "会做不会讲"),
        )

    def question_mastery_summary(self, question_id: int) -> dict[str, object]:
        with self._database._connection() as connection:
            legacy_rows = connection.execute(
                """
                SELECT outcome, can_explain FROM mastery_records
                WHERE question_id = ? ORDER BY practiced_at, id
                """,
                (question_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT result AS outcome, event_type, explanation_state,
                       source, created_at, id
                FROM mastery_evidence
                WHERE question_id = ?
                ORDER BY created_at, id
                """,
                (question_id,),
            ).fetchall()
        practice_rows = [
            row
            for row in evidence_rows
            if row["source"] == "system_training"
            and row["event_type"] in (
                "practice", "method_trigger", "boundary_check", "review"
            )
            and row["outcome"] in _OUTCOMES
        ]
        # v27 introduced the evidence ledger while keeping mastery_records for
        # compatibility.  Prefer real system evidence when it exists; falling
        # back preserves legacy callers without double-counting migrated rows.
        rows = practice_rows or legacy_rows
        total = len(rows)
        correct = sum(1 for row in rows if row["outcome"] == "correct")
        evidence_explanations = [
            row for row in evidence_rows if row["event_type"] == "explain_back"
        ]
        legacy_explanations = [
            bool(row["can_explain"])
            for row in legacy_rows
            if row["can_explain"] is not None
        ]
        if evidence_explanations:
            can_explain = bool(self.teachback_status(question_id)["passed"])
        else:
            can_explain = bool(legacy_explanations) and legacy_explanations[-1]
        return {
            "total": total,
            "correct": correct,
            "can_do": bool(rows) and rows[-1]["outcome"] in ("correct", "partial"),
            "can_explain": can_explain,
            "needs_review": bool(rows) and rows[-1]["outcome"] == "incorrect",
        }

    def list_practice_records(self, question_id: int) -> list[dict[str, object]]:
        """Return the full practice trail of one question (oldest first).

        ``mastery_records`` is the legacy manual log.  System training now
        writes ``mastery_evidence``; hiding those rows made the UI claim a
        non-zero history while offering no expandable history at all.  The
        two sources are normalized here and obvious migration duplicates are
        collapsed without deleting either table.
        """

        with self._database._connection() as connection:
            legacy_rows = connection.execute(
                """
                SELECT id, practiced_at, outcome, can_explain, note, created_at
                FROM mastery_records WHERE question_id = ? ORDER BY practiced_at, id
                """,
                (question_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT id, created_at AS practiced_at, result AS outcome,
                       NULL AS can_explain,
                       CASE WHEN trim(provenance) <> '' THEN provenance
                            ELSE user_note END AS note,
                       created_at, source
                FROM mastery_evidence
                WHERE question_id = ?
                  AND event_type IN ('practice', 'method_trigger',
                                     'boundary_check', 'review')
                  AND result IN ('correct', 'incorrect', 'partial')
                ORDER BY created_at, id
                """,
                (question_id,),
            ).fetchall()
        normalized_evidence = [dict(row) for row in evidence_rows]
        if not normalized_evidence:
            return [dict(row) for row in legacy_rows]
        seen = {
            (str(row["practiced_at"]), str(row["outcome"]))
            for row in normalized_evidence
        }
        combined = normalized_evidence + [
            dict(row)
            for row in legacy_rows
            if (str(row["practiced_at"]), str(row["outcome"])) not in seen
        ]
        return sorted(
            combined,
            key=lambda row: (str(row["practiced_at"]), int(row["id"])),
        )

    def list_review_queue(self) -> list[dict[str, object]]:
        """Return family review profiles ordered by next review date."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT p.id, p.family_id, f.title, f.family_kind, p.weak_points,
                       p.next_review_at, p.review_interval_days, p.updated_at
                FROM mastery_profiles p LEFT JOIN question_families f
                    ON f.id = p.family_id
                ORDER BY p.next_review_at IS NULL, p.next_review_at, p.family_id
                """
            ).fetchall()
            members = connection.execute(
                "SELECT m.family_id, q.subject FROM question_family_members m "
                "JOIN question_items q ON q.id = m.question_id",
            ).fetchall()
        foreign = {row["family_id"] for row in members
                   if is_foreign_language_subject(row["subject"])}
        regular = {row["family_id"] for row in members
                   if not is_foreign_language_subject(row["subject"])}
        return [dict(row) for row in rows if row["family_id"] not in foreign - regular]

    def bump_family_profile(
        self, family_id: int, *, outcome: str, weak_points: list[str] | None = None
    ) -> None:
        """One review-interval step: correct doubles, otherwise it resets."""

        _require(outcome in _OUTCOMES, "练习结果必须是 correct/incorrect/partial。")
        timestamp = _utc_now()
        with self._database._connection() as connection:
            family = connection.execute(
                "SELECT id FROM question_families WHERE id = ?", (family_id,)
            ).fetchone()
            _require(family is not None, f"题型族不存在：{family_id}")
            row = connection.execute(
                "SELECT id, review_interval_days FROM mastery_profiles WHERE family_id = ?",
                (family_id,),
            ).fetchone()
            if row is None:
                interval = 2 if outcome == "correct" else 1
                connection.execute(
                    """
                    INSERT INTO mastery_profiles(
                        family_id, scope, weak_points, next_review_at,
                        review_interval_days, updated_at
                    ) VALUES (?, 'family', ?, datetime('now', ? || ' days'), ?, ?)
                    """,
                    (
                        family_id,
                        _dump_tags(weak_points or []),
                        str(interval),
                        interval,
                        timestamp,
                    ),
                )
            else:
                interval = (
                    int(row["review_interval_days"]) * 2 if outcome == "correct" else 1
                )
                connection.execute(
                    """
                    UPDATE mastery_profiles SET
                        weak_points = ?, next_review_at = datetime('now', ? || ' days'),
                        review_interval_days = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        _dump_tags(weak_points) if weak_points is not None else "[]",
                        str(interval),
                        interval,
                        timestamp,
                        int(row["id"]),
                    ),
                )

    # ------------------------------------------------- self-explanation (§15)
    def record_explanation_attempt(
        self,
        question_id: int,
        *,
        content: str,
        feedback: str = "",
    ) -> int:
        """Store one “我自己讲一遍” attempt with its AI review feedback.

        Attempts are a first-class history (第一/二/三/四次对比：会做但讲不清
        → 逐渐能讲明白), separate from the plain 会讲 checkbox records.  The
        student's own words are stored verbatim; AI feedback never replaces
        them.
        """

        _require(bool(content.strip()), "自我讲解内容不能为空。")
        timestamp = _utc_now()
        with self._database._connection() as connection:
            question = connection.execute(
                "SELECT id FROM question_items WHERE id = ?", (question_id,)
            ).fetchone()
            _require(question is not None, f"整理题目不存在：{question_id}")
            cursor = connection.execute(
                """
                INSERT INTO explanation_attempts(
                    question_id, content, feedback, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (question_id, content, feedback, timestamp, timestamp),
            )
            return int(cursor.lastrowid)

    def update_explanation_feedback(self, attempt_id: int, feedback: str) -> None:
        """Attach the AI review feedback to one stored attempt."""

        with self._database._connection() as connection:
            cursor = connection.execute(
                "UPDATE explanation_attempts SET feedback = ?, updated_at = ? WHERE id = ?",
                (feedback, _utc_now(), attempt_id),
            )
            if cursor.rowcount == 0:
                raise LearningWorkflowError(f"讲解尝试不存在：{attempt_id}")

    def list_explanation_attempts(self, question_id: int) -> list[dict[str, object]]:
        """Return one question's explanation attempts (oldest first)."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, content, feedback, created_at
                FROM explanation_attempts WHERE question_id = ? ORDER BY id
                """,
                (question_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def teachback_status(self, question_id: int) -> dict[str, object]:
        """Combine the two independent teach-back assessments with OR.

        The latest self-report and latest AI review remain separately
        attributable.  Even when both pass, the question has one combined
        teach-back pass, never two mastery passes or a stability claim.
        """

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT source, explanation_state FROM mastery_evidence
                WHERE question_id = ? AND event_type = 'explain_back'
                ORDER BY created_at DESC, id DESC
                """,
                (question_id,),
            ).fetchall()
        manual_state = "unverified"
        ai_state = "unverified"
        for row in rows:
            source = str(row["source"])
            state = str(row["explanation_state"])
            if source == "user_manual" and manual_state == "unverified":
                manual_state = state
            elif source in ("system_training", "ai_generated") and ai_state == "unverified":
                ai_state = state
            if manual_state != "unverified" and ai_state != "unverified":
                break
        manual_pass = manual_state in ("basically_clear", "complete")
        ai_pass = ai_state in ("basically_clear", "complete")
        passed = manual_pass or ai_pass
        if ai_state == "complete":
            combined_state = "complete"
        elif passed:
            combined_state = "basically_clear"
        elif manual_state == "gaps" or ai_state == "gaps":
            combined_state = "gaps"
        else:
            combined_state = "unverified"
        return {
            "manual_state": manual_state,
            "ai_state": ai_state,
            "manual_pass": manual_pass,
            "ai_pass": ai_pass,
            "passed": passed,
            "pass_count": int(passed),
            "combined_state": combined_state,
        }

    # --------------------------------------- G2-C mastery evidence loop
    #: Deterministic, explainable review schedule (G2-C §26/§27): no
    #: pseudo-scientific memory model — intervals a student could guess.
    REVIEW_INTERVALS: Final[dict[str, int]] = {
        "first_error": 0,          # 初次错误 → 当天再试一次
        "first_independent": 3,    # 第一次独立正确 → 3 天后
        "repeated_independent": 7, # 连续独立正确 → 7 天后
        "stable": 21,              # 稳定正确 → 21 天后
    }

    EVIDENCE_EVENT_LABELS: Final[dict[str, str]] = {
        "practice": "重做训练",
        "explain_back": "讲解验证",
        "method_trigger": "方法触发训练",
        "boundary_check": "边界辨析训练",
        "review": "间隔复习",
    }

    NEXT_ACTION_LABELS: Final[dict[str, str]] = {
        "redo_original": "重做原题",
        "targeted_variation": "做一道同族变式",
        "explain_back": "把这个方法讲给我听",
        "method_trigger": "方法触发训练",
        "boundary_check": "边界条件辨析",
        "spaced_review": "间隔复习",
        "no_evidence_baseline": "先做一遍原题",
    }

    def record_evidence(
        self,
        question_id: int,
        *,
        event_type: str,
        result: str = "unevaluated",
        independence: str = "unknown",
        explanation_state: str = "unverified",
        hint_used: bool = False,
        source: str,
        provenance: str = "",
        ai_evaluation: str = "",
        user_note: str = "",
        idempotency_key: str | None = None,
        family_id: int | None = None,
    ) -> int:
        """Append one mastery evidence event (G2-C §52) and reschedule.

        ``idempotency_key`` makes repeated submissions (double click,
        refresh-replay, browser back) collapse into ONE evidence row
        (§105).  ``source`` must say who produced the event — a manual
        tick can never pose as a system training result (§17).
        """

        _require(
            event_type in self.EVIDENCE_EVENT_LABELS,
            f"证据类型无效：{event_type}",
        )
        _require(
            not is_foreign_language_subject(
                QuestionService(self._database).get_question_item(question_id).subject,
            ), "外语学科在第一层整理结束，不安排训练或复盘。",
        )
        _require(
            source in ("system_training", "user_manual", "ai_generated"),
            "证据来源必须是 system_training/user_manual/ai_generated。",
        )
        timestamp = _utc_now()
        with self._database._connection() as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT id FROM mastery_evidence WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    return int(existing["id"])
            question = connection.execute(
                "SELECT id FROM question_items WHERE id = ?", (question_id,)
            ).fetchone()
            _require(question is not None, f"整理题目不存在：{question_id}")
            resolved_family = family_id
            if resolved_family is None:
                fam_row = connection.execute(
                    """
                    SELECT f.id FROM question_family_members fm
                    JOIN question_families f ON f.id = fm.family_id
                    WHERE fm.question_id = ? ORDER BY f.id LIMIT 1
                    """,
                    (question_id,),
                ).fetchone()
                resolved_family = (
                    int(fam_row["id"]) if fam_row is not None else None
                )
            interval = self._next_interval_days(
                connection, question_id, event_type, result,
                independence, hint_used,
            )
            next_review = (
                (
                    datetime.now(UTC) + timedelta(days=interval)
                ).isoformat(timespec="seconds")
                if interval is not None
                else None
            )
            cursor = connection.execute(
                """
                INSERT INTO mastery_evidence(
                    question_id, family_id, event_type, result, independence,
                    explanation_state, hint_used, source, provenance,
                    ai_evaluation, user_note, next_review_at, review_status,
                    idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)
                """,
                (
                    question_id,
                    resolved_family,
                    event_type,
                    result,
                    independence,
                    explanation_state,
                    1 if hint_used else 0,
                    source,
                    provenance,
                    ai_evaluation,
                    user_note,
                    next_review,
                    idempotency_key,
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def _next_interval_days(
        self,
        connection,
        question_id: int,
        event_type: str,
        result: str,
        independence: str,
        hint_used: bool,
    ) -> int | None:
        """Explainable interval choice (§27); None = schedule nothing.

        0 means "due today" — a genuinely scheduled value, NOT the same
        as unscheduled.
        """

        if event_type == "explain_back":
            return 3 if result in ("correct", "partial") else 1
        if result == "incorrect":
            return self.REVIEW_INTERVALS["first_error"]
        if result in ("correct", "partial"):
            prior_independent = connection.execute(
                """
                SELECT COUNT(*) AS n FROM mastery_evidence
                WHERE question_id = ? AND result = 'correct'
                  AND independence = 'independent'
                  AND source = 'system_training'
                """,
                (question_id,),
            ).fetchone()["n"]
            independent_now = (
                independence == "independent" and not hint_used
            )
            if not independent_now:
                # A hinted success is still short-cycle: retry soon
                # without hints (§28 提示依赖 → 无提示重试).
                return self.REVIEW_INTERVALS["first_error"]
            if prior_independent == 0:
                return self.REVIEW_INTERVALS["first_independent"]
            if prior_independent == 1:
                return self.REVIEW_INTERVALS["repeated_independent"]
            return self.REVIEW_INTERVALS["stable"]
        return None

    def list_review_items_for_question(
        self, question_id: int
    ) -> list[dict[str, object]]:
        """Full evidence trail of one question (oldest first, §52 ledger)."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, event_type, result, independence,
                       explanation_state, hint_used, source, provenance,
                       ai_evaluation, user_note, next_review_at,
                       review_status, created_at
                FROM mastery_evidence WHERE question_id = ?
                ORDER BY created_at, id
                """,
                (question_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def defer_review(self, evidence_id: int) -> None:
        """「稍后再练」 — never recorded as a failure (§75/§76)."""

        timestamp = _utc_now()
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT id, next_review_at FROM mastery_evidence WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            _require(row is not None, f"掌握证据不存在：{evidence_id}")
            base = str(row["next_review_at"] or _utc_now())
            try:
                shifted = (
                    datetime.fromisoformat(base) + timedelta(days=1)
                ).isoformat(timespec="seconds")
            except ValueError:
                shifted = (
                    datetime.now(UTC) + timedelta(days=1)
                ).isoformat(timespec="seconds")
            connection.execute(
                "UPDATE mastery_evidence SET next_review_at = ?, "
                "review_status = 'deferred', provenance = provenance || '　"
                "用户选择了稍后（不计失败）', updated_at = ? WHERE id = ?",
                (shifted, timestamp, evidence_id),
            )

    def mastery_states(self, question_id: int) -> dict[str, str]:
        """Four student-facing mastery states for one question (§14/§47-§51).

        NO EVIDENCE is distinct from FAILED everywhere: “尚未验证” must
        never render as “不会”.  Internal data may be numeric; the UI
        states never pretend to precision (no percentages).
        """

        with self._database._connection() as connection:
            practice = connection.execute(
                """
                SELECT result, independence, hint_used, source, created_at
                FROM mastery_evidence WHERE question_id = ?
                  AND event_type IN ('practice', 'method_trigger',
                                     'boundary_check', 'review')
                ORDER BY created_at, id
                """,
                (question_id,),
            ).fetchall()
        system_practice = [
            row
            for row in practice
            if row["source"] == "system_training"
        ]
        if not system_practice:
            do_state = "no_evidence"
        else:
            last = system_practice[-1]
            if last["result"] == "incorrect":
                do_state = "needs_practice"
            elif last["independence"] == "independent" and not last["hint_used"]:
                do_state = "independent"
            else:
                do_state = "basically_ok"
        correct_rows = [
            row
            for row in system_practice
            if row["result"] == "correct"
            and row["independence"] == "independent"
            and not row["hint_used"]
        ]
        if not correct_rows:
            stability = "unverified"
        elif len(correct_rows) == 1:
            stability = "temporary"
        else:
            try:
                first = datetime.fromisoformat(str(correct_rows[0]["created_at"]))
                last = datetime.fromisoformat(str(correct_rows[-1]["created_at"]))
                spaced = (last - first).days >= 2
            except ValueError:
                spaced = False
            stability = "spaced" if spaced else "repeated"
        latest = system_practice[-1] if system_practice else None
        if latest is None:
            independence = "unknown"
        elif latest["independence"] in ("heavy_hint", "after_answer"):
            independence = "hint_dependent"
        elif latest["independence"] == "light_hint" or bool(latest["hint_used"]):
            independence = "light_hint"
        elif latest["independence"] == "independent":
            independence = "independent"
        else:
            independence = "unknown"
        explain_state = str(self.teachback_status(question_id)["combined_state"])
        return {
            "do_state": do_state,
            "explain_state": explain_state,
            "independence": independence,
            "stability": stability,
        }

    def next_action(self, question_id: int) -> dict[str, object]:
        """The ONE thing worth doing next for this question (§4/§63).

        Deterministic rules over layer-1/layer-2 assets — no LLM call for
        the recommendation itself.  Every recommendation carries a WHY
        (§8); “AI 推荐你练习” style empty reasons are impossible here.
        """

        _require(
            not is_foreign_language_subject(
                QuestionService(self._database).get_question_item(question_id).subject,
            ), "外语学科在第一层整理结束，没有后续训练任务。",
        )
        states = self.mastery_states(question_id)
        with self._database._connection() as connection:
            question = connection.execute(
                "SELECT stem_text, teacher_verdict, method_tags "
                "FROM question_items WHERE id = ?",
                (question_id,),
            ).fetchone()
            if question is None:
                raise LearningWorkflowError(f"整理题目不存在：{question_id}")
            last_practice = connection.execute(
                """
                SELECT event_type, result, independence, hint_used,
                       explanation_state, source
                FROM mastery_evidence WHERE question_id = ?
                  AND source = 'system_training'
                  AND event_type IN ('practice', 'method_trigger',
                                     'boundary_check', 'review')
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (question_id,),
            ).fetchone()
            family_row = connection.execute(
                """
                SELECT f.id, f.title FROM question_family_members fm
                JOIN question_families f ON f.id = fm.family_id
                WHERE fm.question_id = ? ORDER BY f.id LIMIT 1
                """,
                (question_id,),
            ).fetchone()
            family_has_boundary = False
            if family_row is not None:
                family_has_boundary = bool(
                    connection.execute(
                        "SELECT 1 FROM wing_entries WHERE family_id = ? "
                        "AND wing_kind = 'boundary_counterexample' LIMIT 1",
                        (family_row["id"],),
                    ).fetchone()
                )
        why: list[str] = []
        if last_practice is None:
            if str(question["teacher_verdict"]) == "incorrect":
                why.append("第一层记录里这道题上次是做错的，先用原题确认现在能不能独立完成。")
            else:
                why.append("这道题还没有任何掌握证据——先做一遍，系统才知道从哪里帮你。")
            return self._action("no_evidence_baseline", why, question_id)
        if str(last_practice["result"]) == "incorrect":
            if states["independence"] in ("hint_dependent",) or bool(
                last_practice["hint_used"]
            ):
                why.append("上次没有做对，而且过程中依赖了提示——先恢复方法触发的思路。")
                return self._action("method_trigger", why, question_id)
            if family_has_boundary:
                why.append("上次做错了；如果错在条件判断，边界辨析比直接重做更能定位问题。")
                return self._action("boundary_check", why, question_id)
            why.append("上次没有做对——重做一遍，检验是哪一步卡住。")
            return self._action("redo_original", why, question_id)
        if str(last_practice["result"]) in ("correct", "partial"):
            if str(last_practice["independence"]) != "independent" or bool(
                last_practice["hint_used"]
            ):
                why.append("上次做对了但用了提示——换一道同类题、不用提示，才能算独立掌握。")
                return self._action("targeted_variation", why, question_id)
            if states["explain_state"] in ("unverified", "gaps"):
                why.append("已经能做对，但还没有通过「讲给我听」的验证——会做不等于会讲。")
                return self._action("explain_back", why, question_id)
            why.append("已能独立完成并通过讲解验证——隔几天再做一次变式，确认没有忘。")
            return self._action("spaced_review", why, question_id)
        why.append("最近的证据不足以判断，先重做一遍原题。")
        return self._action("redo_original", why, question_id)

    def _action(
        self, task_type: str, why: list[str], question_id: int
    ) -> dict[str, object]:
        payload: dict[str, object] = {"question_id": question_id}
        if task_type == "targeted_variation":
            variation = self.find_family_variation(question_id)
            payload["variation"] = variation
        elif task_type == "boundary_check":
            payload["boundary"] = self.family_boundary_pack(question_id)
        elif task_type == "method_trigger":
            payload["trigger"] = self.family_trigger_pack(question_id)
        return {
            "task_type": task_type,
            "task_label": self.NEXT_ACTION_LABELS[task_type],
            "why": why,
            "payload": payload,
        }

    def find_family_variation(self, question_id: int) -> dict[str, object]:
        """Pick a REAL question from the same family (§29-§31/§33).

        Prefers a different source document (异图同法) over the same page
        (同图异问); AI generation is a last resort and this round the
        engine never fabricates one — no real sibling means the payload
        honestly degrades to the original question.
        """

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT q.id, q.question_number, substr(q.stem_text, 1, 60) AS stem,
                       d.title AS doc_title, p.page_number, q.page_id
                FROM question_family_members fm
                JOIN question_items q ON q.id = fm.question_id
                LEFT JOIN pages p ON p.id = q.page_id
                LEFT JOIN documents d ON d.id = q.document_id
                WHERE fm.family_id = (
                    SELECT fm2.family_id FROM question_family_members fm2
                    WHERE fm2.question_id = ? LIMIT 1
                ) AND q.id != ?
                ORDER BY CASE WHEN q.document_id != (
                    SELECT document_id FROM question_items WHERE id = ?
                ) THEN 0 ELSE 1 END, q.id
                LIMIT 1
                """,
                (question_id, question_id, question_id),
            ).fetchone()
        if rows is None:
            return {
                "available": False,
                "provenance": "同族还没有其它真实题，先用原题巩固。",
            }
        provenance = "来自你的资料"
        if rows["doc_title"]:
            provenance += f"《{rows['doc_title']}》"
            if rows["page_number"] is not None:
                provenance += f"第 {rows['page_number']} 页"
        return {
            "available": True,
            "question_id": int(rows["id"]),
            "question_number": rows["question_number"],
            "stem": rows["stem"],
            "page_id": rows["page_id"],
            "provenance": provenance,
        }

    def family_boundary_pack(self, question_id: int) -> dict[str, object]:
        """Boundary scenarios for a BOUNDARY_CHECK task (§36/§37)."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT w.id, w.validity_conditions, w.invalidation_conditions,
                       w.common_misuses, w.origin, w.confidence, w.status
                FROM wing_entries w
                WHERE w.family_id = (
                    SELECT fm.family_id FROM question_family_members fm
                    WHERE fm.question_id = ? LIMIT 1
                ) AND w.wing_kind = 'boundary_counterexample'
                ORDER BY w.id DESC LIMIT 1
                """,
                (question_id,),
            ).fetchone()
        if rows is None:
            return {"available": False}
        return {
            "available": True,
            "wing_entry_id": int(rows["id"]),
            "validity": _load_tags(str(rows["validity_conditions"])),
            "invalidation": _load_tags(str(rows["invalidation_conditions"])),
            "misuses": _load_tags(str(rows["common_misuses"])),
            "provenance": (
                "AI 补充，建议核对"
                if rows["origin"] == "ai_draft" and rows["status"] != "confirmed"
                else "你已核对"
            ),
        }

    def family_trigger_pack(self, question_id: int) -> dict[str, object]:
        """Method-trigger cues for a METHOD_TRIGGER task (§35)."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT w.id, w.trigger_conditions, w.recognition_signals,
                       w.candidate_method, w.origin, w.status
                FROM wing_entries w
                WHERE w.family_id = (
                    SELECT fm.family_id FROM question_family_members fm
                    WHERE fm.question_id = ? LIMIT 1
                ) AND w.wing_kind = 'method_trigger'
                ORDER BY w.id DESC LIMIT 1
                """,
                (question_id,),
            ).fetchone()
        if rows is None:
            return {"available": False, "question_method_tags": []}
        tags_row = connection.execute(
            "SELECT method_tags FROM question_items WHERE id = ?",
            (question_id,),
        ).fetchone()
        return {
            "available": True,
            "wing_entry_id": int(rows["id"]),
            "triggers": _load_tags(str(rows["trigger_conditions"])),
            "signals": _load_tags(str(rows["recognition_signals"])),
            "method": str(rows["candidate_method"]),
            "provenance": (
                "AI 补充，建议核对"
                if rows["origin"] == "ai_draft" and rows["status"] != "confirmed"
                else "你已核对"
            ),
            "question_method_tags": _load_tags(
                str(tags_row["method_tags"]) if tags_row else "[]"
            ),
        }

    def today_review_items(self, limit: int = 5) -> list[dict[str, object]]:
        """The few review items worth attention today (§24/§25/§74).

        Priority: recent failure > repeated failure > hint dependence >
        items due for spaced review.  Overdue is phrased as「已经到了适合
        复习的时间」— no punishment (§72).  At most ``limit`` items are
        highlighted; the rest stay reachable without shouting.
        """

        now = _utc_now()
        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.question_id, e.result, e.independence,
                       e.hint_used, e.next_review_at, e.review_status,
                       e.created_at, q.subject, substr(q.stem_text, 1, 60) AS stem,
                       f.title AS family_title
                FROM mastery_evidence e
                JOIN question_items q ON q.id = e.question_id
                LEFT JOIN question_families f ON f.id = e.family_id
                WHERE e.review_status != 'done'
                  AND e.result IN ('correct', 'incorrect', 'partial')
                  AND e.next_review_at IS NOT NULL
                  AND e.next_review_at <= ?
                ORDER BY CASE WHEN e.result = 'incorrect' THEN 0 ELSE 1 END,
                         e.hint_used DESC, e.next_review_at, e.id
                """,
                (now,),
            ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            if is_foreign_language_subject(row["subject"]):
                continue
            if len(items) >= limit:
                break
            if str(row["result"]) == "incorrect":
                why = "上次没有做对，今天是最适合再试一次的时间。"
            elif int(row["hint_used"]) or str(row["independence"]) in (
                "light_hint",
                "heavy_hint",
                "after_answer",
            ):
                why = "上次靠提示完成，需要一次无提示的确认。"
            else:
                why = "已经到了适合复习的时间——隔几天再确认一次，才算稳定。"
            items.append(
                {
                    "evidence_id": int(row["id"]),
                    "question_id": int(row["question_id"]),
                    "stem": row["stem"],
                    "family_title": row["family_title"],
                    "last_result": str(row["result"]),
                    "why": why,
                    "next_review_at": row["next_review_at"],
                    "review_status": str(row["review_status"]),
                }
            )
        return items


# --------------------------------------------------------------- output
class CollectionRenderer(Protocol):
    """Output renderer contract: structured assets in, document bytes out."""

    def render(self, collection_id: int) -> bytes:
        """Render one collection; the format is the renderer's identity."""
        ...


class MarkdownCollectionRenderer:
    """Real minimal renderer for the vertical slice (reviewable .md output)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def render(self, collection_id: int) -> bytes:
        service = OutputCollectionService(self._database)
        collection = service.get_collection(collection_id)
        lines = [f"# {collection['title']}", ""]
        if collection["description"]:
            lines += [collection["description"], ""]
        for entry in service.list_collection_items(collection_id):
            lines += self._render_item(entry)
        return "\n".join(lines).encode("utf-8")

    def _render_item(self, entry: dict[str, object]) -> list[str]:
        layer = str(entry["item_layer"])
        item_id = int(entry["item_id"])
        if layer == "question":
            question = QuestionService(self._database).get_question_item(item_id)
            # User-facing output uses Chinese labels (V086-308); the stored
            # enum values are never leaked into rendered documents.
            kind_labels = {
                "error": "错题",
                "good": "好题",
                "typical": "典型题",
                "method": "方法题",
            }
            verdict_labels = {
                None: "未判定",
                "correct": "√ 正确",
                "incorrect": "× 错误",
                "uncertain": "判定不确定",
            }
            confidence_labels = {
                "confirmed": "已确认",
                "probable": "较可能",
                "uncertain": "不确定",
            }
            verdict = verdict_labels.get(question.teacher_verdict, "未判定")
            kind_label = kind_labels.get(question.question_kind, question.question_kind)
            confidence_label = confidence_labels.get(
                question.stem_confidence, question.stem_confidence
            )
            lines = [
                f"## 题目 #{question.id}（{question.question_number or '未编号'}）",
                (
                    f"- 判定：{verdict}；类型：{kind_label}；"
                    f"题干置信度：{confidence_label}"
                ),
            ]
            if not question.source_available:
                snapshot = " ".join(
                    part
                    for part in (
                        question.source_document_title_snapshot,
                        question.source_page_label_snapshot,
                    )
                    if part
                )
                lines.append(
                    "- 来源：原始资料已删除，证据不可用"
                    + (f"（原为：{snapshot}）" if snapshot else "")
                )
            lines += [
                "",
                question.stem_text or "（题干待核对）",
                "",
                f"**我的作答**：{question.student_answer or '（未记录）'}",
                *(
                    [f"**教师批注**：{question.teacher_comment}"]
                    if question.teacher_comment
                    else []
                ),
                *(
                    [f"**订正**：{question.correction_note}"]
                    if question.correction_note
                    else []
                ),
            ]
            return lines
        if layer == "family":
            family = QuestionOrganizationService(self._database).get_family(item_id)
            family_labels = {"type": "题型族", "method": "方法族", "conclusion": "二级结论"}
            label = family_labels[family.family_kind]
            return [
                f"## {label}：{family.title}",
                family.description or "",
                f"- 成员题数：{family.member_count}",
            ]
        if layer == "mastery":
            summary = MasteryService(self._database).question_mastery_summary(item_id)
            return [
                f"## 掌握概览：题目 #{item_id}",
                (
                    f"- 练习 {summary['total']} 次，正确 {summary['correct']} 次；"
                    f"会做：{'是' if summary['can_do'] else '否'}；"
                    f"会讲：{'是' if summary['can_explain'] else '否'}"
                ),
            ]
        return [f"## 未知条目层：{layer} #{item_id}"]


class DocxCollectionRenderer:
    """Word renderer contract stub: interface ready, binary format pending.

    Raises a clear error instead of emitting a fake .docx; the selection and
    data contracts above are the sanctioned integration surface once the
    python-docx dependency is approved.
    """

    def render(self, collection_id: int) -> bytes:
        raise LearningWorkflowError(
            "Word 输出渲染器尚未实现：数据与选择契约已就绪，"
            "当前请使用 MarkdownCollectionRenderer。"
        )


class PlainTextCollectionRenderer:
    """Plain-text booklet renderer for ordinary Windows users (§11).

    Not a mechanical Markdown strip: the booklet keeps a clear hierarchy
    with 【…】 section markers, renders wings and secondary conclusions of
    every question, and is encoded UTF-8 with BOM so double-clicking in
    Windows Notepad opens it correctly.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def render(self, collection_id: int) -> bytes:
        service = OutputCollectionService(self._database)
        collection = service.get_collection(collection_id)
        lines = [str(collection["title"]), ""]
        if collection.get("description"):
            lines += [str(collection["description"]), ""]
        for entry in service.list_collection_items(collection_id):
            lines += self._render_item(entry)
        return "\r\n".join(lines).encode("utf-8-sig")

    def _render_question(self, question: QuestionItem) -> list[str]:
        from src.display_labels import (
            BOUNDARY_COUNTEREXAMPLE_FIELD_LABELS,
            METHOD_TRIGGER_FIELD_LABELS,
            VERDICT_LABELS,
            question_display_title,
        )

        verdict = VERDICT_LABELS.get(
            question.teacher_verdict or "none", "（未判定）"
        )
        lines = [
            "＝" * 24,
            f"【题目】{question_display_title(question)}",
            question.stem_text or "（题干待补充）",
            "",
            f"【我的作答】{question.student_answer or '（未记录）'}",
            f"【判定】{verdict}",
        ]
        if question.correction_note:
            lines.append(f"【订正】{question.correction_note}")
        if question.reason_tags:
            lines.append(f"【错因】{'、'.join(question.reason_tags)}")
        if question.method_tags:
            lines.append(f"【方法】{'、'.join(question.method_tags)}")
        wings = TwoWingsService(self._database)
        trigger_lines: list[str] = []
        boundary_lines: list[str] = []
        for kind in ("method_trigger", "boundary_counterexample"):
            label_map = (
                METHOD_TRIGGER_FIELD_LABELS
                if kind == "method_trigger"
                else BOUNDARY_COUNTEREXAMPLE_FIELD_LABELS
            )
            for wing_entry in wings.list_entries_for_question(question.id, wing_kind=kind):
                field_lines: list[str] = []
                for key, value in wing_entry.fields.items():
                    text = self._field_text(value)
                    if text:
                        field_lines.append(f"  {label_map.get(key, key)}：{text}")
                if not field_lines:
                    continue
                if kind == "method_trigger":
                    trigger_lines.append("  · 方法触发")
                    trigger_lines.extend(field_lines)
                else:
                    boundary_lines.append("  · 边界反例")
                    boundary_lines.extend(field_lines)
        if trigger_lines:
            lines.append("【方法触发】")
            lines.extend(trigger_lines)
        if boundary_lines:
            lines.append("【边界反例】")
            lines.extend(boundary_lines)
        organization = QuestionOrganizationService(self._database)
        for family in organization.list_families_for_question(question.id):
            if family.family_kind == "conclusion":
                lines.append(f"【二级结论】{family.title}")
                if family.description:
                    lines.append(f"  {family.description}")
                if family.derivation:
                    lines.append(f"  常见推导方法：{family.derivation}")
        if not question.source_available:
            lines.append("原始来源已删除，当前保留的是学习整理内容。")
        lines.append("")
        return lines

    def _field_text(self, value: object) -> str:
        if isinstance(value, list):
            return "；".join(str(item) for item in value if str(item).strip())
        return str(value).strip() if value is not None else ""

    def _render_item(self, entry: dict[str, object]) -> list[str]:
        layer = str(entry["item_layer"])
        item_id = int(entry["item_id"])
        if layer == "question":
            question = QuestionService(self._database).get_question_item(item_id)
            return self._render_question(question)
        if layer == "family":
            family = QuestionOrganizationService(self._database).get_family(item_id)
            kind_labels = {"type": "题型族", "method": "方法族", "conclusion": "二级结论"}
            lines = [
                "＝" * 24,
                f"【{kind_labels.get(family.family_kind, family.family_kind)}】{family.title}",
            ]
            if family.description:
                lines.append(family.description)
            if family.derivation:
                lines.append(f"常见推导方法：{family.derivation}")
            lines.append(f"成员题数：{family.member_count}")
            lines.append("")
            return lines
        if layer == "mastery":
            from src.display_labels import OUTCOME_LABELS

            summary = MasteryService(self._database).question_mastery_summary(item_id)
            outcome_label = OUTCOME_LABELS.get(str(summary.get("outcome")), "")
            return [
                "＝" * 24,
                f"【掌握概览】练习 {summary['total']} 次，做对 {summary['correct']} 次",
                f"会做：{'是' if summary['can_do'] else '否'}；"
                f"会讲：{'是' if summary['can_explain'] else '否'}"
                + (f"（最近一次：{outcome_label}）" if outcome_label else ""),
                "",
            ]
        return [f"【未知条目】{layer}", ""]


class OutputCollectionService:
    """Collections of structured learning assets with a selection contract."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_collection(
        self, *, kind: str, title: str, description: str = ""
    ) -> int:
        _require(
            kind in _COLLECTION_KINDS,
            "册子类型不在支持的输出类型清单内。",
        )
        _require(bool(title.strip()), "册子标题不能为空。")
        timestamp = _utc_now()
        with self._database._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO output_collections(
                    kind, title, description, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (kind, title.strip(), description, timestamp, timestamp),
            )
            return int(cursor.lastrowid)

    def add_collection_item(
        self,
        collection_id: int,
        *,
        item_layer: Literal["question", "family", "mastery"],
        item_id: int,
        range_spec: dict | None = None,
        position: int | None = None,
    ) -> int:
        """Append one item; ``range_spec`` is the selection/range contract."""

        _require(
            item_layer in ("question", "family", "mastery"),
            "条目层必须是 question/family/mastery。",
        )
        if item_layer == "question":
            QuestionService(self._database).get_question_item(item_id)
        elif item_layer == "family":
            QuestionOrganizationService(self._database).get_family(item_id)
        else:
            with self._database._connection() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM question_items WHERE id = ?", (item_id,)
                ).fetchone()
            _require(exists is not None, f"整理题目不存在：{item_id}")
        with self._database._connection() as connection:
            if position is None:
                row = connection.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM output_collection_items "
                    "WHERE collection_id = ?",
                    (collection_id,),
                ).fetchone()
                position = int(row[0])
            cursor = connection.execute(
                """
                INSERT INTO output_collection_items(
                    collection_id, item_layer, item_id, range_spec, position, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    collection_id,
                    item_layer,
                    item_id,
                    json.dumps(range_spec or {}, ensure_ascii=False),
                    position,
                    _utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def get_collection(self, collection_id: int) -> dict[str, object]:
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT * FROM output_collections WHERE id = ?", (collection_id,)
            ).fetchone()
        _require_collection(row is not None, collection_id)
        return dict(row)

    def list_collection_items(self, collection_id: int) -> list[dict[str, object]]:
        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM output_collection_items WHERE collection_id = ? "
                "ORDER BY position, id",
                (collection_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_collections(self) -> list[dict[str, object]]:
        """List output collections (newest first) with item counts."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT c.*, (SELECT COUNT(*) FROM output_collection_items i
                    WHERE i.collection_id = c.id) AS item_count
                FROM output_collections c ORDER BY c.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]


def _require_collection(condition: bool, collection_id: int) -> None:
    if not condition:
        raise LearningWorkflowError(f"导出册子不存在：{collection_id}")


# --------------------------------------------------------------- two wings
WING_KINDS: Final = ("method_trigger", "boundary_counterexample")

# User-ratified two-wings definitions (v0.8.6 RUN 2, docs/V086_SCOPE_FREEZE.md):
# the Method Trigger Wing (left) trains "see these conditions -> recall this
# method -> why -> how it lands on the current question"; the Boundary &
# Counterexample Wing (right) trains "when does this NOT apply -> missing
# conditions -> common misuses -> counterexamples -> what changes if the
# conditions change".  Both are horizontal attachments to question items or
# question families - never extra layers - and both are learning assets with
# revision history, not UI decoration.
METHOD_TRIGGER_FIELDS: Final = (
    "trigger_conditions",
    "recognition_signals",
    "candidate_method",
    "selection_reason",
    "applicability_prerequisites",
    "application_to_current_question",
    "similar_method_distinction",
)
BOUNDARY_COUNTEREXAMPLE_FIELDS: Final = (
    "validity_conditions",
    "invalidation_conditions",
    "boundary_cases",
    "counterexamples",
    "common_misuses",
    "confusing_conclusions",
    "condition_change_effect",
)
WING_LIST_FIELDS: Final = frozenset(
    {
        "trigger_conditions",
        "recognition_signals",
        "applicability_prerequisites",
        "validity_conditions",
        "invalidation_conditions",
        "boundary_cases",
        "counterexamples",
        "common_misuses",
        "confusing_conclusions",
    }
)


@dataclass(frozen=True, slots=True)
class WingEntry:
    """One attachment of a wing to a question or a family."""

    id: int
    wing_kind: str
    target_layer: str
    target_id: int
    fields: dict[str, object]
    origin: str
    confidence: str
    status: str
    evidence_item_id: int | None
    region_json: dict | None


class TwoWingsService:
    """Structured two-wings layer with revision and evidence discipline.

    Generation discipline (user-ratified): AI writes drafts, users revise,
    every change lands in ``wing_entry_revisions``; AI output never poses as
    user-confirmed knowledge (``origin`` + ``confidence`` are explicit), and
    a question may carry only the left wing, only the right wing, both, or
    none - wings stay optional by design.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_entry(
        self,
        *,
        wing_kind: str,
        target_layer: str,
        question_id: int | None = None,
        family_id: int | None = None,
        origin: str = "user",
        confidence: str = "probable",
        **fields: object,
    ) -> int:
        """Create one wing entry; ``fields`` carries the wing-specific content."""

        _require(
            wing_kind in WING_KINDS,
            "翼类型必须是 method_trigger 或 boundary_counterexample。",
        )
        _require(target_layer in ("question", "family"), "挂接目标必须是 question 或 family。")
        _require(origin in ("user", "ai_draft"), "来源必须是 user 或 ai_draft。")
        _require(
            confidence in ("confirmed", "probable", "uncertain"),
            "置信度必须是 confirmed/probable/uncertain。",
        )
        allowed = (
            METHOD_TRIGGER_FIELDS
            if wing_kind == "method_trigger"
            else BOUNDARY_COUNTEREXAMPLE_FIELDS
        )
        known = {name: value for name, value in fields.items() if name in allowed}
        if wing_kind == "method_trigger":
            _require(
                bool(known.get("candidate_method")),
                "方法触发翼必须给出 candidate_method。",
            )
        else:
            _require(
                bool(known.get("validity_conditions")),
                "边界反例翼必须给出 validity_conditions（哪怕只有一条待确认条件）。",
            )
        timestamp = _utc_now()
        with self._database._connection() as connection:
            resolved_question, resolved_family = self._resolve_target(
                connection, target_layer, question_id, family_id
            )
            columns = ["wing_kind", "target_layer", "question_id", "family_id"]
            values: list[object] = [
                wing_kind,
                target_layer,
                resolved_question,
                resolved_family,
            ]
            for name in allowed:
                columns.append(name)
                raw = known.get(name)
                if name in WING_LIST_FIELDS:
                    encoded = _dump_tags(
                        [str(item) for item in raw] if isinstance(raw, list) else []
                    )
                else:
                    encoded = str(raw) if raw is not None else ""
                values.append(encoded)
            columns += ["origin", "confidence", "status", "created_at", "updated_at"]
            values += [origin, confidence, "draft", timestamp, timestamp]
            cursor = connection.execute(
                "INSERT INTO wing_entries("
                + ", ".join(columns)
                + ") VALUES ("
                + ", ".join("?" for _ in columns)
                + ")",
                values,
            )
            entry_id = int(cursor.lastrowid)
        self._record_revision(
            entry_id,
            revision_kind="ai_draft" if origin == "ai_draft" else "user_revision",
        )
        return entry_id

    def update_entry(self, entry_id: int, *, note: str = "", **fields: object) -> WingEntry:
        """Apply a user revision; the pre-change snapshot lands in the history."""

        existing = self.get_entry(entry_id)
        allowed = (
            METHOD_TRIGGER_FIELDS
            if existing.wing_kind == "method_trigger"
            else BOUNDARY_COUNTEREXAMPLE_FIELDS
        )
        updates = {name: value for name, value in fields.items() if name in allowed}
        if not updates:
            raise LearningWorkflowError("修订内容为空：至少提供一个翼字段。")
        timestamp = _utc_now()
        with self._database._connection() as connection:
            assignments = []
            values: list[object] = []
            for name, value in updates.items():
                assignments.append(name + " = ?")
                encoded = (
                    _dump_tags([str(item) for item in value])
                    if name in WING_LIST_FIELDS and isinstance(value, list)
                    else str(value)
                )
                values.append(encoded)
            assignments += ["origin = 'user'", "status = 'confirmed'", "updated_at = ?"]
            values += [timestamp, entry_id]
            connection.execute(
                "UPDATE wing_entries SET "
                + ", ".join(assignments)
                + " WHERE id = ?",
                values,
            )
        snapshot = {"fields": dict(existing.fields), "status": existing.status}
        self._record_revision(
            entry_id, revision_kind="user_revision", note=note, snapshot=snapshot
        )
        return self.get_entry(entry_id)

    def confirm_entry(self, entry_id: int, *, note: str = "") -> WingEntry:
        """User confirms an entry (typically after reviewing an AI draft)."""

        with self._database._connection() as connection:
            connection.execute(
                "UPDATE wing_entries SET status = 'confirmed', updated_at = ? WHERE id = ?",
                (_utc_now(), entry_id),
            )
        self._record_revision(entry_id, revision_kind="confirmed", note=note)
        return self.get_entry(entry_id)

    def get_entry(self, entry_id: int) -> WingEntry:
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT * FROM wing_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        if row is None:
            raise LearningWorkflowError(f"翼条目不存在：{entry_id}")
        return self._from_row(row)

    def list_entries_for_question(
        self, question_id: int, *, wing_kind: str | None = None
    ) -> list[WingEntry]:
        return self._list("question", question_id, wing_kind)

    def list_entries_for_family(
        self, family_id: int, *, wing_kind: str | None = None
    ) -> list[WingEntry]:
        return self._list("family", family_id, wing_kind)

    def attach_evidence(
        self,
        entry_id: int,
        *,
        evidence_item_id: int | None,
        region_json: dict | None = None,
    ) -> None:
        """Bind page evidence to a wing entry (same contract as questions)."""

        _require(evidence_item_id is not None or region_json, "必须提供证据项或页面区域。")
        with self._database._connection() as connection:
            if evidence_item_id is not None:
                exists = connection.execute(
                    "SELECT 1 FROM evidence_items WHERE id = ?", (evidence_item_id,)
                ).fetchone()
                _require(exists is not None, f"证据项不存在：{evidence_item_id}")
            connection.execute(
                """
                UPDATE wing_entries SET
                    evidence_item_id = ?, region_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    evidence_item_id,
                    json.dumps(region_json, ensure_ascii=False) if region_json else None,
                    _utc_now(),
                    entry_id,
                ),
            )

    def entry_history(self, entry_id: int) -> list[dict[str, object]]:
        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT revision_kind, note, snapshot, created_at
                FROM wing_entry_revisions WHERE wing_entry_id = ? ORDER BY id
                """,
                (entry_id,),
            ).fetchall()
        history = []
        for row in rows:
            try:
                snapshot = json.loads(str(row["snapshot"]))
            except json.JSONDecodeError:
                snapshot = {}
            history.append(
                {
                    "revision_kind": str(row["revision_kind"]),
                    "note": str(row["note"]),
                    "snapshot": snapshot,
                    "created_at": str(row["created_at"]),
                }
            )
        return history

    # ------------------------------------------------------------ internals
    def _resolve_target(
        self,
        connection,
        target_layer: str,
        question_id: int | None,
        family_id: int | None,
    ) -> tuple[int | None, int | None]:
        if target_layer == "question":
            _require(question_id is not None, "question 目标必须提供 question_id。")
            row = connection.execute(
                "SELECT id, subject FROM question_items WHERE id = ?", (question_id,)
            ).fetchone()
            _require(row is not None, f"整理题目不存在：{question_id}")
            _require(not is_foreign_language_subject(row["subject"]),
                     "外语学科只做第一层整理，不进入两翼。")
            return question_id, None
        _require(family_id is not None, "family 目标必须提供 family_id。")
        row = connection.execute(
            "SELECT id FROM question_families WHERE id = ?", (family_id,)
        ).fetchone()
        _require(row is not None, f"题型族不存在：{family_id}")
        return None, family_id

    def _record_revision(
        self,
        entry_id: int,
        *,
        revision_kind: str,
        note: str = "",
        snapshot: dict | None = None,
    ) -> None:
        entry = self.get_entry(entry_id)
        payload = snapshot or {"fields": dict(entry.fields)}
        with self._database._connection() as connection:
            connection.execute(
                """
                INSERT INTO wing_entry_revisions(
                    wing_entry_id, revision_kind, note, snapshot, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    revision_kind,
                    note,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    _utc_now(),
                ),
            )

    def _list(
        self, target_layer: str, target_id: int, wing_kind: str | None
    ) -> list[WingEntry]:
        column = "question_id" if target_layer == "question" else "family_id"
        query = "SELECT * FROM wing_entries WHERE " + column + " = ?"
        parameters: list[object] = [target_id]
        if wing_kind is not None:
            query += " AND wing_kind = ?"
            parameters.append(wing_kind)
        with self._database._connection() as connection:
            rows = connection.execute(query + " ORDER BY id", parameters).fetchall()
        return [self._from_row(row) for row in rows]

    def _from_row(self, row) -> WingEntry:
        fields: dict[str, object] = {}
        for name in (*METHOD_TRIGGER_FIELDS, *BOUNDARY_COUNTEREXAMPLE_FIELDS):
            if name in row.keys():
                raw = row[name]
                if name in WING_LIST_FIELDS:
                    try:
                        value = json.loads(str(raw))
                    except json.JSONDecodeError:
                        value = []
                    fields[name] = value if isinstance(value, list) else []
                else:
                    fields[name] = str(raw)
        region = None
        if row["region_json"]:
            try:
                region = json.loads(str(row["region_json"]))
            except json.JSONDecodeError:
                region = None
        return WingEntry(
            id=int(row["id"]),
            wing_kind=str(row["wing_kind"]),
            target_layer=str(row["target_layer"]),
            target_id=int(row["question_id"] or row["family_id"]),
            fields=fields,
            origin=str(row["origin"]),
            confidence=str(row["confidence"]),
            status=str(row["status"]),
            evidence_item_id=(
                int(row["evidence_item_id"])
                if row["evidence_item_id"] is not None
                else None
            ),
            region_json=region,
        )


def render_collection_to_file(
    database: Database,
    collection_id: int,
    destination: Path,
    *,
    renderer: CollectionRenderer | None = None,
) -> Path:
    """Render one collection through the renderer contract into ``destination``."""

    chosen = renderer or MarkdownCollectionRenderer(database)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(chosen.render(collection_id))
    return destination
