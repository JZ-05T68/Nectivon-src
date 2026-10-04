"""Corpus versioning management for the 8511 staging instance.

Minimal CORPUS VERSIONING (morning-closure §5): eval cases carry a
``corpus_ref``; manifests carry a version id. This tool moves the staging
corpus between the two registered versions:

- ``v0.8-frozen-corpus-v1``  8 docs / 17 memories  (BEFORE/AFTER comparability)
- ``dirty-long-term-v1``    12 docs / 24 memories  (DIRTY_LONG_TERM V1)

Stages:
  --snapshot-dirty    write corpus_manifest_dirty_v2.json (12 docs / 24 mem)
  --restore-frozen    delete the 4 dirty docs (quarantined, recoverable) and
                      the 7 dirty memories, returning to the frozen state
  --reapply-dirty     regenerate/import dirty docs, reseed memories, re-read

Deletion uses the application's own DocumentDeletionService (staged,
title-confirmed, quarantine + recovery manifest) — fixtures only, staging
only, never the formal instance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ["EKB_STAGING_INSTANCE"] = "1"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "reliability_eval"
DIRTY_MANIFEST = FIXTURE_DIR / "corpus_manifest_dirty_v2.json"

DIRTY_DOC_TITLES = (
    "泵维护视觉手册_v2.0",
    "冷却水泵性能图表",  # same-name different-content import (id 13)
    "变频器OC报警排查记录",
    "空压机完整维护手册",
)
DIRTY_MEMORY_TITLES = (
    "冷却水泵流量排序速查（二车间 2026 复测）",
    "OC 报警优先查制动回路（复测中）",
    "高粉尘车间空压机维护周期缩短规定",
    "泵手册版本基线（已被 2.0 草案跟进）",
    "空压机联轴器对中标准（长手册摘要）",
    "M8 地脚螺栓最大力矩是多少？",
    "M8 螺栓最多能拧到多少牛·米？",
)

# 冷却水泵性能图表 exists twice after the dirty import (original id 8 + dirty
# id 13). Only the SECOND one is removed on restore.
SAME_NAME_KEEP_DOC_ID = 8


def _settings():
    from src.config import staging_settings

    return staging_settings()


def _database():
    from src.database import Database

    return Database(_settings().database_path)


def _memory_titles(database) -> set[str]:
    import sqlite3

    connection = sqlite3.connect(f"file:{_settings().database_path}?mode=ro", uri=True)
    return {
        row[0] for row in connection.execute(
            "SELECT title FROM knowledge_memory_entries"
        )
    }


def snapshot_dirty() -> int:
    database = _database()
    docs = [
        {"title": d.title, "sha256": d.sha256}
        for d in sorted(database.list_documents(), key=lambda item: item.id)
    ]
    present = {item["title"] for item in docs}
    expected = sorted(DIRTY_DOC_TITLES)
    missing = [t for t in expected if t not in present]
    if missing:
        print("脏文档缺失，快照拒绝：", "、".join(missing))
        return 2
    manifest = {
        "manifest_id": "dirty-long-term-v1",
        "frozen_at": datetime.now(UTC).isoformat(),
        "database_path_expected": str(_settings().database_path),
        "documents": docs,
        "knowledge_memory_entry_count": 24,
    }
    DIRTY_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"dirty manifest written: {DIRTY_MANIFEST.name} ({len(docs)} docs)")
    return 0


def restore_frozen() -> int:
    from src.document_deletion_service import DocumentDeletionService
    from src.knowledge_memory_service import KnowledgeMemoryService

    settings = _settings()
    database = _database()
    service = DocumentDeletionService(
        database=database,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        data_dir=settings.data_dir,
        agent_readings_dir=settings.agent_readings_dir,
    )
    all_documents = list(database.list_documents())
    for title in DIRTY_DOC_TITLES:
        matches = [d for d in all_documents if d.title == title]
        if not matches:
            print(f"skip (absent): {title}")
            continue
        targets = matches if title != "冷却水泵性能图表" else [
            d for d in matches if d.id != SAME_NAME_KEEP_DOC_ID
        ]
        for document in targets:
            service.delete_document(document.id, expected_title=document.title)
            print(f"deleted doc id={document.id} {document.title}")

    memory_service = KnowledgeMemoryService(database)
    titles = _memory_titles(database)
    for title in DIRTY_MEMORY_TITLES:
        if title not in titles:
            print(f"skip memory (absent): {title}")
            continue
        # The database API excludes tombstones unless filtered explicitly;
        # purge needs to see both live and deleted rows for the same title.
        entries = (
            database.list_knowledge_memory_entries(limit=500)
            + database.list_knowledge_memory_entries(limit=500, status="deleted")
        )
        entry = next((e for e in entries if e.title == title), None)
        if entry is None:
            print(f"skip memory (not found): {title}")
            continue
        status = str(entry.status.value) if hasattr(entry.status, "value") else str(entry.status)
        if status != "deleted":
            memory_service.delete_entry(entry.id)
        memory_service.purge_entry(entry.id)
        print(f"purged memory: {title}")

    frozen = json.loads((FIXTURE_DIR / "corpus_manifest_v1.json").read_text(encoding="utf-8"))
    live_docs = [
        {"title": d.title, "sha256": d.sha256}
        for d in sorted(database.list_documents(), key=lambda item: item.id)
    ]
    drift = live_docs != [dict(item) for item in frozen["documents"]]
    print("frozen state restored" if not drift else "WARNING: still drifted vs frozen manifest")
    return 0 if not drift else 2


def reapply_dirty() -> int:
    import scripts.build_dirty_long_term_v1 as dirty

    rc = dirty.stage_import()
    if rc:
        return rc
    rc = dirty.stage_read()
    if rc:
        return rc
    return dirty.stage_memories()


def main() -> int:
    parser = argparse.ArgumentParser(description="corpus version switcher (staging only)")
    parser.add_argument("--snapshot-dirty", action="store_true")
    parser.add_argument("--restore-frozen", action="store_true")
    parser.add_argument("--reapply-dirty", action="store_true")
    args = parser.parse_args()
    if not any((args.snapshot_dirty, args.restore_frozen, args.reapply_dirty)):
        parser.print_help()
        return 2
    if args.snapshot_dirty:
        rc = snapshot_dirty()
        if rc:
            return rc
    if args.restore_frozen:
        rc = restore_frozen()
        if rc:
            return rc
    if args.reapply_dirty:
        return reapply_dirty()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
