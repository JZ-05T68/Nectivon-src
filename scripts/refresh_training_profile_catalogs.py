"""Rebuild Phase-1 school and undergraduate-major catalogs from official files.

Usage (maintenance only)::

    python scripts/refresh_training_profile_catalogs.py \
        --schools-xls tmp/official/2026普通高校.xls \
        --majors-text tmp/official/本科专业目录2026.txt

The school workbook is the Ministry of Education's national list dated
2026-06-17.  The major text is produced with ``pdftotext -layout`` from the
Ministry's 2026 undergraduate-major catalog.  This script is not used at
application runtime and never downloads files by itself.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import xlrd

SCHOOL_SOURCE_URL = (
    "https://www.moe.gov.cn/jyb_xxgk/s5743/s5744/202606/"
    "W020260618416094865984.xls"
)
MAJOR_SOURCE_URL = (
    "https://www.moe.gov.cn/srcsite/A08/moe_1034/s3882/202604/"
    "W020260427440749576927.pdf"
)


def parse_school_names(path: Path) -> list[str]:
    """Extract serial-numbered institution names from the official workbook."""

    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    names: list[str] = []
    for row_index in range(sheet.nrows):
        serial = sheet.cell_value(row_index, 0)
        name = str(sheet.cell_value(row_index, 1)).strip()
        if isinstance(serial, (int, float)) and name:
            names.append(name)
    return names


def parse_undergraduate_major_names(path: Path) -> list[str]:
    """Extract six-digit major rows from a layout-preserving PDF text export."""

    pattern = re.compile(r"^\s*\d{6}[TK]*\s+(.+?)\s*$")
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        name = match.group(1).split("（注：", 1)[0].strip()
        if name:
            names.append(name)
    return names


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schools-xls", type=Path, required=True)
    parser.add_argument("--majors-text", type=Path, required=True)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "data",
    )
    args = parser.parse_args()

    schools = parse_school_names(args.schools_xls)
    majors = parse_undergraduate_major_names(args.majors_text)

    if len(schools) != 2952 or len(set(schools)) != len(schools):
        raise ValueError(f"Official school parse mismatch: {len(schools)} rows")
    if len(majors) != 875 or len(set(majors)) != len(majors):
        raise ValueError(f"Official major parse mismatch: {len(majors)} rows")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.data_dir / "schools.json", schools)
    write_json(args.data_dir / "undergraduate_majors.json", majors)
    write_json(
        args.data_dir / "training_profile_catalog_sources.json",
        {
            "schools": {
                "title": "全国普通高等学校名单（截至2026年6月17日）",
                "source": SCHOOL_SOURCE_URL,
                "count": len(schools),
            },
            "undergraduate_majors": {
                "title": "普通高等学校本科专业目录（2026年）",
                "source": MAJOR_SOURCE_URL,
                "count": len(majors),
            },
        },
    )
    print(f"schools={len(schools)} majors={len(majors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
