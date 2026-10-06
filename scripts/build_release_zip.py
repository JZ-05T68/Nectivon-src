"""Build the deterministic Nectivon v0.8.6 release archive."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "Nectivon_v0.8.6_release.zip"
ARCHIVE_ROOT = "Nectivon_v0.8.6"
ZIP_TIMESTAMP = (2026, 10, 1, 0, 0, 0)

EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".workbuddy",
    "__pycache__",
    "artifacts",
    "build",
    "cache",
    "dist",
    "htmlcov",
    "logs",
    "runtime",
    "scratch",
    "tmp",
}

EXCLUDED_FILE_SUFFIXES = {
    ".bak",
    ".log",
    ".orig",
    ".pyc",
    ".pyo",
    ".rej",
    ".tmp",
}

EXCLUDED_FILE_NAMES = {
    ".coverage",
    ".zcodeignore",
    "Nectivon_v0.8.6_release.zip",
}

TOP_LEVEL_INCLUDE_DIRS = (
    "src",
    "pages",
    "tests",
    "scripts",
    "requirements",
    "templates",
    ".streamlit",
    "benchmarks",
    "packaging",
)

EMPTY_RUNTIME_DIRS = (
    "data",
    "staging-data",
    "staging-data-8512",
)

TOP_LEVEL_INCLUDE_FILES = (
    "app.py",
    "AGENTS.md",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    "Dockerfile",
    "CHANGELOG.md",
    "_nectivon_launcher.cmd",
    "requirements.txt",
    "requirements-hosted.txt",
    "pyproject.toml",
    "uv.lock",
    ".env.example",
    "README.md",
    "README_EN.md",
    "README_JP.md",
    "启动正式版.bat",
    "静默启动Nectivon.vbs",
    "stop_release.bat",
    "启动测试版8511.bat",
    "停止测试版8511.bat",
    "启动测试版8512.bat",
    "停止测试版8512.bat",
    "check_environment.bat",
    "run_all_tests.bat",
)


def should_skip(path: Path) -> bool:
    """Return whether a source path is transient or unsafe to ship."""

    relative = path.relative_to(PROJECT_ROOT).as_posix()
    if relative in {
        "src/data/schools.json",
        "src/data/undergraduate_majors.json",
        "src/data/training_profile_catalog_sources.json",
    }:
        return True
    if path.name == "secrets.toml" or (
        path.name.startswith(".env") and path.name != ".env.example"
    ):
        return True
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
        return True
    if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return True
    if path.name in EXCLUDED_FILE_NAMES or path.name.endswith(".pid.json"):
        return True
    return False


def _write_bytes(zf: zipfile.ZipFile, arcname: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(arcname, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    zf.writestr(info, payload, compresslevel=9)


def _write_directory(zf: zipfile.ZipFile, arcname: str) -> None:
    info = zipfile.ZipInfo(arcname.rstrip("/") + "/", ZIP_TIMESTAMP)
    info.external_attr = (0o40755 << 16) | 0x10
    zf.writestr(info, b"")


def _release_files() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    missing: list[str] = []
    for filename in TOP_LEVEL_INCLUDE_FILES:
        file_path = PROJECT_ROOT / filename
        if not file_path.is_file():
            missing.append(filename)
        else:
            files.append((file_path, f"{ARCHIVE_ROOT}/{filename}"))
    for dirname in TOP_LEVEL_INCLUDE_DIRS:
        dir_path = PROJECT_ROOT / dirname
        if not dir_path.is_dir():
            missing.append(dirname + "/")
            continue
        for item in sorted(dir_path.rglob("*"), key=lambda value: value.as_posix()):
            if item.is_file() and not should_skip(item):
                relative = item.relative_to(PROJECT_ROOT).as_posix()
                files.append((item, f"{ARCHIVE_ROOT}/{relative}"))
    if missing:
        raise FileNotFoundError("Release inputs are missing: " + ", ".join(missing))
    return sorted(files, key=lambda item: item[1])


def build_zip(output: Path) -> Path:
    """Write a clean archive without any mutable user or test data."""

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    files = _release_files()
    total_uncompressed_bytes = 0
    with zipfile.ZipFile(output, "w") as zf:
        for dirname in EMPTY_RUNTIME_DIRS:
            _write_directory(zf, f"{ARCHIVE_ROOT}/{dirname}")
        for file_path, arcname in files:
            payload = file_path.read_bytes()
            _write_bytes(zf, arcname, payload)
            total_uncompressed_bytes += len(payload)
    print(f"Created: {output}")
    print(f"Files: {len(files)}")
    print(f"Uncompressed: {total_uncompressed_bytes / (1024 * 1024):.2f} MB")
    print(f"Archive: {output.stat().st_size / (1024 * 1024):.2f} MB")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Nectivon v0.8.6 release zip")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    build_zip(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
