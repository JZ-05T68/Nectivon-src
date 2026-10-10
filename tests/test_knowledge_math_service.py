"""Automatic AI math display: broad notation, local authority and bounded calls."""

from __future__ import annotations

import json
from threading import Event
from types import SimpleNamespace

import pytest

from src.ai.completion_stage import CompletionStage, current_completion_stage
from src.database import Database
from src.knowledge_math_service import (
    KnowledgeMathAIService,
    knowledge_math_display,
    schedule_knowledge_math,
    typeset_knowledge_spans,
)
from src.knowledge_object_service import KnowledgeObjectService


@pytest.fixture()
def saved_knowledge(tmp_path):
    database = Database(tmp_path / "knowledge.db")
    service = KnowledgeObjectService(database)
    knowledge = service.create(kind="concept", title="通用数学格式",
                               content="x的n次方根；a/b；f'(x)",
                               epistemic_basis="personal_judgment").knowledge_object
    return database, tmp_path / "cache", knowledge


@pytest.mark.parametrize(("literal", "latex"), [
    ("(a+b)/(c-d)", r"\dfrac{a+b}{c-d}"),
    ("x的n次方根", r"\sqrt[n]{x}"),
    ("sqrt(x+1)", r"\sqrt{x+1}"),
    ("a^(x+1)", r"a^{x+1}"),
    ("a_n", r"a_{n}"),
    ("log_2(x)", r"\log_{2}(x)"),
    ("f'(x)", r"f'(x)"),
    ("dy/dx", r"\dfrac{dy}{dx}"),
    ("u对x的偏导数", r"\dfrac{\partial u}{\partial x}"),
    ("∫从0到1 x^2 dx", r"\int_{0}^{1}x^{2}\,\mathrm{d}x"),
    ("∫∫_D f(x,y) dxdy", r"\iint_{D}f(x,y)\,\mathrm{d}x\,\mathrm{d}y"),
    ("lim(x→0) sin(x)/x", r"\lim_{x\to 0}\dfrac{\sin x}{x}"),
    ("求和 k=1到n a_k", r"\sum_{k=1}^{n}a_{k}"),
    ("乘积 k=1到n a_k", r"\prod_{k=1}^{n}a_{k}"),
    ("矩阵 [[a,b],[c,d]]", r"\begin{bmatrix}a&b\\c&d\end{bmatrix}"),
    ("f(x)=x(x≥0)，-x(x<0)", r"f(x)=\begin{cases}x&x\geq0\\-x&x<0\end{cases}"),
    ("A∪B⊆C", r"A\cup B\subseteq C"),
    ("P(A|B)", r"P(A\mid B)"),
    ("∀x∈R，∃y>x", r"\forall x\in\mathbb{R},\exists y>x"),
    ("C(n,k)", r"\binom{n}{k}"),
])
def test_generic_math_is_not_limited_to_initial_examples(literal, latex) -> None:
    source = f"说明：{literal}。保留全部人工说明。"
    display, rejected = typeset_knowledge_spans(source, [{"text": literal, "latex": latex}])
    assert display == f"说明：${latex}$。保留全部人工说明。"
    assert rejected == 0


@pytest.mark.parametrize("span", [
    {"text": "a+b=3", "latex": "a+b=3"},
    {"text": "a+b=2", "latex": "a-b=2"},
    {"text": "a+b=2", "latex": r"\dfrac{a}{b"},
    {"text": "a+b=2", "latex": r"\href{https://example.com}{a+b=2}"},
    {"text": "a+b=2", "latex": "$a+b=2$"},
])
def test_reject_wrong_source_changed_simple_math_and_damaged_latex(span) -> None:
    assert typeset_knowledge_spans("a+b=2", [span]) == ("a+b=2", 1)


def test_existing_latex_code_and_fraction_precedence_are_preserved() -> None:
    source = r"已有 $a/b$，代码 `a/b`，普通 a/b+c。"
    display, rejected = typeset_knowledge_spans(source, [
        {"text": "$a/b$", "latex": r"\dfrac{a}{b}"},
        {"text": "`a/b`", "latex": r"\dfrac{a}{b}"},
        {"text": "a/b+c", "latex": r"\dfrac{a}{b+c}"},
    ])
    assert display == source and rejected == 3
    assert typeset_knowledge_spans("x的n次方根", [
        {"text": "x的n次方根", "latex": r"\sqrt[n]{x}", "block": True},
    ], title=True) == (r"$\sqrt[n]{x}$", 0)


def test_save_conversion_reuses_audited_provider_and_never_rewrites_source(saved_knowledge) -> None:
    database, cache, knowledge = saved_knowledge
    before = KnowledgeObjectService(database).revisions(knowledge.id)

    class Provider:
        calls = 0

        def complete(self, prompt, **kwargs):
            self.calls += 1
            assert current_completion_stage() == CompletionStage.LEARNING_DRAFT
            assert kwargs["source_feature"] == "knowledge_math_typesetting"
            assert kwargs["target_refs"] == (f"knowledge:{knowledge.id}",)
            assert kwargs["max_completion_tokens"] == 8192
            assert "x的n次方根" in prompt
            return SimpleNamespace(text=json.dumps({"title": [], "content": [
                {"text": "x的n次方根", "latex": r"\sqrt[n]{x}"},
                {"text": "a/b", "latex": r"\dfrac{a}{b}", "block": True},
                {"text": "f'(x)", "latex": "f'(x)"},
            ]}), finish_reason="stop")

    provider = Provider()
    ai = KnowledgeMathAIService(provider)
    future = schedule_knowledge_math(database, cache, knowledge.id, ai)
    assert future is not None and future.result(timeout=5)
    assert provider.calls == 1
    display = knowledge_math_display(cache, database.database_path, knowledge)
    assert display.status == "ready"
    assert r"$\sqrt[n]{x}$" in display.content
    assert r"$$\dfrac{a}{b}$$" in display.content
    assert KnowledgeObjectService(database).get(knowledge.id) == knowledge
    assert KnowledgeObjectService(database).revisions(knowledge.id) == before
    assert schedule_knowledge_math(database, cache, knowledge.id, ai) is None
    assert provider.calls == 1
    assert all("api_key" not in file.read_text("utf-8") for file in cache.rglob("*.json"))


