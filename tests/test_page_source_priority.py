"""Geography G1: page-source priority must prefer verbatim text over AI paraphrase."""

from __future__ import annotations

from types import SimpleNamespace

import src.learning_entry_ui as entry_ui


class _FakeAgentStore:
    def __init__(self, summary: str) -> None:
        self._summary = summary

    def page_reading(self, page_id: int):
        if self._summary == "__none__":
            return None
        return SimpleNamespace(summary=self._summary)


class _FakeVisualService:
    def get_page_visual_state(self, page_id: int):
        return {"interpretations": []}


def _page(ocr: str = "", extracted: str = "", manual: str = ""):
    return SimpleNamespace(
        id=1,
        document_id=1,
        markdown_content=manual,
        ocr_text=ocr,
        extracted_text=extracted,
    )


def _patch(monkeypatch, summary: str) -> None:
    # The imports happen inside the function, so patch the source modules.
    import src.agent_document_reader as adr
    import src.runtime as rt

    monkeypatch.setattr(adr, "AgentReadingStore", lambda _dir: _FakeAgentStore(summary))
    monkeypatch.setattr(
        rt, "application_settings", lambda: SimpleNamespace(agent_readings_dir="x")
    )
    monkeypatch.setattr(
        rt, "application_page_visual_service", lambda: _FakeVisualService()
    )


def test_short_agent_summary_loses_to_longer_ocr(monkeypatch) -> None:
    """Scan-heavy geography pages: verbatim OCR beats a condensed AI summary."""

    _patch(monkeypatch, "考查甲地区人口密度低的主要原因。")
    ocr = "1. 甲地区人口密度低的主要原因 A. 地势较高 B. 台风多发 C. 滩涂广布 D. 淡水短缺 " * 3
    content, label, extra = entry_ui._resolve_page_source(_page(ocr=ocr))
    assert label == "系统识别出的文字"
    assert "滩涂广布" in content
    assert extra["origin"] == "page_ocr_text"


def test_agent_summary_kept_when_ocr_missing(monkeypatch) -> None:
    _patch(monkeypatch, "Agent 对本页的完整阅读摘要，没有 OCR 时仍是有效来源。")
    content, label, extra = entry_ui._resolve_page_source(_page())
    assert label == "Agent 阅读结果"
    assert extra["origin"] == "agent_page_reading"


def test_agent_summary_kept_when_verbatim_is_tiny(monkeypatch) -> None:
    summary = "完整摘要：第 1 题考查人口密度影响因素，涵盖地势、台风、滩涂、淡水。"
    _patch(monkeypatch, summary)
    content, label, extra = entry_ui._resolve_page_source(_page(ocr="甲地区 人口密度"))
    assert label == "Agent 阅读结果"


def test_extracted_text_used_when_no_ocr(monkeypatch) -> None:
    _patch(monkeypatch, "短摘要")
    content, label, extra = entry_ui._resolve_page_source(
        _page(extracted="PDF 文本层完整原文，包含第 1 题的全部题干与四个选项内容。")
    )
    assert label == "页面文本"
    assert "四个选项" in content


def test_manual_text_always_first(monkeypatch) -> None:
    _patch(monkeypatch, "任何摘要")
    content, label, extra = entry_ui._resolve_page_source(_page(manual="人工校对过的文字"))
    assert label == "人工校对文字"
    assert extra["origin"] == "user_manual_text"
