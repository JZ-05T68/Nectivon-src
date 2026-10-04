"""G3-B P2 experiment: mechanism chain draft (NOT a schema asset).

Runs once against the 冲刺2 cycle-economy figure OCR text and writes an
experimental JSON draft to the evidence directory.  Deliberately NOT
promoted into question_groups/any v29 table (task spec §14).
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.learning_ai_draft_service import (  # noqa: E402
    LearningAIDraftService,
    _strip_code_fence,
)
from src.runtime import application_ai_provider  # noqa: E402

OUT = Path(
    os.environ.get(
        "EKB_G3B_EVIDENCE_DIR",
        str(REPO_ROOT / "runtime" / "experiments" / "g3b"),
    )
) / "mechanism_chain_draft_experimental.json"

PROMPT = (
    "你是地理学习助手。以下是「北疆电厂循环经济链示意图」的OCR文字："
    "原海水-火力发电(发电为龙头)-余热-海水淡化(优质淡水)-浓海水-汉沽盐场"
    "(日晒制盐)-原盐-精制盐；灰渣-砌块水泥石膏建材。电水盐一体化循环经济。"
    "请输出JSON(不要其它文字)："
    '{"chain_name":"链名","nodes":["按顺序的节点"],'
    '"links":[{"from":"A","to":"B","relation":"because或causes或produces或enables",'
    '"conditions":"成立条件","evidence":"材料证据"}],'
    '"experimental_note":"experimental draft 非正式资产"}。只依据给出内容。'
)


def main() -> int:
    provider = application_ai_provider()
    if provider is None:
        print("NO_PROVIDER")
        return 2
    service = LearningAIDraftService(provider)
    raw = service._complete(  # noqa: SLF001 - one-shot experiment
        PROMPT, target_refs=("experimental_mechanism_chain",)
    )
    parsed = json.loads(_strip_code_fence(raw))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK nodes={len(parsed.get('nodes', []))} links={len(parsed.get('links', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