def test_background_conversion_deduplicates_and_discards_stale_results(saved_knowledge) -> None:
    database, cache, knowledge = saved_knowledge
    entered, release = Event(), Event()

    class AI:
        calls = 0

        def _complete(self, *args, **kwargs):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5)
            return '{"title": [], "content": []}'

    ai = AI()
    future = schedule_knowledge_math(database, cache, knowledge.id, ai)
    assert future is not None
    try:
        assert entered.wait(timeout=3)
        assert not future.done()
        assert knowledge_math_display(cache, database.database_path, knowledge).status == "pending"
        assert schedule_knowledge_math(database, cache, knowledge.id, ai) is None
        edited = KnowledgeObjectService(database).update_content(
            knowledge.id, content="人工后续修改 y的n次方根",
        ).knowledge_object
    finally:
        release.set()
    assert not future.result(timeout=5)
    display = knowledge_math_display(cache, database.database_path, edited)
    assert display.status == "none" and display.content == edited.content
    assert ai.calls == 1


def test_optional_failure_never_retries_or_exposes_provider_details(
    saved_knowledge, caplog,
) -> None:
    database, cache, knowledge = saved_knowledge

    class AI:
        calls = 0

        def _complete(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("sensitive-provider-response-for-test")

    ai = AI()
    assert schedule_knowledge_math(database, cache, knowledge.id, None) is None
    future = schedule_knowledge_math(database, cache, knowledge.id, ai)
    assert future is not None and not future.result(timeout=5)
    display = knowledge_math_display(cache, database.database_path, knowledge)
    assert display.status == "failed" and display.content == knowledge.content
    assert schedule_knowledge_math(database, cache, knowledge.id, ai) is None
    assert ai.calls == 1
    assert "sensitive-provider-response-for-test" not in caplog.text
    assert "sensitive-provider-response-for-test" not in next(cache.rglob("*.json")).read_text()
    retry = schedule_knowledge_math(database, cache, knowledge.id, ai, retry=True)
    assert retry is not None and not retry.result(timeout=5)
    assert ai.calls == 2
    assert database.get_knowledge_object(knowledge.id) == knowledge


def test_other_database_and_new_content_never_receive_old_display(
    saved_knowledge, tmp_path,
) -> None:
    database, cache, knowledge = saved_knowledge

    class AI:
        def _complete(self, *args, **kwargs):
            return json.dumps({"title": [], "content": [
                {"text": "x的n次方根", "latex": r"\sqrt[n]{x}"},
            ]})

    future = schedule_knowledge_math(database, cache, knowledge.id, AI())
    assert future is not None and future.result(timeout=5)
    other = knowledge_math_display(cache, tmp_path / "other.db", knowledge)
    assert other.status == "none" and other.content == knowledge.content
    edited = KnowledgeObjectService(database).update_content(
        knowledge.id, title="人工新标题",
    ).knowledge_object
    assert knowledge_math_display(cache, database.database_path, edited).status == "none"


@pytest.mark.parametrize("raw", ["not JSON", '{"title": [], "content": "bad shape"}'])
def test_malformed_ai_result_falls_back_to_saved_original(saved_knowledge, raw) -> None:
    database, cache, knowledge = saved_knowledge

    class AI:
        def _complete(self, *args, **kwargs):
            return raw

    future = schedule_knowledge_math(database, cache, knowledge.id, AI())
    assert future is not None and not future.result(timeout=5)
    display = knowledge_math_display(cache, database.database_path, knowledge)
    assert display.content == knowledge.content


def test_corrupt_and_interrupted_caches_never_trigger_automatic_calls(saved_knowledge) -> None:
    database, cache, knowledge = saved_knowledge

    class AI:
        calls = 0

        def _complete(self, *args, **kwargs):
            self.calls += 1
            return '{"title": [], "content": []}'

    ai = AI()
    future = schedule_knowledge_math(database, cache, knowledge.id, ai)
    assert future is not None and future.result(timeout=5)
    path = next(cache.rglob("*.json"))
    path.write_text("[]", encoding="utf-8")
    assert knowledge_math_display(cache, database.database_path, knowledge).status == "failed"
    path.write_text('{"status":"pending"}', encoding="utf-8")
    interrupted = knowledge_math_display(cache, database.database_path, knowledge)
    assert interrupted.status == "interrupted" and interrupted.content == knowledge.content
    assert schedule_knowledge_math(database, cache, knowledge.id, ai) is None
    assert ai.calls == 1


def test_truncated_response_is_not_accepted_or_retried(saved_knowledge) -> None:
    database, cache, knowledge = saved_knowledge

    class Provider:
        calls = 0

        def complete(self, *args, **kwargs):
            self.calls += 1
            return SimpleNamespace(text='{"title": [], "content": []}', finish_reason="length")

    provider = Provider()
    future = schedule_knowledge_math(
        database, cache, knowledge.id, KnowledgeMathAIService(provider),
    )
    assert future is not None and not future.result(timeout=5)
    assert knowledge_math_display(cache, database.database_path, knowledge).status == "failed"
    assert provider.calls == 1
