"""Chinese-friendly local search orchestration and result presentation."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Protocol

from src.models import (
    SearchFacetCounts,
    SearchField,
    SearchFilters,
    SearchResult,
    SearchSnippet,
    SearchSort,
)
from src.scan_artifact_filter import filter_knowledge_text
from src.text_utils import (
    build_context_excerpt,
    build_context_excerpts,
    chinese_numeral_digit_fragments,
    cjk_char_fragments,
    extract_relaxed_search_terms,
    extract_search_terms,
    highlight_html,
    identifier_digit_fragments,
    literal_match_spans,
    partition_query_terms,
    question_number_marker_fragments,
)

LOGGER = logging.getLogger(__name__)


class SearchDatabase(Protocol):
    """The small database surface required by :class:`SearchService`."""

    def search(
        self,
        query: str,
        limit: int = 20,
        *,
        terms: tuple[str, ...] | None = None,
        filters: SearchFilters | None = None,
        sort_by: SearchSort | str = SearchSort.RELEVANCE,
    ) -> list[SearchResult]:
        """Return filtered, ranked page matches for a safe FTS5 expression."""

    def search_facet_counts(
        self,
        *,
        terms: tuple[str, ...] = (),
        filters: SearchFilters | None = None,
    ) -> SearchFacetCounts:
        """Return counts for the complete current search and filter state."""

    def search_document_counts(
        self,
        *,
        terms: tuple[str, ...] = (),
        filters: SearchFilters | None = None,
    ) -> dict[int, int]:
        """Return exact per-document counts for the complete filtered result set."""


class SearchService:
    """Normalize free-form queries and return source-aware local matches."""

    def __init__(
        self,
        database: SearchDatabase,
        *,
        max_query_terms: int = 16,
        snippet_length: int = 180,
        max_results: int = 100,
    ) -> None:
        if max_query_terms < 1:
            raise ValueError("max_query_terms 必须大于 0")
        if snippet_length < 20:
            raise ValueError("snippet_length 不能小于 20")
        if max_results < 1:
            raise ValueError("max_results 必须大于 0")
        self._database = database
        self._max_query_terms = max_query_terms
        self._snippet_length = snippet_length
        self._max_results = max_results

    def query_terms(self, query: str) -> tuple[str, ...]:
        """Expose the same literal terms used for search and highlighting."""

        markers = question_number_marker_fragments(query)
        ordinary = extract_search_terms(query, max_terms=self._max_query_terms)
        return tuple(dict.fromkeys((*markers, *ordinary)))[: self._max_query_terms]

    def normalize_query(self, query: str) -> str:
        """Convert free-form input into an operator-safe FTS5 OR expression."""

        return " OR ".join(f'"{term}"' for term in self.query_terms(query))

    def search(
        self,
        query: str,
        limit: int = 20,
        *,
        filters: SearchFilters | None = None,
        sort_by: SearchSort | str = SearchSort.RELEVANCE,
        widen_on_zero_recall: bool = True,
    ) -> list[SearchResult]:
        """Search local pages, safely returning an empty list for empty input.

        Recall (the literal term filter) always uses every extracted term, but
        FTS5 ``bm25`` ranking weights only high-information terms
        (HBV2-MORNING-20260909 M1): isolated digits and interrogative
        fragments stay recallable yet can no longer crowd out the pages that
        match the question's content words. When demotion leaves ranking
        terms that match no page at all, :class:`Database` restores
        full-term ranking for that query (M1-RESIDUAL fallback).

        A query whose literal recall is empty retries once with a widened
        recall pool (M1R-A second residual, HY4 independent retest
        2026-09-09): relaxed term extraction that keeps single-character CJK
        hooks (「铜 铝 导电」 loses 铜/铝 to the de-noise filter), Arabic digit
        fragments converted from Chinese numeral runs (「二十米」 vs 「20 m/s」),
        character fragments of multi-character CJK terms (「保险丝」 vs
        「熔断器」 share no token), and the glued letter+digit token fragments
        (「20m」). The retry is gated on zero results, so it can never change
        an already-recalling query.
        """

        terms = self.query_terms(query)
        if not terms or limit <= 0:
            return []
        question_markers = question_number_marker_fragments(query)
        normalized_query = " OR ".join(f'"{term}"' for term in terms)
        rank_terms, _ = partition_query_terms(terms, query)
        safe_limit = min(limit, self._max_results)
        # Explicit question references need enough candidates to recover the
        # exact numbered page from title-heavy exam families. The public result
        # window remains ``safe_limit`` after the deterministic re-rank below.
        database_limit = self._max_results if question_markers else safe_limit
        try:
            results = self._database.search(
                normalized_query,
                limit=database_limit,
                terms=terms,
                rank_terms=rank_terms,
                filters=filters or SearchFilters(),
                sort_by=sort_by,
            )
            if not results and widen_on_zero_recall:
                relaxed = extract_relaxed_search_terms(query)
                # Recall widens with relaxed terms + digit fragments only.
                # Character fragments (M1R-A second residual) stay OUT of
                # recall: characters like 电/大/一 LIKE-match half the corpus
                # and drown the target pool. They join the *ranking* term set
                # below, where the coverage-weighted content boost turns each
                # additionally-matched fragment into ranking weight.
                widened_recall = tuple(
                    dict.fromkeys(
                        (
                            *terms,
                            *relaxed,
                            *chinese_numeral_digit_fragments(query),
                            *identifier_digit_fragments(terms),
                        )
                    )
                )
                if widened_recall != terms:
                    retry_rank_terms, _ = partition_query_terms(widened_recall, query)
                    retry_rank_terms = tuple(
                        dict.fromkeys(
                            (*retry_rank_terms, *cjk_char_fragments(relaxed))
                        )
                    )
                    results = self._database.search(
                        " OR ".join(f'"{term}"' for term in widened_recall),
                        limit=database_limit,
                        terms=widened_recall,
                        rank_terms=retry_rank_terms,
                        filters=filters or SearchFilters(),
                        sort_by=sort_by,
                        coverage_content_boost=True,
                    )
                    if results:
                        terms = widened_recall
        except Exception:
            LOGGER.exception("本地全文检索失败")
            raise
        if question_markers:
            results = _prioritize_question_number_results(
                results, question_markers, terms=terms, limit=safe_limit
            )
        else:
            results = results[:safe_limit]
        return [self._with_natural_snippet(result, terms) for result in results]

    def build_snippet(
        self,
        content: str,
        terms: tuple[str, ...] | list[str],
        *,
        max_chars: int | None = None,
    ) -> str:
        """Build a compact natural-text excerpt centred on the first match."""

        length = max_chars if max_chars is not None else self._snippet_length
        return build_context_excerpt(content, terms, max_chars=length)

    def facet_counts(
        self,
        query: str,
        *,
        filters: SearchFilters | None = None,
    ) -> SearchFacetCounts:
        """Count filter values consistently, including when the query is empty."""

        return self._database.search_facet_counts(
            terms=self.query_terms(query),
            filters=filters or SearchFilters(),
        )

    def document_counts(
        self,
        query: str,
        *,
        filters: SearchFilters | None = None,
    ) -> dict[int, int]:
        """Count complete filtered matches by document without rerunning search."""

        terms = self.query_terms(query)
        if not terms:
            return {}
        return self._database.search_document_counts(
            terms=terms,
            filters=filters or SearchFilters(),
        )

    def highlighted_snippet(self, result: SearchResult, query: str) -> str:
        """Return an escaped HTML snippet containing only safe ``mark`` tags."""

        snippet = result.snippet or self.build_snippet(
            result.content, self.query_terms(query)
        )
        return highlight_html(snippet, self.query_terms(query))

    def _with_natural_snippet(
        self, result: SearchResult, terms: tuple[str, ...]
    ) -> SearchResult:
        # V086-309-PRT1: snippet sources for the three content fields are the
        # knowledge surfaces (same scan-artifact filter as the FTS mirrors),
        # so an excerpt can never centre on a raw scanner-branding line even
        # when the term also exists in real page content. Raw evidence fields
        # on the result itself stay verbatim for preview/audit surfaces.
        content_surface = {
            SearchField.MARKDOWN: filter_knowledge_text(
                result.markdown_content
            ).filtered_text,
            SearchField.OCR_TEXT: filter_knowledge_text(
                result.ocr_text
            ).filtered_text,
            SearchField.EXTRACTED_TEXT: filter_knowledge_text(
                result.extracted_text
            ).filtered_text,
        }
        source_values = {
            SearchField.MARKDOWN: content_surface[SearchField.MARKDOWN],
            SearchField.OCR_TEXT: content_surface[SearchField.OCR_TEXT],
            SearchField.EXTRACTED_TEXT: content_surface[SearchField.EXTRACTED_TEXT],
            SearchField.DOCUMENT_TITLE: result.document_title,
            SearchField.FILENAME: result.filename,
            SearchField.TAG: "、".join(result.tags),
            SearchField.PROJECT: "、".join(result.projects),
        }
        snippets: list[SearchSnippet] = []
        total_matches = 0
        seen_excerpt_text: set[str] = set()
        for field in result.match_fields:
            source = source_values[field]
            field_matches = len(literal_match_spans(source, terms))
            total_matches += field_matches
            remaining = 3 - len(snippets)
            if remaining <= 0:
                continue
            for excerpt in build_context_excerpts(
                source,
                terms,
                max_chars=self._snippet_length,
                max_excerpts=remaining,
            ):
                normalized = " ".join(excerpt.casefold().split())
                if normalized in seen_excerpt_text:
                    continue
                seen_excerpt_text.add(normalized)
                snippets.append(
                    SearchSnippet(
                        field=field,
                        text=excerpt,
                        match_count=field_matches,
                    )
                )
        fallback_source = result.content.strip() or result.snippet
        fallback = self.build_snippet(fallback_source, terms)
        if not snippets and fallback:
            fallback_field = (
                result.match_fields[0]
                if result.match_fields
                else SearchField.EXTRACTED_TEXT
            )
            snippets.append(
                SearchSnippet(
                    field=fallback_field,
                    text=fallback,
                    match_count=len(literal_match_spans(fallback_source, terms)),
                )
            )
        primary = snippets[0].text if snippets else fallback
        return replace(
            result,
            snippet=primary,
            snippets=tuple(snippets),
            match_count=total_matches,
        )


__all__ = ["SearchDatabase", "SearchService"]


def _prioritize_question_number_results(
    results: list[SearchResult],
    markers: tuple[str, ...],
    *,
    terms: tuple[str, ...],
    limit: int,
) -> list[SearchResult]:
    """Put exact numbered pages and their continuation page ahead of title hits.

    Exam titles often match every page, while the requested number appears
    only on the page where that question begins. Answers may continue on the
    following page without repeating the number, so one adjacent page is kept
    with the exact hit. Relative order inside each tier stays unchanged.
    """

    if not results or not markers or limit <= 0:
        return results[: max(limit, 0)]

    folded_markers = tuple(marker.casefold() for marker in markers)
    title_terms = tuple(
        term.casefold()
        for term in terms
        if term not in markers
        and not term.isdigit()
        and not (len(term) == 1 and "\u3400" <= term <= "\u9fff")
        and term not in ("第", "题", "问", "問")
    )

    def has_marker(result: SearchResult) -> bool:
        content = "\n".join(
            (result.markdown_content, result.ocr_text, result.extracted_text)
        ).casefold()
        return any(marker in content for marker in folded_markers)

    exact_pages = {
        (result.document_id, result.page_number)
        for result in results
        if has_marker(result)
    }

    def tier(result: SearchResult) -> int:
        identity = (result.document_id, result.page_number)
        if identity in exact_pages:
            return 0
        if (result.document_id, result.page_number - 1) in exact_pages:
            return 1
        return 2

    def is_answer_index(result: SearchResult) -> bool:
        """Return whether this is the first-page answer table of an exam."""

        metadata = f"{result.document_title}\n{result.filename}".casefold()
        return result.page_number == 1 and any(
            label in metadata for label in ("参考答案", "答案", "评分标准")
        )

    def title_coverage(result: SearchResult) -> int:
        metadata = f"{result.document_title}\n{result.filename}".casefold()
        return sum(term in metadata for term in title_terms)

    title_corpus = "\n".join(
        f"{result.document_title}\n{result.filename}" for result in results
    ).casefold()
    content_only_terms = tuple(
        term for term in title_terms if term not in title_corpus
    )

    max_title_coverage = max(title_coverage(result) for result in results)

    def is_strong_content_match(result: SearchResult) -> bool:
        # OCR may lose the printed question number beside a diagram. Keep a
        # page from the best-matching exam title when it also contains another
        # query-specific content word (for example “蹦床” or “动量定理”).
        content = "\n".join(
            (result.markdown_content, result.ocr_text, result.extracted_text)
        ).casefold()
        return (
            title_coverage(result) == max_title_coverage
            and bool(content_only_terms)
            and any(term in content for term in content_only_terms)
        )

    def source_role(result: SearchResult) -> int:
        """Prefer the original question, then its answer, then answer sheet."""

        metadata = f"{result.document_title}\n{result.filename}".casefold()
        if "答题卡" in metadata:
            return 2
        if any(label in metadata for label in ("参考答案", "答案", "评分标准", "解析")):
            return 1
        return 0

    ordered = sorted(
        enumerate(results),
        key=lambda item: (
            0
            if tier(item[1]) < 2
            or is_answer_index(item[1])
            or is_strong_content_match(item[1])
            else 1,
            -title_coverage(item[1]),
            source_role(item[1]),
            (
                tier(item[1])
                if tier(item[1]) < 2 or not is_strong_content_match(item[1])
                else 2
            ),
            item[0],
        ),
    )
    return [result for _, result in ordered[:limit]]
