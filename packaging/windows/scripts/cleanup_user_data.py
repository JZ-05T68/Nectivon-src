"""Explicit, allowlisted removal of Nectivon current-user data."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

CONFIRMATION_PHRASE: Final[str] = "DELETE NECTIVON LOCAL DATA"
ALLOWED_CHILDREN: Final[frozenset[str]] = frozenset(
    {"data", "config", "backups", "logs", "runtime-state", "credentials"}
)


def expected_user_root(local_app_data: Path) -> Path:
    """Return the one permitted application root."""

    base = local_app_data.expanduser().resolve(strict=False)
    if base == Path(base.anchor):
        raise ValueError("LOCALAPPDATA cannot be a drive root")
    return base / "Nectivon"


def cleanup_targets(root: Path) -> tuple[Path, ...]:
    """Return exact direct children; never accept a caller-provided glob."""

    lexical = root.parent.resolve(strict=False) / root.name
    canonical = root.resolve(strict=False)
    if canonical != lexical:
        raise ValueError("Nectivon user root cannot be a symlink or junction")
    return tuple(canonical / name for name in sorted(ALLOWED_CHILDREN))


def validate_cleanup_target(target: Path, root: Path) -> Path:
    """Require an exact allowlisted direct child below the canonical root."""

    canonical_root = root.resolve(strict=False)
    canonical_target = target.resolve(strict=False)
    if canonical_target.parent != canonical_root:
        raise ValueError(f"cleanup target escapes Nectivon root: {target}")
    if canonical_target.name not in ALLOWED_CHILDREN:
        raise ValueError(f"cleanup target is not allowlisted: {target}")
    return canonical_target


def _stop_service(install_root: Path) -> None:
    python = install_root / "runtime" / "python" / "python.exe"
    manager = install_root / "scripts" / "service_manager.py"
    if python.is_file() and manager.is_file():
        subprocess.run([str(python), str(manager), "stop"], check=False)
        subprocess.run([str(python), str(manager), "disable-autostart"], check=False)


def _delete_windows_credentials(install_root: Path) -> None:
    if os.name != "nt":
        return
    sys.path.insert(0, str(install_root / "app"))
    from src.ai.credential_store import (  # noqa: PLC0415
        CredentialStoreError,
        WindowsCredentialManagerStore,
    )
    from src.ai.model_registry import ProviderId  # noqa: PLC0415

    try:
        store = WindowsCredentialManagerStore()
    except CredentialStoreError:
        return
    failures: list[str] = []
    for provider in ProviderId:
        try:
            store.delete(provider)
        except CredentialStoreError:
            failures.append(provider.value)
    if failures:
        raise RuntimeError("Could not verify credential removal: " + ", ".join(failures))


def delete_user_data(root: Path) -> tuple[Path, ...]:
    """Delete only validated direct children and return the removed paths."""

    removed: list[Path] = []
    for candidate in cleanup_targets(root):
        target = validate_cleanup_target(candidate, root)
        if target.is_symlink():
            target.unlink()
            removed.append(target)
        elif target.is_dir():
            shutil.rmtree(target)
            removed.append(target)
        elif target.exists():
            target.unlink()
            removed.append(target)
    return tuple(removed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Permanently delete Nectivon local data for the current user."
    )
    parser.add_argument("--confirm", default="")
    parser.add_argument("--confirm-root", default="")
    arguments = parser.parse_args()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        print("LOCALAPPDATA is unavailable; nothing was deleted.")
        return 2
    root = expected_user_root(Path(local_app_data))
    print("WARNING: this permanently deletes Nectivon knowledge data, imports, config, backups,")
    print("logs, runtime state, DPAPI fallback data, and four Provider credentials.")
    phrase = arguments.confirm or input(f'Type "{CONFIRMATION_PHRASE}" to continue: ')
    root_confirmation = arguments.confirm_root or input(f"Type the exact path {root}: ")
    if phrase != CONFIRMATION_PHRASE or Path(root_confirmation) != root:
        print("Confirmation did not match; nothing was deleted.")
        return 3

    install_root = Path(__file__).resolve().parents[1]
    _stop_service(install_root)
    _delete_windows_credentials(install_root)
    removed = delete_user_data(root)
    print(f"Removed {len(removed)} allowlisted Nectivon data locations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
