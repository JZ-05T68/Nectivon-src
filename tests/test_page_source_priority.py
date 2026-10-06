"""Direct-image transcripts win over historical OCR and AI summaries."""

from types import SimpleNamespace

import src.learning_entry_ui as entry_ui


def _page(ocr="", extracted="", manual=""):
    return SimpleNamespace(
        id=1, document_id=1, markdown_content=manual, ocr_text=ocr, extracted_text=extracted
    )


def _patch(monkeypatch, transcript=""):
    import src.page_image_ui as image_ui
    import src.runtime as runtime

    monkeypatch.setattr(
        runtime, "application_settings", lambda: SimpleNamespace(agent_readings_dir="unused")
    )
    reading = SimpleNamespace(transcript=transcript, model="qwen3.8-max") if transcript else None
    monkeypatch.setattr(image_ui, "current_image_reading", lambda page, root: reading)


def test_image_transcript_wins_even_when_ocr_is_longer(monkeypatch):
    _patch(monkeypatch, "直接读图原文 $x^{2}$")
    content, label, extra = entry_ui._resolve_page_source(_page(ocr="错误OCR" * 100))
    assert content == "直接读图原文 $x^{2}$" and "OCR" not in content
    assert label == "AI 直接读图文字" and extra["origin"] == "page_image_reading"


def test_old_ocr_alone_never_becomes_a_question_source(monkeypatch):
    _patch(monkeypatch)
    content, label, extra = entry_ui._resolve_page_source(_page(ocr="历史OCR"))
    assert content == "" and extra["origin"] == "none"
    assert "直接读图" in label


def test_ai_summary_is_not_an_original_transcript(monkeypatch):
    _patch(monkeypatch)
    content, label, extra = entry_ui._resolve_page_source(_page())
    assert content == "" and extra["origin"] == "none"


def test_native_text_layer_remains_available_for_manual_review(monkeypatch):
    _patch(monkeypatch)
    content, label, extra = entry_ui._resolve_page_source(_page(extracted="PDF原生文字层"))
    assert content == "PDF原生文字层" and label == "原文件文本层"


def test_manual_text_always_first(monkeypatch):
    _patch(monkeypatch, "原图文字")
    content, label, extra = entry_ui._resolve_page_source(_page(manual="人工校对文字"))
    assert content == "人工校对文字" and extra["origin"] == "user_manual_text"
