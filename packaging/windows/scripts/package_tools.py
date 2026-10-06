"""Deterministic inventory, manifest, and WiX authoring helpers."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

WIX_NAMESPACE: Final[str] = "http://wixtoolset.org/schemas/v4/wxs"
BANNED_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        ".workbuddy",
        "artifacts",
        "backups",
        "build",
        "data",
        "dist",
        "experiments",
        "logs",
        "staging-data",
        "tests",
        "tmp",
    }
)
BANNED_ANY_PART: Final[frozenset[str]] = frozenset(
    {".git", ".venv", ".workbuddy", "tests", "__pycache__"}
)
BANNED_FILENAMES: Final[frozenset[str]] = frozenset({".env", "knowledge.db"})
REQUIRED_FILES: Final[tuple[str, ...]] = (
    "VERSION",
    "app/app.py",
    "app/src/__init__.py",
    "runtime/python/python.exe",
    "runtime/python/pythonw.exe",
    "runtime/python/msvcp140.dll",
    "runtime/python/msvcp140_1.dll",
    "scripts/service_manager.py",
    "scripts/launch.vbs",
    "scripts/cleanup_user_data.py",
    "scripts/uninstall_stop.py",
)


def sha256_file(path: Path) -> str:
    """Hash a file without loading a large native wheel into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> tuple[Path, ...]:
    """Return all staged files in stable relative-path order."""

    canonical = root.resolve(strict=True)
    return tuple(
        sorted(
            (path.relative_to(canonical) for path in canonical.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix().casefold(),
        )
    )


def prune_vendor_development_files(root: Path) -> tuple[Path, ...]:
    """Remove only caches and test trees inside the staged site-packages root."""

    canonical = root.resolve(strict=True)
    site_packages = (canonical / "runtime/python/Lib/site-packages").resolve(strict=True)
    if not site_packages.is_relative_to(canonical):
        raise ValueError("site-packages escapes staging root")
    removed: list[Path] = []
    directories = sorted(
        (
            path
            for path in site_packages.rglob("*")
            if path.is_dir() and path.name.casefold() in {"tests", "test", "__pycache__"}
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        resolved = directory.resolve(strict=False)
        if not resolved.is_relative_to(site_packages):
            raise ValueError(f"refusing to prune outside site-packages: {directory}")
        if directory.exists():
            shutil.rmtree(directory)
            removed.append(directory.relative_to(canonical))
    for suffix in ("*.pyc", "*.pyo"):
        for path in site_packages.rglob(suffix):
            path.unlink()
            removed.append(path.relative_to(canonical))
    return tuple(removed)


def validate_staging(root: Path) -> tuple[str, ...]:
    """Return all package-policy violations without mutating the staging tree."""

    canonical = root.resolve(strict=True)
    errors: list[str] = []
    for required in REQUIRED_FILES:
        if not (canonical / required).is_file():
            errors.append(f"missing required file: {required}")
    version_file = canonical / "VERSION"
    if version_file.is_file() and version_file.read_text(encoding="utf-8").strip() != "0.8.5":
        errors.append("VERSION must be 0.8.5")
    for relative in inventory(canonical):
        folded_parts = {part.casefold() for part in relative.parts}
        top_level = relative.parts[0].casefold()
        if top_level in BANNED_TOP_LEVEL or folded_parts & BANNED_ANY_PART:
            errors.append(f"banned path in package: {relative.as_posix()}")
        if relative.name.casefold() in BANNED_FILENAMES:
            errors.append(f"banned file in package: {relative.as_posix()}")
        if relative.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
            errors.append(f"user database candidate in package: {relative.as_posix()}")
        if "rapidfuzz" in relative.as_posix().casefold():
            errors.append(f"unused rapidfuzz dependency in package: {relative.as_posix()}")
    return tuple(errors)


def write_manifest(
    root: Path,
    output: Path,
    *,
    source_git_sha: str,
    source_tree_dirty: bool,
    dependency_lock_sha256: str,
    msvc_runtime_version: str,
    msvc_redist_sha256: str,
    timestamp: str | None = None,
) -> dict[str, object]:
    """Write a traceable runtime manifest with per-file SHA-256 hashes."""

    canonical = root.resolve(strict=True)
    output_resolved = output.resolve(strict=False)
    files = []
    for relative in inventory(canonical):
        path = canonical / relative
        if path.resolve(strict=False) == output_resolved:
            continue
        files.append(
            {
                "path": relative.as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest: dict[str, object] = {
        "version": "0.8.5",
        "platform": "windows",
        "arch": "x86_64",
        "python_version": "3.11.9",
        "dependency_lock_sha256": dependency_lock_sha256,
        "msvc_runtime_version": msvc_runtime_version,
        "msvc_redist_sha256": msvc_redist_sha256,
        "source_git_sha": source_git_sha,
        "source_tree_dirty": source_tree_dirty,
        "build_timestamp": timestamp or datetime.now(UTC).isoformat(),
        "signing_state": "UNSIGNED_ENGINEERING_BUILD",
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def smoke_bundled_python(root: Path) -> None:
    """Exercise the actual staged interpreter, import graph, and native wheels."""

    canonical = root.resolve(strict=True)
    runtime = canonical / "runtime/python"
    pth = runtime / "python311._pth"
    expected_pth = [
        "python311.zip",
        ".",
        r"Lib\site-packages",
        r"..\..\app",
        "import site",
    ]
    actual_pth = pth.read_text(encoding="ascii").splitlines()
    if actual_pth != expected_pth:
        raise RuntimeError(f"unexpected python311._pth: {actual_pth!r}")

    expected_paths = {
        (runtime / "python311.zip").resolve(),
        runtime.resolve(),
        (runtime / "Lib/site-packages").resolve(),
        (canonical / "app").resolve(),
    }
    actual_paths = {Path(item).resolve() for item in sys.path}
    if not expected_paths.issubset(actual_paths):
        raise RuntimeError(
            "bundled sys.path is incomplete: "
            + json.dumps(
                [str(path) for path in sorted(actual_paths, key=str)], ensure_ascii=False
            )
        )
    if any(".venv" in str(path).casefold() for path in actual_paths):
        raise RuntimeError("bundled sys.path leaked the development .venv")

    modules = (
        "streamlit",
        "fitz",
        "PIL",
        "cv2",
        "jieba",
        "pydantic",
        "pydantic_settings",
    )
    imported = {name: importlib.import_module(name) for name in modules}

    app_local_runtime = (
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
    )
    for dll_name in app_local_runtime:
        dll_path = runtime / dll_name
        if not dll_path.is_file():
            raise RuntimeError(f"missing app-local MSVC runtime: {dll_name}")
        ctypes.WinDLL(str(dll_path))

    fitz = imported["fitz"]
    document = fitz.open()
    document.new_page(width=32, height=32)
    if document.page_count != 1:
        raise RuntimeError("PyMuPDF native smoke failed")
    importlib.import_module("PIL.Image").new("RGB", (2, 2), "white").getpixel((0, 0))
    cv2 = imported["cv2"]
    if not cv2.getBuildInformation():
        raise RuntimeError("OpenCV native build information unavailable")

    native_files = tuple(
        path
        for path in (runtime / "Lib/site-packages").rglob("*")
        if path.is_file() and path.suffix.casefold() in {".pyd", ".dll"}
    )
    if not native_files:
        raise RuntimeError("no native extension or DLL was installed")

    print("BUNDLED_PYTHON_IMPORT_SMOKE = PASS")
    print(f"BUNDLED_PYTHON_EXECUTABLE = {sys.executable}")
    print("BUNDLED_PYTHON_SYS_PATH = " + json.dumps(sys.path, ensure_ascii=False))
    print("BUNDLED_IMPORTS = " + ",".join(modules))
    print(f"NATIVE_EXTENSION_OR_DLL_COUNT = {len(native_files)}")
    print("NATIVE_RUNTIME_DEPENDENCY_AUDIT = PASS")
    print("APP_LOCAL_MSVC_RUNTIME = " + ",".join(app_local_runtime))


def smoke_staged_app(root: Path) -> None:
    """Run the staged home page with Streamlit's fake UI harness."""

    from streamlit.testing.v1 import AppTest

    canonical = root.resolve(strict=True)
    app = AppTest.from_file(str(canonical / "app/app.py")).run(timeout=30)
    if app.exception:
        raise RuntimeError(f"staged home page raised: {app.exception}")
    rendered = "\n".join(
        str(element.value)
        for group in (app.title, app.header, app.subheader, app.markdown, app.caption)
        for element in group
    )
    if "Nectivon" not in rendered:
        raise RuntimeError("staged home page did not render the Nectivon brand")
    print("STAGED_HOMEPAGE_BRAND_SMOKE = PASS")


def _identifier(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def write_wix_files(root: Path, output: Path) -> None:
    """Generate deterministic WiX components for the validated staging tree."""

    canonical = root.resolve(strict=True)
    ET.register_namespace("", WIX_NAMESPACE)
    wix = ET.Element(f"{{{WIX_NAMESPACE}}}Wix")
    directory_fragment = ET.SubElement(wix, f"{{{WIX_NAMESPACE}}}Fragment")
    root_ref = ET.SubElement(
        directory_fragment,
        f"{{{WIX_NAMESPACE}}}DirectoryRef",
        {"Id": "INSTALLFOLDER"},
    )
    directories: dict[Path, str] = {Path(): "INSTALLFOLDER"}
    elements: dict[Path, ET.Element] = {Path(): root_ref}
    parent_paths = sorted(
        {parent for item in inventory(canonical) for parent in item.parents if parent != Path(".")},
        key=lambda path: (len(path.parts), path.as_posix().casefold()),
    )
    for relative in parent_paths:
        identifier = _identifier("dir", relative.as_posix())
        directories[relative] = identifier
        elements[relative] = ET.SubElement(
            elements[relative.parent],
            f"{{{WIX_NAMESPACE}}}Directory",
            {"Id": identifier, "Name": relative.name},
        )

    component_fragment = ET.SubElement(wix, f"{{{WIX_NAMESPACE}}}Fragment")
    group = ET.SubElement(
        component_fragment,
        f"{{{WIX_NAMESPACE}}}ComponentGroup",
        {"Id": "ApplicationFiles"},
    )
    for relative in inventory(canonical):
        value = relative.as_posix()
        component = ET.SubElement(
            group,
            f"{{{WIX_NAMESPACE}}}Component",
            {
                "Id": _identifier("cmp", value),
                "Directory": directories[relative.parent],
                "Guid": "*",
            },
        )
        ET.SubElement(
            component,
            f"{{{WIX_NAMESPACE}}}File",
            {
                "Id": _identifier("fil", value),
                "Source": str(canonical / relative),
                "KeyPath": "yes",
            },
        )
    ET.indent(wix, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(wix).write(output, encoding="utf-8", xml_declaration=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("root", type=Path)
    prune = subparsers.add_parser("prune")
    prune.add_argument("root", type=Path)
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("root", type=Path)
    manifest.add_argument("output", type=Path)
    manifest.add_argument("--source-git-sha", required=True)
    manifest.add_argument(
        "--source-tree-dirty", choices=("true", "false"), required=True
    )
    manifest.add_argument("--dependency-lock-sha256", required=True)
    manifest.add_argument("--msvc-runtime-version", required=True)
    manifest.add_argument("--msvc-redist-sha256", required=True)
    wix = subparsers.add_parser("wix")
    wix.add_argument("root", type=Path)
    wix.add_argument("output", type=Path)
    runtime_smoke = subparsers.add_parser("runtime-smoke")
    runtime_smoke.add_argument("root", type=Path)
    app_smoke = subparsers.add_parser("app-smoke")
    app_smoke.add_argument("root", type=Path)
    arguments = parser.parse_args()

    if arguments.command == "validate":
        errors = validate_staging(arguments.root)
        for error in errors:
            print(error)
        return 1 if errors else 0
    if arguments.command == "prune":
        for removed in prune_vendor_development_files(arguments.root):
            print(f"pruned {removed.as_posix()}")
        return 0
    if arguments.command == "manifest":
        write_manifest(
            arguments.root,
            arguments.output,
            source_git_sha=arguments.source_git_sha,
            source_tree_dirty=arguments.source_tree_dirty == "true",
            dependency_lock_sha256=arguments.dependency_lock_sha256,
            msvc_runtime_version=arguments.msvc_runtime_version,
            msvc_redist_sha256=arguments.msvc_redist_sha256,
        )
        return 0
    if arguments.command == "wix":
        write_wix_files(arguments.root, arguments.output)
        return 0
    if arguments.command == "runtime-smoke":
        smoke_bundled_python(arguments.root)
        return 0
    if arguments.command == "app-smoke":
        smoke_staged_app(arguments.root)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
