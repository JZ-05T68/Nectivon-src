"""Regression cover for the Office import MAX_PATH and diagnostics defects.

Two real defects are locked down here, both reachable from the production
``import_document`` path:

1. Path budget. ``MAX_STORED_STEM_LENGTH`` is 150 and the stored name is
   ``f"{sha256}_{stem}{suffix}"``, i.e. up to 220 characters. Under a normal
   ``raw/originals`` layout that pushes the full path past the Windows
   260-character MAX_PATH. Word/PowerPoint COM automation stays bound by
   MAX_PATH even when the system-wide ``LongPathsEnabled`` switch is on, while
   Python itself writes the longer path happily — so the original landed on
   disk intact, the conversion then died, and the user only saw "cannot read
   this Word or PowerPoint file".

2. Discarded diagnostics. ``OfficePdfConverter.convert`` checked
   ``completed.returncode`` but threw ``completed.stderr`` away, collapsing
   every distinct COM failure into one undiagnosable message.

Path arithmetic is asserted against synthetic directory strings so the budget
math does not depend on how deep the test runner's temp directory happens to
be. These tests never invoke real Microsoft Office: the converter is replaced
by a stub that writes a valid PDF, keeping the suite offline and deterministic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest import mock

import fitz
import pytest

from src.database import Database
from src.document_service import (
    MAX_STORED_STEM_LENGTH,
    DocumentImportError,
    DocumentService,
    _stem_budget,
)
from src.office_pdf_converter import (
    _CONVERSION_SCRIPT,
    OfficeConversionError,
    OfficePdfConverter,
)

WINDOWS_MAX_PATH = 260
#: sha256(64) + "_" + suffix + ".tmp-" + 12 hex digits
_TEMP_SIBLING_EXTRA = 17


class _PdfWritingConverter:
    """Stand-in that records the source path and emits a real 2-page PDF."""

    def __init__(self) -> None:
        self.sources: list[Path] = []
        self.outputs: list[Path] = []

    def convert(self, source_path: Path | str, output_path: Path | str) -> Path:
        source = Path(source_path)
        output = Path(output_path)
        self.sources.append(source)
        self.outputs.append(output)
        document = fitz.open()
        for page_number in (1, 2):
            page = document.new_page()
            page.insert_text((72, 72), f"Office import page {page_number}", fontsize=12)
        document.save(output)
        document.close()
        return output


class _FailingConverter:
    """Stand-in emulating a COM conversion that never produces a PDF."""

    def convert(self, source_path: Path | str, output_path: Path | str) -> Path:
        raise OfficeConversionError("本机没有完成文档转换。")


def _service(root: Path, converter: object | None = None) -> DocumentService:
    return DocumentService(
        database=Database(root / "data" / "database" / "knowledge.db"),
        raw_dir=root / "data" / "raw",
        pages_dir=root / "data" / "pages",
        markdown_dir=root / "data" / "markdown",
        office_converter=converter or _PdfWritingConverter(),  # type: ignore[arg-type]
    )


def _synthetic_originals(levels: int) -> Path:
    """Return an ``originals`` path for pure arithmetic; nothing is created."""

    root = Path("D:/Nectivon")
    for level in range(levels):
        root = root / f"深层中文资料目录第{level}层"
    return root / "data" / "raw" / "originals"


def _long_stem(length: int = MAX_STORED_STEM_LENGTH) -> str:
    return ("南工程电赛设计报告章节说明" * 40)[:length]


def _stored_stem(path: Path) -> str:
    """Strip the sha256 prefix off a stored name, leaving the clamped stem."""

    return path.stem.split("_", 1)[1]


# --------------------------------------------------------------- budget math


def test_stem_budget_never_exceeds_the_static_maximum() -> None:
    assert _stem_budget(Path("D:/shallow"), ".docx") <= MAX_STORED_STEM_LENGTH


def test_production_layout_keeps_a_generous_budget() -> None:
    """The shipped ``raw/originals`` layout must leave room for long titles."""

    limit = _stem_budget(Path("D:/Nectivon/data/raw/originals"), ".docx",
                         extra=_TEMP_SIBLING_EXTRA)
    assert limit >= 100


def test_stem_budget_shrinks_as_the_directory_gets_deeper() -> None:
    shallow = _stem_budget(_synthetic_originals(0), ".docx", extra=_TEMP_SIBLING_EXTRA)
    deep = _stem_budget(_synthetic_originals(8), ".docx", extra=_TEMP_SIBLING_EXTRA)
    assert deep < shallow
    assert deep >= 1


def test_stem_budget_accounts_for_the_atomic_rename_sibling() -> None:
    """The ``.tmp-<12 hex>`` sibling is longer than the final name."""

    directory = _synthetic_originals(4)
    plain = _stem_budget(directory, ".docx")
    with_sibling = _stem_budget(directory, ".docx", extra=_TEMP_SIBLING_EXTRA)
    assert with_sibling < plain
    assert plain - with_sibling == _TEMP_SIBLING_EXTRA


def test_stem_budget_raises_when_directory_leaves_no_room() -> None:
    """A hopeless directory must fail loudly, not emit an over-long path."""

    with pytest.raises(DocumentImportError, match="资料目录路径过长"):
        _stem_budget(_synthetic_originals(40), ".docx")


def test_original_path_stays_within_max_path(tmp_path: Path) -> None:
    service = _service(tmp_path)

    path = service._choose_original_path("0" * 64, _long_stem() + ".docx")
    temporary = path.parent / f"{path.name}.tmp-{'0' * 12}"

    assert len(str(path)) < WINDOWS_MAX_PATH
    assert len(str(temporary)) < WINDOWS_MAX_PATH


def test_raw_pdf_path_stays_within_max_path(tmp_path: Path) -> None:
    service = _service(tmp_path)

    path = service._choose_raw_path("0" * 64, _long_stem() + ".pdf")
    temporary = path.parent / f"{path.name}.tmp-{'0' * 12}"

    assert len(str(path)) < WINDOWS_MAX_PATH
    assert len(str(temporary)) < WINDOWS_MAX_PATH


# ------------------------------------------------------------ import end to end


def test_long_chinese_filename_imports_end_to_end(tmp_path: Path) -> None:
    """The exact shape that failed before the fix: long stem + deep originals."""

    converter = _PdfWritingConverter()
    service = _service(tmp_path, converter)
    filename = _long_stem() + ".docx"

    result = service.import_document(b"local original bytes", filename)

    assert result.document.page_count == 2
    assert [page.page_number for page in result.pages] == [1, 2]
    # The user-facing title keeps the full original stem; only storage clamps.
    assert result.document.title == _long_stem()

    original = converter.sources[0]
    assert original.parent.name == "originals"
    assert len(str(original)) < WINDOWS_MAX_PATH
    assert _long_stem().startswith(_stored_stem(original))

    converted = converter.outputs[0]
    assert converted.suffix.lower() == ".pdf"
    assert len(str(converted)) < WINDOWS_MAX_PATH


@pytest.mark.parametrize(
    "filename",
    ["学习计划.docx", "Nectivon Project Technical Description.docx", "答辩 稿.pptx"],
)
def test_short_filenames_keep_their_stem(tmp_path: Path, filename: str) -> None:
    converter = _PdfWritingConverter()
    service = _service(tmp_path, converter)

    result = service.import_document(b"bytes", filename)

    assert result.document.title == Path(filename).stem
    # Clamping is prefix-preserving, so a short name is never rewritten.
    assert Path(filename).stem.startswith(_stored_stem(converter.sources[0]))
    assert len(str(converter.sources[0])) < WINDOWS_MAX_PATH


def test_reimporting_the_same_office_file_is_not_blocked(tmp_path: Path) -> None:
    """Word's PDF export embeds a timestamp, so bytes differ between runs.

    That makes Office imports effectively non-deduplicating by content hash.
    This is pre-existing behavior, not something the path-budget fix changes;
    the contract locked down here is only that a second import still succeeds
    and keeps both originals instead of failing or overwriting.
    """

    service = _service(tmp_path, _PdfWritingConverter())

    service.import_document(b"same bytes", "技术说明书.docx")
    second = service.import_document(b"same bytes", "技术说明书.docx")

    assert second.document.page_count == 2
    originals = tmp_path / "data" / "raw" / "originals"
    assert len(list(originals.glob("*技术说明书.docx"))) == 1


# ------------------------------------------------------- failure containment


def test_conversion_failure_preserves_the_original(tmp_path: Path) -> None:
    service = _service(tmp_path, _FailingConverter())
    originals = tmp_path / "data" / "raw" / "originals"

    with pytest.raises(DocumentImportError, match="无法在本机读取"):
        service.import_document(b"precious user bytes", "技术说明书.docx")

    kept = list(originals.glob("*技术说明书.docx"))
    assert len(kept) == 1, "原文件必须安全保留"
    assert kept[0].read_bytes() == b"precious user bytes"


def test_conversion_failure_does_not_create_a_document_row(tmp_path: Path) -> None:
    service = _service(tmp_path, _FailingConverter())

    with pytest.raises(DocumentImportError):
        service.import_document(b"bytes", "技术说明书.docx")

    assert service.database.list_documents() == []


def test_conversion_failure_leaves_no_partial_pages(tmp_path: Path) -> None:
    service = _service(tmp_path, _FailingConverter())

    with pytest.raises(DocumentImportError):
        service.import_document(b"bytes", "技术说明书.docx")

    pages_dir = tmp_path / "data" / "pages"
    rendered = list(pages_dir.rglob("*.png")) if pages_dir.is_dir() else []
    assert rendered == []


# --------------------------------------------------------------- diagnostics


def test_conversion_failure_logs_the_discarded_child_stderr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The real COM failure text must reach the log, not just a generic message."""

    source = tmp_path / "技术说明书.docx"
    source.write_bytes(b"PK\x03\x04 real-ish bytes")
    output = tmp_path / "out.pdf"

    completed = mock.Mock(
        returncode=1,
        stdout="",
        stderr="New-Object : 类没有注册 (Exception from HRESULT: 0x80040154)",
    )
    with mock.patch(
        "src.office_pdf_converter.subprocess.run", return_value=completed
    ) as run:
        caplog.set_level(logging.ERROR, logger="src.office_pdf_converter")
        with pytest.raises(OfficeConversionError):
            OfficePdfConverter(timeout_seconds=5.0).convert(source, output)

    assert run.call_count == 1
    logged = caplog.text
    assert "0x80040154" in logged, "PowerShell stderr 必须被记录以便诊断"
    assert "returncode=1" in logged


