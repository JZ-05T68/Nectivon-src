"""Per-question candidate cards for multi-question pages (fix round §37-42).

A photographed textbook/exam page very often carries several questions
(2-15/2-16/2-17 on one page).  The old "加入学习整理" entry could only add
the WHOLE page as one undifferentiated blob, which human review judged a
UX failure.  This module provides:

1. ``QuestionCandidateStore`` — filesystem persistence for the per-page
   candidate list (``data/question-candidates/page_<id>.json``), so one
   AI extraction survives navigation and is never silently re-billed.
2. ``build_extraction_prompt`` / ``parse_candidates_payload`` — one AI
   call splits the page into per-question candidates with honest
   completeness (an incomplete stem stays incomplete, §42), excluding the
   sliver of an adjacent page that often appears in photos (§34/§42).
3. Candidate lifecycle: ``pending`` → ``added``（已加入学习整理）or
   ``ignored``（暂不整理/忽略）.  Ignoring or deleting a candidate only
   ever removes the AI-derived candidate record — source images, OCR
   text and page data are never touched (§40).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger(__name__)

CANDIDATE_FORMAT_VERSION = 2
_COMPLETENESS_LEVELS = ("complete", "incomplete")
_CANDIDATE_STATUSES = ("pending", "added", "ignored", "skipped")
# Geography G2-A: the old single completeness axis conflated "the TEXT is
# missing words/options" with "the TEXT is fine but the question needs the
# map/figure".  These are different states with different user actions, so
# the semantics are split into three explicit dimensions (low-risk: no
# schema migration — candidate JSON only).
_VISUAL_DEPENDENCIES = ("none", "required", "uncertain")
_QUESTION_KINDS = ("atomic", "composite")
_SPLIT_SOURCES = ("explicit_numbering", "layout", "ai_inference", "manual")
_SPLIT_CONFIDENCE = ("high", "medium", "low")


class QuestionCandidateError(RuntimeError):
    """Candidate extraction or persistence was refused."""


@dataclass(slots=True)
class QuestionCandidate:
    """One extracted question candidate on a page."""

    number: str
    stem: str
    completeness: str  # TEXT completeness: complete | incomplete
    incomplete_reason: str = ""
    figure_refs: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | added | ignored
    extracted_at: str = ""
    user_edited: bool = False  # 人工补录或修订过的候选（provenance 诚实标注）
    # Geography G2-A three-dimension semantics:
    visual_dependency: str = "uncertain"  # none | required | uncertain
    visual_notes: str = ""  # 共享材料说明（图名/题组范围），供 UI 展示与绑定
    binding_confirmed: bool = False  # USER CONFIRMED BINDING（未确认=AI draft）
    question_kind: str = "atomic"  # atomic | composite
    children: list[QuestionCandidate] = field(default_factory=list)
    shared_context_refs: list[str] = field(default_factory=list)
    page_refs: list[int] = field(default_factory=list)
    image_refs: list[str] = field(default_factory=list)
    answer_refs: list[str] = field(default_factory=list)
    split_source: str = "ai_inference"
    split_confidence: str = "low"

    def validate(self) -> None:
        if not self.number.strip() and not self.stem.strip():
            raise QuestionCandidateError("候选题缺少题号和题干。")
        if self.completeness not in _COMPLETENESS_LEVELS:
            raise QuestionCandidateError("完整性必须是 complete/incomplete。")
        if self.status not in _CANDIDATE_STATUSES:
            raise QuestionCandidateError("候选状态必须是 pending/added/ignored/skipped。")
        if self.visual_dependency not in _VISUAL_DEPENDENCIES:
            raise QuestionCandidateError("视觉依赖必须是 none/required/uncertain。")
        if self.question_kind not in _QUESTION_KINDS:
            raise QuestionCandidateError("题目粒度必须是 atomic/composite。")
        if self.split_source not in _SPLIT_SOURCES:
            raise QuestionCandidateError("拆分来源无效。")
        if self.split_confidence not in _SPLIT_CONFIDENCE:
            raise QuestionCandidateError("拆分置信度无效。")
        if self.children and self.question_kind != "composite":
            raise QuestionCandidateError("含子题的候选必须标记为 composite。")
        if not self.children and self.question_kind != "atomic":
            raise QuestionCandidateError("无子题的叶子必须标记为 atomic。")
        for child in self.children:
            child.validate()

    @property
    def needs_visual(self) -> bool:
        return self.visual_dependency in ("required", "uncertain")

    @property
    def is_leaf(self) -> bool:
        return not self.children


def _fix_legacy_visual_misjudgement(candidate: QuestionCandidate) -> None:
    """Repair the G1-GEO-02 misjudgement on stored candidates.

    G1's splitter wrote 「题干依赖的地图（…）未完整提供」 as
    ``completeness=incomplete`` even when the map was ON THE PAGE — that is
    a visual dependency, not missing text (the stem/options are verbatim).
    Reading such a record now reclassifies it honestly: TEXT complete (the
    extracted text was fine), VISUAL required, and the material description
    moves into ``visual_notes`` instead of scaring the user about a missing
    page.  AI binding stays unconfirmed (needs review until the user acts).
    """

    reason = candidate.incomplete_reason or ""
    if candidate.completeness != "incomplete":
        return
    # 「题干依赖的地图/图表（…）…」 wording is inherently a dependency
    # declaration — never a description of missing TEXT — regardless of how
    # the sentence tail was truncated by the model.  （Note: 「依赖的地图」
    # does NOT contain「依赖的图」as a substring — check both explicitly.)
    if (
        "依赖的地图" in reason
        or "依赖的图表" in reason
        or "依赖的图" in reason
    ):
        import re as _re

        match = _re.search(r"[（(]([^（）()]+)[）)]", reason)
        material = match.group(1).strip() if match else ""
        candidate.completeness = "complete"
        candidate.incomplete_reason = ""
        candidate.visual_dependency = "required"
        candidate.visual_notes = (
            f"共享材料：{material}" if material else "需结合本页图表作答"
        )
        candidate.binding_confirmed = False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class QuestionCandidateStore:
    """Atomic filesystem persistence for per-page candidate lists."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def page_candidates(self, page_id: int) -> list[QuestionCandidate] | None:
        """Return the stored candidate list, or None when never extracted."""

        payload = self._read_json(self._page_path(page_id))
        if payload is None:
            return None
        try:
            candidates = _candidates_from_payload(payload, page_id)
        except (KeyError, TypeError, ValueError):
            LOGGER.warning("忽略损坏的题目候选记录：page_id=%s", page_id)
            return None
        for candidate in iter_question_nodes(candidates):
            _fix_legacy_visual_misjudgement(candidate)
        return candidates

    def save_page_candidates(self, page_id: int, candidates: list[QuestionCandidate]) -> None:
        for candidate in candidates:
            candidate.validate()
        payload = {
            "format_version": CANDIDATE_FORMAT_VERSION,
            "page_id": page_id,
            "extracted_at": _utc_now(),
            "candidates": [asdict(c) for c in candidates],
        }
        self._write_json(self._page_path(page_id), payload)

    def mark_status(self, page_id: int, number: str, status: str) -> list[QuestionCandidate]:
        if status not in _CANDIDATE_STATUSES:
            raise QuestionCandidateError("候选状态必须是 pending/added/ignored/skipped。")
        candidates = self.page_candidates(page_id)
        if candidates is None:
            raise QuestionCandidateError("这一页还没有题目候选记录。")
        hit = False
        for candidate in iter_question_nodes(candidates):
            if candidate.number == number:
                candidate.status = status
                hit = True
        if not hit:
            raise QuestionCandidateError(f"找不到候选题：{number}")
        self.save_page_candidates(page_id, candidates)
        return candidates

    def mark_status_at_path(
        self, page_id: int, node_path: str, status: str
    ) -> list[QuestionCandidate]:
        """Update one exact candidate node selected by its page-level path.

        Printed sub-question labels such as ``(2)`` may legitimately repeat
        under different root questions on the same page.  UI mutations must
        therefore use the structural path instead of matching every node with
        the same displayed number.
        """

        if status not in _CANDIDATE_STATUSES:
            raise QuestionCandidateError("候选状态必须是 pending/added/ignored/skipped。")
        candidates = self.page_candidates(page_id)
        if candidates is None:
            raise QuestionCandidateError("这一页还没有题目候选记录。")
        _node_at_path(candidates, node_path).status = status
        self.save_page_candidates(page_id, candidates)
        return candidates

    def split_leaf(
        self,
        page_id: int,
        node_path: str,
        children: list[QuestionCandidate],
        *,
        shared_stem: str | None = None,
    ) -> list[QuestionCandidate]:
        """Manually turn one atomic draft into a composite subtree.

        ``node_path`` is the page-level root/child index path (for example
        ``"1.0"`` means the first child of the second root).  Already joined
        candidates are refused so a correction cannot silently invalidate a
        linked learning item.  Callers may then present the new leaves for
        independent review and joining.
        """

        candidates = self.page_candidates(page_id)
        if candidates is None:
            raise QuestionCandidateError("这一页还没有题目候选记录。")
        node = _node_at_path(candidates, node_path)
        if node.children:
            raise QuestionCandidateError("该候选已经是综合题；请编辑现有子题。")
        if node.status == "added":
            raise QuestionCandidateError("已加入学习整理的题目不能直接改拆分结构。")
        if not children:
            raise QuestionCandidateError("漏拆修正至少需要一个子题。")
        for child in children:
            child.validate()
            if not child.extracted_at:
                child.extracted_at = _utc_now()
            child.user_edited = True
            child.split_source = "manual"
        if shared_stem is not None:
            node.stem = str(shared_stem).strip()
        node.children = children
        node.question_kind = "composite"
        node.user_edited = True
        node.split_source = "manual"
        node.split_confidence = "high"
        self.save_page_candidates(page_id, candidates)
        return candidates

    def merge_subtree(
        self,
        page_id: int,
        node_path: str,
        *,
        merged_stem: str | None = None,
    ) -> list[QuestionCandidate]:
        """Manually collapse a wrongly split composite into one atomic draft.

        Descendant wording is never concatenated implicitly: the caller may
        provide the faithful merged stem, otherwise the existing parent stem
        is retained.  A subtree containing an already joined leaf is refused
        for the same referential-integrity reason as :meth:`split_leaf`.
        """

        candidates = self.page_candidates(page_id)
        if candidates is None:
            raise QuestionCandidateError("这一页还没有题目候选记录。")
        node = _node_at_path(candidates, node_path)
        if not node.children:
            raise QuestionCandidateError("该候选已经是 atomic，无需合并。")
        if any(descendant.status == "added" for descendant in iter_question_nodes(node.children)):
            raise QuestionCandidateError("含已加入学习整理的子题，不能直接合并结构。")
        if merged_stem is not None:
            node.stem = str(merged_stem).strip()
        node.children = []
        node.question_kind = "atomic"
        node.user_edited = True
        node.split_source = "manual"
        node.split_confidence = "high"
        self.save_page_candidates(page_id, candidates)
        return candidates

    def update_candidate(
        self,
        page_id: int,
        number: str,
        *,
        new_number: str | None = None,
        stem: str | None = None,
        figure_refs: list[str] | None = None,
    ) -> list[QuestionCandidate]:
        """Apply one user edit to a candidate (题号/题干/图像绑定).

        The candidate keeps its lifecycle status; an edit never silently
        flips ``added`` back to ``pending``.  The result is honestly marked
        ``user_edited`` so provenance never presents user wording as AI
        extraction.
        """

        candidates = self.page_candidates(page_id)
        if candidates is None:
            raise QuestionCandidateError("这一页还没有题目候选记录。")
        hit = False
        for candidate in iter_question_nodes(candidates):
            if candidate.number == number:
                if new_number is not None:
                    candidate.number = str(new_number).strip()
                if stem is not None:
                    candidate.stem = str(stem).strip()
                if figure_refs is not None:
                    candidate.figure_refs = [
                        str(f).strip() for f in figure_refs if str(f).strip()
                    ]
                candidate.user_edited = True
                hit = True
        if not hit:
            raise QuestionCandidateError(f"找不到候选题：{number}")
        for candidate in candidates:
            candidate.validate()
        self.save_page_candidates(page_id, candidates)
        return candidates

    def update_candidate_at_path(
        self,
        page_id: int,
        node_path: str,
        *,
        new_number: str | None = None,
        stem: str | None = None,
        figure_refs: list[str] | None = None,
    ) -> list[QuestionCandidate]:
        """Apply a user edit to exactly one page-level candidate node."""

        candidates = self.page_candidates(page_id)
        if candidates is None:
            raise QuestionCandidateError("这一页还没有题目候选记录。")
        candidate = _node_at_path(candidates, node_path)
        if new_number is not None:
            candidate.number = str(new_number).strip()
        if stem is not None:
            candidate.stem = str(stem).strip()
        if figure_refs is not None:
            candidate.figure_refs = [
                str(figure).strip()
                for figure in figure_refs
                if str(figure).strip()
            ]
        candidate.user_edited = True
        for root in candidates:
            root.validate()
        self.save_page_candidates(page_id, candidates)
        return candidates

    def confirm_visual_binding(self, page_id: int, number: str) -> list[QuestionCandidate]:
        """Record USER CONFIRMED BINDING for one candidate's visual material.

        AI-suggested figure/question association stays a draft until this is
        called; the flag is honestly separate from ``user_edited`` (content
        edits) so provenance can tell「AI 认为这张图属于这题」apart from
        「用户确认过」.
        """

        candidates = self.page_candidates(page_id)
        if candidates is None:
            raise QuestionCandidateError("这一页还没有题目候选记录。")
        hit = False
        for candidate in iter_question_nodes(candidates):
            if candidate.number == number:
                candidate.binding_confirmed = True
                if candidate.visual_dependency == "uncertain":
                    candidate.visual_dependency = "required"
                hit = True
        if not hit:
            raise QuestionCandidateError(f"找不到候选题：{number}")
        self.save_page_candidates(page_id, candidates)
        return candidates

    def confirm_visual_binding_at_path(
        self, page_id: int, node_path: str
    ) -> list[QuestionCandidate]:
        """Confirm visual binding for one exact page-level candidate node."""

        candidates = self.page_candidates(page_id)
        if candidates is None:
            raise QuestionCandidateError("这一页还没有题目候选记录。")
        candidate = _node_at_path(candidates, node_path)
        candidate.binding_confirmed = True
        if candidate.visual_dependency == "uncertain":
            candidate.visual_dependency = "required"
        self.save_page_candidates(page_id, candidates)
        return candidates

    def add_manual_candidate(
        self,
        page_id: int,
        number: str,
        stem: str,
        figure_refs: list[str] | None = None,
    ) -> QuestionCandidate:
        """Add a question the AI missed, explicitly as user-provided content."""

        candidates = self.page_candidates(page_id) or []
        trimmed_number = str(number).strip()
        trimmed_stem = str(stem).strip()
        if trimmed_number and any(
            c.number == trimmed_number for c in iter_question_nodes(candidates)
        ):
            raise QuestionCandidateError(f"已存在同题号候选：{trimmed_number}")
        if not trimmed_number and any(
            not c.number and c.stem == trimmed_stem for c in candidates
        ):
            raise QuestionCandidateError("已存在同内容的无编号候选。")
        candidate = QuestionCandidate(
            number=trimmed_number,
            stem=trimmed_stem,
            completeness="complete" if trimmed_stem else "incomplete",
            incomplete_reason="" if trimmed_stem else "（人工补录，题干待补）",
            figure_refs=[str(f).strip() for f in (figure_refs or []) if str(f).strip()],
            status="pending",
            extracted_at=_utc_now(),
            user_edited=True,
        )
        candidate.validate()
        candidates.append(candidate)
        self.save_page_candidates(page_id, candidates)
        return candidate

    def _page_path(self, page_id: int) -> Path:
        return self.root / f"page_{page_id}.json"

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _candidates_from_payload(payload: dict, page_id: int) -> list[QuestionCandidate]:
    version = int(payload.get("format_version", 0))
    if version not in (1, CANDIDATE_FORMAT_VERSION):
        raise ValueError("format_version")
    if int(payload.get("page_id", -1)) != page_id:
        raise ValueError("page_id")
    result = []
    for item in payload.get("candidates", []):
        if isinstance(item, dict):
            result.append(_candidate_from_dict(item, legacy=version == 1))
    _qualify_subquestion_numbers(result)
    return result


