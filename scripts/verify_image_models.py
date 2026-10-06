"""Explicit live acceptance of image reading and math formatting model presets.

Run only on user-authorized material. Alternate models write isolated temporary
databases and candidates; formal question edits and provider settings stay intact.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from src.agent_document_reader import AgentReadingStore
from src.ai.completion_stage import CompletionStage, completion_stage_scope
from src.ai.model_registry import CapabilitySupport, ProviderId, get_capability_profile
from src.ai.openai_compatible import urllib_transport
from src.ai.provider import build_production_audited_provider
from src.ai.provider_config import ProviderConfigStore
from src.ai.provider_factory import (
    build_provider_adapter,
    resolve_deepseek_runtime,
    resolve_glm_runtime,
    resolve_hunyuan_runtime,
    resolve_kimi_runtime,
    resolve_qwen_runtime,
)
from src.database import Database
from src.page_image_reader import PageImageReader
from src.question_candidate_service import (
    QuestionCandidateStore,
    iter_atomic_leaves,
    iter_question_nodes,
    parse_candidates_payload,
)
from src.question_recognition_rules import CHOICE_LAYOUT_RULES, MATH_NOTATION_RULES
from src.runtime import (
    _LazyDatabaseAiCallLedger,
    _LazyTokenBudgetGuard,
    application_credential_store,
    application_settings,
)

RESOLVERS = {
    ProviderId.QWEN: resolve_qwen_runtime,
    ProviderId.DEEPSEEK: resolve_deepseek_runtime,
    ProviderId.KIMI: resolve_kimi_runtime,
    ProviderId.HUNYUAN: resolve_hunyuan_runtime,
    ProviderId.GLM: resolve_glm_runtime,
}


def verify_model(provider_id: str, model: str, document_id: int, root: Path) -> dict:
    """Make bounded calls to one preset, reporting failures without paid retries."""

    settings = application_settings()
    provider_name = ProviderId(provider_id)
    store = ProviderConfigStore()
    config = store.load()

    class ReadOnlyTestConfig:
        path = store.path

        def load(self):
            return replace(config, active_provider_id=provider_name)

    resolved = RESOLVERS[provider_name](
        settings,
        config_store=ReadOnlyTestConfig(),
        credential_store=application_credential_store(),
    )
    if resolved is None:
        return {"provider": provider_id, "model": model, "status": "not_configured"}
    resolved = replace(
        resolved,
        **{
            "llm_model" if provider_name is ProviderId.QWEN else "model": model,
        },
    )
    audited = build_production_audited_provider(
        build_provider_adapter(resolved, transport=urllib_transport),
        default_model=model,
        default_embedding_model=resolved.default_embedding_model,
        source_feature="model_acceptance",
        ledger=_LazyDatabaseAiCallLedger(),
        budget_guard=_LazyTokenBudgetGuard(settings),
    )
    directory = root / model
    directory.mkdir(parents=True, exist_ok=True)
    outcome = {"provider": provider_id, "model": model}
    if get_capability_profile(provider_id, model).effective.vision is CapabilitySupport.SUPPORTED:
        destination = directory / "database" / "knowledge.db"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (
            sqlite3.connect(settings.database_path) as source,
            sqlite3.connect(destination) as copy,
        ):
            source.backup(copy)
        reader = PageImageReader(
            database=Database(destination),
            provider=audited,
            readings=AgentReadingStore(directory / "readings"),
            candidates=QuestionCandidateStore(directory / "candidates"),
        )
        try:
            reader.read_document(
                document_id,
                force=True,
                progress_callback=lambda n, t: print(
                    model,
                    "page_attempted",
                    n,
                    t,
                    flush=True,
                ),
            )
            outcome["status"] = "image_reading_completed"
        except Exception as exc:  # noqa: BLE001 - model errors belong in the report
            outcome["status"] = "image_reading_failed"
            outcome["error"] = str(exc)
        pages = []
        for page in reader.database.list_pages(document_id):
            candidates = reader.candidates.page_candidates(page.id) or []
            nodes = list(iter_question_nodes(candidates))
            leaves = list(iter_atomic_leaves(candidates))
            pages.append(
                {
                    "page": page.page_number,
                    "fresh": reader.is_fresh(page),
                    "nodes": len(nodes),
                    "atomic_questions": len(leaves),
                    "image_regions": sum(len(n.visual_regions) for n in nodes),
                    "human_overrides": sum(n.user_edited for n in nodes),
                    "unlocated_figures": [
                        leaf.number
                        for _, leaf, _, parents in leaves
                        if leaf.visual_dependency == "required"
                        and not leaf.visual_regions
                        and not any(parent.visual_regions for parent in parents)
                    ],
                }
            )
        outcome["pages"] = pages
    else:
        outcome["image_reading"] = "unsupported_by_current_adapter"
        # Synthetic text checks human correction formatting, never OCR input.
        prompt = (
            MATH_NOTATION_RULES
            + CHOICE_LAYOUT_RULES
            + r"""
只排版下面人工输入的题干，绝不计算或解题。返回合法 JSON 对象：
{"candidates":[{"number":"1","stem":"...","completeness":"complete"}]}。
题干：比较 x^2、(x+1)^(n+1)、1/2、sin α+cos θ、∫_0^1 x^2 dx、Σ_(k=1)^n k。
选择题选项：A. α+β B. sin θ C. 1/2 D. x^2023。
LaTeX 包在 $ 中，指数必须上标，分数必须是 frac，ABCD 四项分别一个段落。
JSON 中 LaTeX 反斜杠双写。"""
        )
        try:
            with completion_stage_scope(CompletionStage.LEARNING_DRAFT):
                result = audited.complete(
                    prompt,
                    model=model,
                    max_completion_tokens=4096,
                    source_feature="model_math_acceptance",
                )
            (directory / "raw-math-response.json").write_text(
                json.dumps(
                    {
                        "model": model,
                        "response": result.text,
                        "finish_reason": result.finish_reason,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            candidate = parse_candidates_payload(result.text)[0]
            outcome["status"] = "math_formatting_completed"
            outcome["stem"] = candidate.stem
        except Exception as exc:  # noqa: BLE001 - report model availability honestly
            outcome["status"] = "math_formatting_failed"
            outcome["error"] = str(exc)
    (directory / "acceptance.json").write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--document", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_model(args.provider, args.model, args.document, args.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