def test_conversion_failure_logs_stdout_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "技术说明书.docx"
    source.write_bytes(b"PK\x03\x04 real-ish bytes")

    completed = mock.Mock(returncode=0, stdout="ExportAsFixedFormat 失败", stderr="")
    with mock.patch("src.office_pdf_converter.subprocess.run", return_value=completed):
        caplog.set_level(logging.ERROR, logger="src.office_pdf_converter")
        # returncode 0 but no PDF produced -> still a failure, still logged.
        with pytest.raises(OfficeConversionError):
            OfficePdfConverter(timeout_seconds=5.0).convert(source, tmp_path / "o.pdf")

    assert "ExportAsFixedFormat" in caplog.text


def test_conversion_success_stays_silent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "技术说明书.docx"
    source.write_bytes(b"PK\x03\x04 real-ish bytes")
    output = tmp_path / "out.pdf"

    def _fake_run(*_args: object, **_kwargs: object) -> mock.Mock:
        document = fitz.open()
        document.new_page()
        document.save(str(output))
        document.close()
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("src.office_pdf_converter.subprocess.run", side_effect=_fake_run):
        caplog.set_level(logging.ERROR, logger="src.office_pdf_converter")
        result = OfficePdfConverter(timeout_seconds=5.0).convert(source, output)

    assert result == output
    assert caplog.text == ""