def _candidate_from_dict(item: dict, *, legacy: bool = False) -> QuestionCandidate:
    children = [
        _candidate_from_dict(child, legacy=legacy)
        for child in item.get("children", [])
        if isinstance(child, dict)
    ]
    candidate = QuestionCandidate(
        number=str(item.get("number", "")),
        stem=str(item.get("stem", "")),
        completeness=str(item.get("completeness", "incomplete")),
        incomplete_reason=str(item.get("incomplete_reason", "")),
        figure_refs=[str(f) for f in item.get("figure_refs", [])],
        status=str(item.get("status", "pending")),
        extracted_at=str(item.get("extracted_at", "")),
        user_edited=bool(item.get("user_edited", False)),
        visual_dependency=str(item.get("visual_dependency", "uncertain")),
        visual_notes=str(item.get("visual_notes", "")),
        binding_confirmed=bool(item.get("binding_confirmed", False)),
        question_kind="composite" if children else "atomic",
        children=children,
        shared_context_refs=[
            str(value) for value in item.get("shared_context_refs", [])
        ],
        page_refs=[
            int(value) for value in item.get("page_refs", [])
            if isinstance(value, int)
        ],
        image_refs=[str(value) for value in item.get("image_refs", [])],
        answer_refs=[str(value) for value in item.get("answer_refs", [])],
        split_source=str(
            item.get("split_source", "manual" if legacy else "ai_inference")
        ),
        split_confidence=str(item.get("split_confidence", "low")),
    )
    candidate.validate()
    return candidate


