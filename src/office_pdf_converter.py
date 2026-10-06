"""Local Microsoft Office to PDF conversion for user-imported documents.

The converter uses the installed desktop Word/PowerPoint applications through
their local COM automation interfaces.  It never sends a document over the
network and never edits the uploaded original.  PDF remains the one page-level
ingestion format used by the page-image recognition and citation pipeline.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Final

LOGGER = logging.getLogger(__name__)


class OfficeConversionError(RuntimeError):
    """Raised when a Word or PowerPoint document cannot be converted locally."""


WORD_EXTENSIONS: Final[frozenset[str]] = frozenset({".doc", ".docx"})
POWERPOINT_EXTENSIONS: Final[frozenset[str]] = frozenset({".ppt", ".pptx"})
OFFICE_EXTENSIONS: Final[frozenset[str]] = WORD_EXTENSIONS | POWERPOINT_EXTENSIONS

_CONVERSION_SCRIPT: Final[str] = r"""
# PowerShell writes to a redirected pipe using the console output encoding,
# which defaults to the system ANSI code page (GBK/936 on Chinese Windows).
# The parent decodes with encoding="utf-8", so without this line every Chinese
# COM error reaches the log as mojibake and the diagnostics are useless.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$InputPath = $env:EKB_OFFICE_CONVERT_INPUT
$OutputPath = $env:EKB_OFFICE_CONVERT_OUTPUT
$Kind = $env:EKB_OFFICE_CONVERT_KIND
if ([string]::IsNullOrWhiteSpace($InputPath) -or
    [string]::IsNullOrWhiteSpace($OutputPath) -or
    $Kind -notin @('word', 'powerpoint')) {
    throw 'Invalid local conversion input.'
}
$application = $null
$document = $null
$usedWps = $false
try {
    if ($Kind -eq 'word') {
        try {
            $application = New-Object -ComObject Word.Application
        } catch {
            # WPS Office ( widespread on machines without Microsoft
            # Office ) exposes a Word-compatible COM interface through
            # KWPS.Application; without this fallback every Word import
            # fails on such machines (F7 real-user complaint).
            $application = New-Object -ComObject KWPS.Application
            $usedWps = $true
        }
        $application.Visible = $false
        $application.DisplayAlerts = 0
        $document = $application.Documents.Open($InputPath, $false, $true)
        $document.ExportAsFixedFormat($OutputPath, 17)
    } else {
        try {
            $application = New-Object -ComObject PowerPoint.Application
        } catch {
            $application = New-Object -ComObject KWPP.Application
            $usedWps = $true
        }
        $document = $application.Presentations.Open($InputPath, $true, $false, $false)
        $document.SaveAs($OutputPath, 32)
    }
} finally {
    if ($null -ne $document) {
        if ($Kind -eq 'word') { $document.Close(0) } else { $document.Close() }
    }
    if ($null -ne $application) { $application.Quit() }
}
Write-Output ("EKB_CONVERT_ENGINE=" + $(if ($usedWps) { 'wps' } else { 'msoffice' }))
"""


class OfficePdfConverter:
    """Convert one local Word/PowerPoint file to a page-preserving PDF."""

    def __init__(self, *, timeout_seconds: float = 180.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self._timeout_seconds = timeout_seconds

    def convert(self, source_path: Path | str, output_path: Path | str) -> Path:
        """Convert ``source_path`` into ``output_path`` and verify the result."""

        source = Path(source_path).resolve(strict=True)
        output = Path(output_path).resolve(strict=False)
        extension = source.suffix.lower()
        if extension in WORD_EXTENSIONS:
            kind = "word"
        elif extension in POWERPOINT_EXTENSIONS:
            kind = "powerpoint"
        else:
            raise OfficeConversionError("只支持 Word 或 PowerPoint 文件。")
        if output.suffix.lower() != ".pdf":
            raise OfficeConversionError("转换结果必须是 PDF 文件。")
        if source == output:
            raise OfficeConversionError("原文件和转换结果不能使用同一路径。")

        output.parent.mkdir(parents=True, exist_ok=True)
        powershell = _powershell_path()
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        child_environment = {
            key: value
            for key in (
                "SystemRoot",
                "TEMP",
                "TMP",
                "PATH",
                "PATHEXT",
                "ComSpec",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
            )
            if (value := os.environ.get(key))
        }
        child_environment.update(
            {
                "EKB_OFFICE_CONVERT_INPUT": str(source),
                "EKB_OFFICE_CONVERT_OUTPUT": str(output),
                "EKB_OFFICE_CONVERT_KIND": kind,
            }
        )
        try:
            completed = subprocess.run(
                (
                    str(powershell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    _CONVERSION_SCRIPT,
                ),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                creationflags=creation_flags,
                env=child_environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OfficeConversionError(
                "本机没有完成文档转换，请确认 Microsoft Office 或 WPS Office"
                " 可以正常打开该文件。"
            ) from exc
        if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            # The child's stderr is the ONLY place the real COM failure text
            # lives (for example a protected-view dialog, a locked file, a path
            # past MAX_PATH, or "Word is not installed"). Discarding it turned
            # every distinct cause into the same undiagnosable message, so keep
            # it in the log while the user still gets the friendly wording.
            LOGGER.error(
                "Office 转换子进程失败：kind=%s returncode=%s output_exists=%s "
                "output_bytes=%s\n--- powershell stdout ---\n%s\n"
                "--- powershell stderr ---\n%s",
                kind,
                completed.returncode,
                output.is_file(),
                output.stat().st_size if output.is_file() else -1,
                (completed.stdout or "").strip() or "<empty>",
                (completed.stderr or "").strip() or "<empty>",
            )
            raise OfficeConversionError(
                "本机没有完成文档转换，请确认文件未损坏，且已安装"
                " Microsoft Office 或 WPS Office。"
            )
        return output


def _powershell_path() -> Path:
    """Return the fixed local Windows PowerShell executable path."""

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not candidate.is_file():
        raise OfficeConversionError("找不到本机文档转换组件。")
    return candidate


__all__ = [
    "OFFICE_EXTENSIONS",
    "OfficeConversionError",
    "OfficePdfConverter",
    "POWERPOINT_EXTENSIONS",
    "WORD_EXTENSIONS",
]
