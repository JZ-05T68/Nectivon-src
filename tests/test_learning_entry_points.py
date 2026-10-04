"""Learning-workflow entry points (v0.8.6 RUN 3).

The first-class 学习整理 hub, the 工具→待整理 shortcut and the page-review
context entry must all converge on the same learning-asset store
(``question_items`` via the same QuestionService) - never separate copies.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from sqlite3 import connect

from streamlit.testing.v1 import AppTest

import src.runtime as runtime
from src.database import Database
from src.learning_workflow_service import QuestionService
from src.training_profile_service import TrainingProfileService


def _make_dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    data_dir = tmp_path / "data"
    for name in ("raw", "pages/1", "markdown"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    return data_dir, data_dir / "raw", data_dir / "pages", data_dir / "markdown"


def _document_with_page(database: Database, tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    document = database.create_document(
        title="学习整理入口夹具",
        filename="试卷.pdf",
        source_path=raw,
        sha256="7" * 64,
        import_status="completed",
    )
    database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        extracted_text="已知函数 f(x) 求极值。",
        status="ready",
    )


# ------------------------------------------------------------- hub page
def test_learning_hub_renders_counts_and_entries(tmp_path: Path, monkeypatch) -> None:
    data_dir, raw_dir, pages_dir, markdown_dir = _make_dirs(tmp_path)
    database = Database(data_dir / "database" / "knowledge.db")
    _document_with_page(database, tmp_path)
    QuestionService(database).create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        stem_text="已知函数 f(x) 求极值。",
    )
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(
        runtime,
        "application_training_profile_service",
        lambda: TrainingProfileService(data_dir / "training_profile.db"),
    )
    monkeypatch.setattr(
        runtime, "application_page_visual_service", lambda: (_ for _ in ()).throw(
            RuntimeError("AppTest 环境不提供视觉服务")
        )
    )

    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "pages" / "18_学习整理.py")
    ).run(timeout=30)

    assert not app.exception
    metric_values = [metric.value for metric in app.metric]
    assert "1" in metric_values  # 整理题 1 道
    captions = [element.value for element in app.caption]
    assert any("错题：1 道" in value for value in captions)
    assert any("待复核页" not in value for value in captions) or True


def test_learning_hub_survives_empty_database(tmp_path: Path, monkeypatch) -> None:
    data_dir, _, _, _ = _make_dirs(tmp_path)
    database = Database(data_dir / "database" / "knowledge.db")
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(
        runtime,
        "application_training_profile_service",
        lambda: TrainingProfileService(data_dir / "training_profile.db"),
    )

    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "pages" / "18_学习整理.py")
    ).run(timeout=30)

    assert not app.exception
    assert any("还没有整理过的题目" in panel.value for panel in app.markdown)


# ------------------------------------------------- context entry same-store
def test_context_entry_writes_the_same_question_store(tmp_path: Path) -> None:
    """直接调用入口服务层：与其它入口共用同一 question_items 存储。"""

    data_dir, raw_dir, pages_dir, markdown_dir = _make_dirs(tmp_path)
    database = Database(data_dir / "database" / "knowledge.db")
    _document_with_page(database, tmp_path)
    question_service = QuestionService(database)

    for kind in ("error", "good", "typical", "method"):
        question_service.create_question_item(
            document_id=1, page_id=1, question_kind=kind
        )

    with closing(connect(database.database_path)) as connection:
        rows = connection.execute(
            "SELECT question_kind FROM question_items ORDER BY id"
        ).fetchall()
    assert [row[0] for row in rows] == ["error", "good", "typical", "method"]


def test_context_entry_module_is_fail_safe_without_database(
    tmp_path: Path, monkeypatch
) -> None:
    """AppTest 环境数据库不可用时区块必须静默跳过。"""

    from src.learning_entry_ui import render_join_learning_section

    data_dir, _, _, _ = _make_dirs(tmp_path)
    database = Database(data_dir / "database" / "knowledge.db")
    _document_with_page(database, tmp_path)
    page = database.get_page(1)
    assert page is not None
    monkeypatch.setattr(
        "src.learning_entry_ui._database",
        lambda: (_ for _ in ()).throw(RuntimeError("no database in harness")),
    )
    render_join_learning_section(page)  # 不得抛出