def iter_question_nodes(candidates: list[QuestionCandidate]):
    """Depth-first iteration over every candidate node."""

    for candidate in candidates:
        yield candidate
        yield from iter_question_nodes(candidate.children)


_BARE_SUBQUESTION_NUMBER = re.compile(
    r"^[（(]\s*([0-9一二三四五六七八九十]+)\s*[）)]$"
)


def _qualify_subquestion_numbers(candidates: list[QuestionCandidate]) -> None:
    """Prefix a bare child label with its printed parent question number.

    Extractors often return a root ``20`` with children ``(1)`` ... ``(4)``.
    Bare labels are ambiguous across large questions, so the UI and any new
    learning item use ``20(1)`` ... ``20(4)``.  Already-qualified/manual
    labels are preserved.
    """

    def walk(node: QuestionCandidate, parent_number: str) -> None:
        match = _BARE_SUBQUESTION_NUMBER.fullmatch(node.number.strip())
        if parent_number and match:
            node.number = f"{parent_number}({match.group(1)})"
        for child in node.children:
            walk(child, node.number.strip())

    for root in candidates:
        walk(root, "")


def iter_atomic_leaves(candidates: list[QuestionCandidate]):
    """Yield ``(root, leaf, path, ancestors)`` for joinable leaves."""

    def walk(root, node, path: str, ancestors: tuple[QuestionCandidate, ...]):
        if not node.children:
            yield root, node, path, ancestors
            return
        for index, child in enumerate(node.children):
            yield from walk(root, child, f"{path}.{index}", (*ancestors, node))

    for root_index, root in enumerate(candidates):
        # Paths are page-level so repeated child labels under different roots
        # (for example both question 20 and 21 having a ``(2)``) remain
        # independently addressable for edits and lifecycle changes.
        yield from walk(root, root, str(root_index), ())


