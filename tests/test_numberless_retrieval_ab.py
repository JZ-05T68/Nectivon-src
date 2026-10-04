"""Regression lock: numbered vs numberless content both recall (v0.8.5).

User feedback (陈奕伟, v0.8.5 closure round 2): content without a leading
number was reported as possibly unreachable by retrieval. The audited A/B
reproduction through the real product chain found the two variants fully
symmetric at every layer — FTS shadow tokenization, page-scope FTS,
knowledge-scope FTS, hybrid fusion, agent tool adapters and the final
evidence package. These tests lock that behavior: if a future change makes
the numberless variant miss where the numbered one hits (or vice versa),
they fail.

Real services only (offline, tmp SQLite): ``Database`` writes,
``_tokenize_for_fts`` shadow columns, ``SearchService`` (page scope),
``KnowledgeSearchService`` (knowledge scope), ``HybridSearchService``
(lexical fusion), both agent search adapters and the audited evidence
mapper. No AI provider and no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.response.tool_context import ToolResultContextMapper
from src.agent.tools.adapters.knowledge_search import KnowledgeSearchAdapter
from src.agent.tools.contracts import ToolContext, ToolInput
from src.ai.hybrid_search import HybridSearchService
from src.database import Database
from src.knowledge_context_packager import KnowledgeContextPackager
from src.knowledge_object_service import KnowledgeObjectService
from src.knowledge_search_service import KnowledgeSearchService
from src.models import (
    KnowledgeEpistemicBasis,
    KnowledgeObjectKind,
    PageStatus,
)
from src.search_service import SearchService

KB_UUID = "12345678-1234-1234-1234-123456789abc"

CONTENT_A = "1. 测试知识点内容"
CONTENT_B = "测试知识点内容"
QUERY_NUMBERLESS = "测试知识点内容"


@pytest.fixture()
def ab_db(tmp_path: Path) -> Database:
    """Real database with the A/B pair as one page each and one object each."""

    db = Database(tmp_path / "ab.db")
    doc_a = db.create_document(
        title="对照甲",
        filename="repro_a.pdf",
        source_path=tmp_path / "repro_a.pdf",
        sha256="a" * 64,
    )
    doc_b = db.create_document(
        title="对照乙",
        filename="repro_b.pdf",
        source_path=tmp_path / "repro_b.pdf",
        sha256="b" * 64,
    )
    db.create_page(
        document_id=doc_a.id,
        page_number=1,
        image_path=tmp_path / "pa.png",
        extracted_text=CONTENT_A,
        status=PageStatus.REVIEWED,
    )
    db.create_page(
        document_id=doc_b.id,
        page_number=1,
        image_path=tmp_path / "pb.png",
        extracted_text=CONTENT_B,
        status=PageStatus.REVIEWED,
    )
    service = KnowledgeObjectService(db)
    service.create(
        kind=KnowledgeObjectKind.FACT,
        title="对照甲",
        content=CONTENT_A,
        epistemic_basis=KnowledgeEpistemicBasis.DIRECT_OBSERVATION,
    )
    service.create(
        kind=KnowledgeObjectKind.FACT,
        title="对照乙",
        content=CONTENT_B,
        epistemic_basis=KnowledgeEpistemicBasis.DIRECT_OBSERVATION,
    )
    return db


def _shadow_content(db: Database, table: str, id_value: int, column: str) -> str:
    with db._connection() as connection:  # noqa: SLF001 - audit-only read
        row = connection.execute(
            f"SELECT {column} FROM {table} WHERE id = ?", (id_value,)
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_fts_shadow_entries_keep_body_tokens(ab_db: Database) -> None:
    body_tokens = {"测试", "知识", "知识点", "内容"}
    for row_id in (1, 2):
        page_shadow = _shadow_content(
            ab_db, "pages", row_id, "search_extracted_text"
        )
        object_shadow = _shadow_content(
            ab_db, "knowledge_objects", row_id, "search_content"
        )
        for shadow in (page_shadow, object_shadow):
            tokens = set(shadow.split())
            assert body_tokens <= tokens, (row_id, shadow)


def test_knowledge_scope_hits_both_variants_without_number(
    ab_db: Database,
) -> None:
    results = KnowledgeSearchService(ab_db).search(QUERY_NUMBERLESS)
    titles = {result.title for result in results}
    assert titles == {"对照甲", "对照乙"}


def test_page_scope_hits_both_variants_without_number(ab_db: Database) -> None:
    results = SearchService(ab_db).search(QUERY_NUMBERLESS)
    assert {result.page_id for result in results} == {1, 2}


def test_hybrid_hits_both_variants_without_number(ab_db: Database) -> None:
    outcome = HybridSearchService(
        lexical=SearchService(ab_db), hydration=ab_db
    ).search(QUERY_NUMBERLESS)
    assert {item.result.page_id for item in outcome.results} == {1, 2}


def test_numbered_query_still_hits_both_variants(ab_db: Database) -> None:
    """The numbered asking style must not lose the numberless variant."""

    results = KnowledgeSearchService(ab_db).search("1 测试知识点内容")
    titles = {result.title for result in results}
    assert titles == {"对照甲", "对照乙"}


def test_final_evidence_contains_both_variants(ab_db: Database) -> None:
    ko_result = KnowledgeSearchAdapter(KnowledgeSearchService(ab_db))(
        ToolInput(
            tool_name="knowledge_search",
            arguments={"query": QUERY_NUMBERLESS},
        ),
        ToolContext(),
    )
    assert ko_result.status.value == "success"
    mapper = ToolResultContextMapper()
    package = mapper.build(
        ko_result,
        question=QUERY_NUMBERLESS,
        packager=KnowledgeContextPackager(kb_uuid=KB_UUID),
    )
    object_ids = {
        item.stable_id.rsplit(":", 1)[1]
        for item in package.items
        if item.stable_id.endswith(("knowledge_object:1", "knowledge_object:2"))
    }
    assert object_ids == {"1", "2"}