def test_unsupported_extension_never_spawns_a_child_process(
    tmp_path: Path,
) -> None:
    source = tmp_path / "note.txt"
    source.write_text("hello", encoding="utf-8")

    with mock.patch("src.office_pdf_converter.subprocess.run") as run:
        with pytest.raises(OfficeConversionError, match="只支持 Word 或 PowerPoint"):
            OfficePdfConverter(timeout_seconds=5.0).convert(source, tmp_path / "o.pdf")

    assert run.call_count == 0


def test_conversion_script_forces_utf8_console_output() -> None:
    """PowerShell must emit UTF-8 or every Chinese COM error logs as mojibake.

    PowerShell writes to a redirected pipe using the console output encoding,
    which defaults to the system ANSI code page (GBK/936 on Chinese Windows),
    while the parent decodes with encoding="utf-8". Without this line the real
    cause — for example "文件可能已经损坏。" — reaches the log unreadable.
    """

    script = _CONVERSION_SCRIPT
    assert "[Console]::OutputEncoding" in script
    assert "UTF8" in script
    # The declaration must run before any COM call can fail and report.
    assert script.index("[Console]::OutputEncoding") < script.index("New-Object -ComObject")


def test_conversion_script_reads_paths_from_the_environment() -> None:
    """Paths travel via env vars, never inline, so quoting cannot break them."""

    script = _CONVERSION_SCRIPT
    assert "$env:EKB_OFFICE_CONVERT_INPUT" in script
    assert "$env:EKB_OFFICE_CONVERT_OUTPUT" in script
    assert "$env:EKB_OFFICE_CONVERT_KIND" in script


def test_child_environment_passes_conversion_paths_by_env(
    tmp_path: Path,
) -> None:
    source = tmp_path / "中文 空格 名称.docx"
    source.write_bytes(b"PK\x03\x04 real-ish bytes")
    output = tmp_path / "out.pdf"

    def _fake_run(*_args: object, **kwargs: object) -> mock.Mock:
        document = fitz.open()
        document.new_page()
        document.save(str(output))
        document.close()
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("src.office_pdf_converter.subprocess.run", side_effect=_fake_run) as run:
        OfficePdfConverter(timeout_seconds=5.0).convert(source, output)

    passed_env = run.call_args.kwargs["env"]
    assert passed_env["EKB_OFFICE_CONVERT_INPUT"] == str(source.resolve())
    assert passed_env["EKB_OFFICE_CONVERT_OUTPUT"] == str(output.resolve())
    assert passed_env["EKB_OFFICE_CONVERT_KIND"] == "word"