_ROOT_QUESTION_NUMBER = re.compile(r"(?:第\s*)?(\d{1,3})")


def associate_companion_answer_refs(
    database: object,
    *,
    source_document_id: int,
    candidates: list[QuestionCandidate],
) -> list[QuestionCandidate]:
    """Attach the matching companion-answer page to each numbered root tree.

    This is a conservative page-level association, not answer transcription:
    only a separate document whose title is the source title plus
    ``答案/参考答案/评分标准`` qualifies, answer sheets are excluded,
    and the selected page must visibly start the same root question number.
    Descendants inherit that one page reference dynamically; scoring bullets
    on the answer page are never turned into question children.
    """

    get_document = getattr(database, "get_document", None)
    list_documents = getattr(database, "list_documents", None)
    list_pages = getattr(database, "list_pages", None)
    if not callable(get_document) or not callable(list_documents) or not callable(list_pages):
        return candidates
    source = get_document(source_document_id)
    if source is None:
        return candidates
    source_title = str(getattr(source, "title", "") or "").strip()
    if not source_title:
        return candidates
    answer_documents = []
    for document in list_documents():
        title = str(getattr(document, "title", "") or "").strip()
        if "答题卡" in title or not any(
            label in title for label in ("参考答案", "答案", "评分标准")
        ):
            continue
        base = title
        for label in ("参考答案", "评分标准", "答案"):
            base = base.replace(label, "")
        if base.strip() == source_title or title.startswith(source_title):
            answer_documents.append(document)

    def apply_reference(node: QuestionCandidate, reference: str) -> None:
        if reference not in node.answer_refs:
            node.answer_refs.append(reference)
        for child in node.children:
            apply_reference(child, reference)

    for root in candidates:
        match = _ROOT_QUESTION_NUMBER.match(root.number.strip())
        if match is None:
            continue
        number = match.group(1)
        marker = re.compile(
            rf"(?m)(?:^|\n)\s*{re.escape(number)}\s*[\.．、:：]"
        )
        hits = []
        for document in answer_documents:
            for page in list_pages(int(document.id)):
                content = str(getattr(page, "searchable_content", "") or "")
                if marker.search(content):
                    hits.append((document, page))
        if len(hits) != 1:
            # Ambiguous/missing is safer than silently binding a wrong answer.
            continue
        document, page = hits[0]
        reference = (
            f"{getattr(document, 'title', '')} · 第 "
            f"{int(page.page_number)} 页 · 第{number}题"
        )
        apply_reference(root, reference)
    return candidates


