"""V086-R1 fix-round retest (AppTest level, no browser needed).

The sandbox browser websocket was frozen by an environment-level proxy
failure (see report §9); this script re-tests the new UI contracts
server-side via Streamlit AppTest.  Clearly NOT a pixel-level retest.

Run: python tests_manual/v086_r1_retest_apptest.py
"""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit.testing.v1 as st_testing  # noqa: E402

from src import runtime as app_runtime  # noqa: E402
from src.config import Settings  # noqa: E402
from src.database import Database  # noqa: E402
from src.learning_workflow_service import QuestionService  # noqa: E402
from src.question_candidate_service import (  # noqa: E402
    QuestionCandidate,
    QuestionCandidateStore,
)

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str) -> None:
    RESULTS.append((bool(ok), name))
    print(("PASS " if ok else "FAIL ") + name)


def build_runtime(tmp_path: Path) -> Database:
    database = Database(tmp_path / "database" / "knowledge.db")
    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    document = database.create_document(
        title="复测夹具",
        filename="fixture.pdf",
        source_path=raw / "fixture.pdf",
        sha256=hashlib.sha256(b"fixture").hexdigest(),
        page_count=1,
    )
    (raw / "fixture.pdf").write_bytes(b"pdf")
    image = pages_dir / "page_0001.png"
    # A real 1x1 PNG: pages render the original page image via st.image, and
    # PIL rejects garbage bytes (UnidentifiedImageError) in AppTest runs.
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
    )
    database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        extracted_text="1. 已知 f'(1)=2，求极限。",
        status="ready",
    )
    return database


def patch_runtime(tmp_path: Path, database: Database) -> Settings:
    settings = Settings(
        data_dir=tmp_path,
        raw_dir=tmp_path / "raw",
        pages_dir=tmp_path / "pages",
        markdown_dir=tmp_path / "markdown",
        database_dir=tmp_path / "database",
        database_path=tmp_path / "database" / "knowledge.db",
        logs_dir=tmp_path / "logs",
        log_path=tmp_path / "logs" / "retest.log",
        runtime_dir=tmp_path / "runtime",
        pid_path=tmp_path / "runtime" / "retest.pid.json",
        _env_file=None,
    )
    app_runtime.application_database = lambda: database
    app_runtime.application_settings = lambda: settings
    app_runtime.application_page_visual_service = lambda: __import__(
        "src.page_visual_service", fromlist=["PageVisualService"]
    ).PageVisualService(database)
    return settings


def run_page(path: str):
    at = st_testing.AppTest.from_file(path, default_timeout=90)
    at.run()
    return at


def page_texts(at) -> str:
    parts = []
    for block in at.markdown:
        parts.append(str(block.value))
    for block in at.caption:
        parts.append(str(block.value))
    for exp in at.expander:
        parts.append(str(exp.label))
    for err in at.error:
        parts.append(str(err.value))
    return "\n".join(parts)


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        database = build_runtime(tmp_path)
        patch_runtime(tmp_path, database)

        # ---- FIX-2/4/6 + R2-02: candidate cards live on the REVIEW page
        # (pages/5_待整理页面.py, render_join_learning_section), NOT on the
        # 学习整理 hub.  Seed one skipped + one pending candidate so both
        # the pending captions and the skipped recover-area render.
        store = QuestionCandidateStore(tmp_path / "question-candidates")
        store.save_page_candidates(
            1,
            [
                QuestionCandidate(
                    number="1",
                    stem="已知 f'(1)=2，求极限。",
                    completeness="complete",
                    incomplete_reason="",
                    figure_refs=[],
                    status="skipped",
                    extracted_at="2026-09-27T05:00:00+00:00",
                ),
                QuestionCandidate(
                    number="2",
                    stem="若 f'(2)=2，求极限。",
                    completeness="incomplete",
                    incomplete_reason="选项未拍全",
                    figure_refs=[],
                    status="pending",
                    extracted_at="2026-09-27T05:00:00+00:00",
                ),
            ],
        )
        # FIX-5: seed one handwriting draft (presence confirmed) BEFORE the
        # review-page run — the draft expander titles render on the review
        # page (pages/5), same surface as the candidate cards.
        from src.page_visual_service import PageVisualService

        visual = PageVisualService(database)
        visual.record_interpretation(
            1,
            provenance="HANDWRITING_VISION",
            content="视觉草稿内容示例",
            confidence="uncertain",
            origin="ai_vision",
            region_json={"handwriting_presence": "confirmed"},
        )

        at_review = run_page("pages/5_待整理页面.py")
        check(not at_review.exception, "待核对页渲染无异常")
        review_text = page_texts(at_review)
        check("已暂不整理（1 条）" in review_text, "FIX-2 已暂不整理恢复区")
        check("恢复" in review_text, "FIX-2 恢复按钮存在")
        check("本页拆出 1 道待处理题目候选" in review_text, "FIX-2 待处理候选计数")
        check("题干为 AI 识别摘录" in review_text, "FIX-4 候选卡识别警示")
        check("先选类型（错题/好题/典型题/方法题）" in review_text, "FIX-6 类型单选用途说明")
        check("候选题干的文字来源：" in review_text, "R2-02 候选来源标注")
        check("已人工修订" not in review_text, "FIX-1 无未编辑题目显示已人工修订徽标")
        check(
            ("最新" in review_text) and ("· 20" in review_text),
            "FIX-5 草稿标题带时间与最新徽标",
        )
        check("HANDWRITING_VISION" not in review_text, "FIX-7 枚举不泄漏到渲染文本")

        # restore flow through the service contract the button uses
        store.mark_status(1, "1", "pending")
        check(store.page_candidates(1)[0].status == "pending", "FIX-2 恢复后回到 pending")

        # ---- FIX-1/3 service contracts ----
        service = QuestionService(database)
        created = service.create_question_item(
            document_id=1,
            page_id=1,
            question_kind="good",
            question_number="1",
            stem_text="已知 f'(1)=2，求极限。",
        )
        check(created.user_edited is False, "FIX-1 候选加入默认非人工修订")
        service.delete_question_item(created.id)
        try:
            service.get_question_item(created.id)
            check(False, "FIX-3 删除后行不存在")
        except Exception:
            check(True, "FIX-3 删除后行不存在")
        page = database.get_page(1)
        check(
            page is not None and (page.extracted_text or "").startswith("1."),
            "FIX-3 原始页面文字保留",
        )

        # ---- 我的资料 page: renders the reader/correction surface. The
        # draft expander + its 最新 badge live on the review page above, so
        # here we only verify clean rendering and no enum leakage.
        at_files = st_testing.AppTest.from_file(
            "pages/17_我的资料.py", default_timeout=90
        )
        at_files.query_params["document"] = "1"
        at_files.query_params["page"] = "1"
        at_files.run()
        check(not at_files.exception, "我的资料页渲染无异常")
        files_text = page_texts(at_files)
        check("HANDWRITING_VISION" not in files_text, "FIX-7 枚举不泄漏到渲染文本")

        # ---- FIX-7: home metric label ----
        at_home = run_page("app.py")
        check(not at_home.exception, "首页渲染无异常")
        labels = [str(m.label) for m in at_home.metric]
        check("已手录页" in labels and "我的笔记" not in labels, "FIX-7 首页指标口径")

    ok = all(r for r, _ in RESULTS)
    print(
        ("ALL_PASS " if ok else "SOME_FAILED ")
        + f"({sum(1 for r, _ in RESULTS if r)}/{len(RESULTS)})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
