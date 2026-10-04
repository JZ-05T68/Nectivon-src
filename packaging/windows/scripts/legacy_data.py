"""Report an explicitly supplied legacy data root without migrating anything."""

from __future__ import annotations

import argparse
from pathlib import Path


def legacy_data_status(path: Path) -> str:
    """Return a status only for the exact caller-supplied location."""

    candidate = path.expanduser().resolve(strict=False)
    database = candidate / "database" / "knowledge.db"
    return "LEGACY_DATA_FOUND" if database.is_file() else "LEGACY_DATA_NOT_FOUND"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check one explicitly registered legacy Nectivon data location."
    )
    parser.add_argument("legacy_root", type=Path)
    arguments = parser.parse_args()
    print(legacy_data_status(arguments.legacy_root))
    print("NO_DATA_WAS_COPIED_OR_MOVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