def _node_at_path(
    candidates: list[QuestionCandidate], node_path: str
) -> QuestionCandidate:
    """Resolve a persisted candidate by its root/child index path."""

    try:
        indexes = [int(part) for part in str(node_path).split(".")]
        if not indexes or any(index < 0 for index in indexes):
            raise ValueError
        node = candidates[indexes[0]]
        for index in indexes[1:]:
            node = node.children[index]
        return node
    except (IndexError, TypeError, ValueError) as exc:
        raise QuestionCandidateError(f"找不到候选结构节点：{node_path}") from exc


# ------------------------------------------------------------------ prompt
def build_extraction_prompt(page_text: str, diagram_text: str) -> str:
    """One-call, whole-page candidate extraction (budget-friendly)."""

    return (
        "你是题目拆分助手。下面是一页资料的识别文字和 AI 图表解析。"
        "请先判断每道题是 atomic（可独立作答、评价、复盘）还是 "
        "composite（共享题干下有多个显式小问），并将 composite "
        "按原卷结构递归拆到 atomic 叶子。\n\n"
        "【系统识别出的原始文字】\n"
        f"{(page_text or '（无）')[:6000]}\n\n"
        "【AI 图表解析（结构化或描述）】\n"
        f"{(diagram_text or '（无）')[:4000]}\n\n"
        "要求：\n"
        "1. 只拆页面主体的题目。照片边缘露出的相邻页/其他章节的窄条不算本页，"
        "绝不为它生成题目候选。\n"
        "2. 题目树每个节点输出：\n"
        '   - "number": 题号（用图中实际编号，如 "2-15"；没有编号就给 ""）\n'
        '   - "stem": 题干（从识别文字忠实摘录；题干不完整时就摘可见部分，'
        "绝不要凭教材知识补写完整题干）\n"
        "     忠实摘录是硬要求：逐句照抄识别文字里属于这道题的原句，"
        "不要概括、改写、合并、简化或用自己的话重新表述；"
        "识别文字有噪声时也照原样保留，宁可长一点也不要概述。\n"
        '   - "completeness": 只评文字本身——"complete"（题号/题干/选项/小问文字都在）'
        '或 "incomplete"（文字真的缺字/缺选项/被截断）\n'
        "     注意：文字完整但需要看本页的地图/图表才能作答，是 complete，"
        "绝不是 incomplete。\n"
        '   - "incomplete_reason": 文字不完整时的原因；文字完整时给空字符串\n'
        '   - "visual_dependency": "none"（纯文字题，不需要任何图）或 "required"'
        "（必须结合本页某图/图表组作答）或 \"uncertain\"（无法确定）\n"
        '   - "visual_notes": visual_dependency 不是 none 时，用一句话写清学生该看'
        "哪张图/图表组（照抄图名或材料导语，如「共享材料：江苏省各区域人口密度分布"
        "示意图（第1～3题共用）」）；none 时给空字符串\n"
        '   - "figure_refs": 本题涉及的图编号数组（如 ["图 2-75"]）\n'
        '   - "question_kind": "atomic" 或 "composite"\n'
        '   - "children": 子题数组；atomic 必须为 []，composite 继续递归\n'
        '   - "shared_context_refs": 需继承的父题/材料引用（如 ["parent"]）\n'
        '   - "page_refs"/"image_refs"/"answer_refs": 可确认的来源引用数组\n'
        '   - "split_source": "explicit_numbering"|"layout"|"ai_inference"\n'
        '   - "split_confidence": "high"|"medium"|"low"\n'
        "3. 优先级：原卷显式编号 > 显式子编号 > 排版层级 > 语义。"
        "只有原卷存在的层级才能创建。\n"
        "4. 不要按句子、文字长度或评分点拆题。未继续编号的"
        "「写方程式并说明理由」保留为一个 atomic；评分点不是子题。\n"
        "5. 手写答案的①②③、答案解析编号或评分标准不得当成原题结构。"
        "OCR 丢号时可结合图像/排版，但必须标低置信度，不得虚构层级。\n"
        "6. 页面主体没有题目就输出空数组，不要硬凑。\n"
        "7. 只输出一个 JSON 对象：{\"candidates\": [...]}，不要输出其它文字。"
    )


def parse_candidates_payload(raw: str) -> list[QuestionCandidate]:
    """Parse the model JSON into validated candidates (never half-trusts)."""

    cleaned = str(raw).strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise QuestionCandidateError("AI 未返回有效的题目候选 JSON。")
    payload = cleaned[start : end + 1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as first_exc:
        # Real model output occasionally omits exactly one comma between two
        # candidate fields, for example ``"stem": "..." "completeness":``.
        # Repair only this narrow, schema-bounded shape; arbitrary malformed
        # text is still rejected so we never half-trust a model response.
        field_names = (
            "number|stem|completeness|incomplete_reason|visual_dependency|"
            "visual_notes|figure_refs"
        )
        repaired = re.sub(
            rf'("|\}}|\])\s*("(?:{field_names})"\s*:)',
            r"\1, \2",
            payload,
        )
        # A trailing comma before an object/array close is another common
        # structured-output slip and is safe to remove at JSON boundaries.
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise QuestionCandidateError(
                f"题目候选 JSON 解析失败：{first_exc}"
            ) from exc
        LOGGER.warning("题目候选 JSON 存在轻微格式错误，已按字段边界修复后校验。")
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise QuestionCandidateError("题目候选 JSON 结构无效。")
    stamp = _utc_now()
    def parse_item(item: dict) -> QuestionCandidate:
        completeness = str(item.get("completeness", "incomplete")).strip()
        if completeness not in _COMPLETENESS_LEVELS:
            completeness = "incomplete"
        visual_dependency = str(item.get("visual_dependency", "")).strip()
        if visual_dependency not in _VISUAL_DEPENDENCIES:
            # Honest inference when the model omits the field: an
            # incomplete_reason about a map/figure is a dependency, not
            # missing text (G1-GEO-02); explicit figure refs imply one too.
            reason_text = str(item.get("incomplete_reason", ""))
            if "依赖的图" in reason_text or "依赖的地图" in reason_text:
                visual_dependency = "required"
            elif item.get("figure_refs"):
                visual_dependency = "required"
            else:
                visual_dependency = "uncertain"
        children = [
            parse_item(child)
            for child in item.get("children", [])
            if isinstance(child, dict)
        ]
        split_source = str(item.get("split_source", "ai_inference")).strip()
        if split_source not in _SPLIT_SOURCES:
            split_source = "ai_inference"
        split_confidence = str(item.get("split_confidence", "low")).strip()
        if split_confidence not in _SPLIT_CONFIDENCE:
            split_confidence = "low"
        candidate = QuestionCandidate(
            number=str(item.get("number", "")).strip(),
            stem=str(item.get("stem", "")).strip(),
            completeness=completeness,
            incomplete_reason=str(item.get("incomplete_reason", "")).strip(),
            figure_refs=[str(f).strip() for f in item.get("figure_refs", []) if str(f).strip()],
            status="pending",
            extracted_at=stamp,
            visual_dependency=visual_dependency,
            visual_notes=str(item.get("visual_notes", "")).strip(),
            binding_confirmed=False,
            question_kind="composite" if children else "atomic",
            children=children,
            shared_context_refs=[
                str(value).strip()
                for value in item.get("shared_context_refs", [])
                if str(value).strip()
            ],
            page_refs=[
                int(value) for value in item.get("page_refs", [])
                if isinstance(value, int)
            ],
            image_refs=[
                str(value).strip() for value in item.get("image_refs", [])
                if str(value).strip()
            ],
            answer_refs=[
                str(value).strip() for value in item.get("answer_refs", [])
                if str(value).strip()
            ],
            split_source=split_source,
            split_confidence=split_confidence,
        )
        candidate.validate()
        return candidate

    candidates: list[QuestionCandidate] = []
    for item in data["candidates"]:
        if isinstance(item, dict):
            candidates.append(parse_item(item))
    _qualify_subquestion_numbers(candidates)
    return candidates


def stem_confidence_for(completeness: str) -> str:
    """Map candidate completeness onto the stem_confidence vocabulary."""

    return "probable" if completeness == "complete" else "uncertain"
