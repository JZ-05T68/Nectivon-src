"""Structured textbook-figure replica pipeline (round 4, Route A).

A cloud vision model proposes a strictly constrained intermediate
representation (``nectivon-replica/1``); local deterministic code renders the
SVG/PNG replica and verifies it.  Two audited cloud calls per generation:

1. spec extraction (one call over the raw crop);
2. independent visual verification (original crop vs rendered replica).

No automatic retries on semantic dissatisfaction.  Original photographs stay
immutable evidence; replicas are derived display files, content-addressed by
the crop pixels they were generated from.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image

from src.figure_replica_render import render_replica
from src.figure_replica_schema import (
    REPLICA_VERSION,
    ReplicaSpecError,
    parse_replica_spec,
    verify_replica_spec,
)

LOGGER = logging.getLogger(__name__)

STATUS_VERIFIED = "verified"
STATUS_ACCEPTED = "accepted"
STATUS_FAILED_VISUAL = "failed_visual"
STATUS_FAILED_PARSE = "failed_parse"
STATUS_UNSUPPORTED = "unsupported"
LIVE_STATUSES = frozenset({
    STATUS_VERIFIED, STATUS_ACCEPTED, STATUS_FAILED_VISUAL, STATUS_FAILED_PARSE,
    STATUS_UNSUPPORTED,
})

_SPEC_PROMPT = "\n".join((
    "你是工程教材插图结构化助手。把图片中的工程图忠实转写为受约束的 JSON，"
    "供本地程序渲染为清晰的教材插图复刻。",
    "",
    "绝对要求：",
    "- 忠实原图：元件数量与类型、连接关系（谁连谁）、箭头方向、"
    "线型（实线/虚线）、标注文字必须与原图一致。",
    "- 不编造：看不清或不确定的结构绝不补画，写入 notes 说明。",
    "- 文字与公式：照抄原文字；下标写 _{..}，上标写 ^{..}"
    "（如 q_{1}、u_{o}^{2}），希腊字母直接用字符（α β ω μ θ）。"
    "不得改动公式含义。",
    "- 坐标：以图片左上角为 (0,0)；先估宽高比，canvas.width 取 "
    "1000~1600，height 按比例。全部坐标用该画布单位。",
    "",
    "figure_type 选择：hydraulic=液压/气动原理图；"
    "block_diagram=控制方框图；circuit=电路/接线图；curve=坐标曲线；"
    "node_link=结构/关系图。",
    "若图片是照片、复杂美术插图或不适合矢量重绘，返回 ",
    '{"schema":"nectivon-replica/1","unsupported":"原因"}。',
    "",
    'elements 元素：{"id":"e1","kind":"符号","x":中心x,"y":中心y,'
    '"w":宽,"h":高,"rotate":0,"labels":[{"text":"1YA","dx":30,"dy":-25}]}',
    "可选 kind：",
    "- hydraulic：tank油箱、pump泵(variable:true表变量)、"
    "motor液压马达、cylinder液压缸(rods:\"single\"|\"double\","
    "cushion:true)、valve换向阀(squares:2|3, "
    "detail每格[{arrows:[{from,to,two_way}],blocked:[\"left\"],"
    "closed:false}] 用 left/right/top/bottom/center, "
    "ports:{\"left\":\"P\",\"right\":\"B\",\"top\":\"A\","
    "\"bottom\":\"T\"})、relief_valve溢流阀、reducing_valve减压阀、"
    "throttle节流阀、check_valve单向阀、pressure_gauge压力表、"
    "filter过滤器、accumulator蓄能器、solenoid电磁铁、spring弹簧、"
    "lever手柄、plug堵头、load负载、junction连接点、box通用框、"
    "circle通用圆、path多段线(points,line)、text独立文字",
    "- block_diagram：block方框(text=框内文字)、"
    "sum比较点(signs:{\"left\":\"+\",\"top\":\"-\"})、"
    "branch/junction引出点、box、path、text",
    "- circuit：resistor、capacitor、inductor、source_dc、source_ac、"
    "battery、ground、terminal端子、winding绕组、switch、lamp、"
    "motor_circuit、box、circle、path、text",
    "- node_link：node(文字节点框)、box、circle、path、text",
    "",
    "connections 连线：{\"id\":\"c1\",\"points\":[[x,y],..≥2个折点含端点],"
    "\"attach_from\":\"e1.right\"(可省),"
    "\"attach_to\":\"e2.left\"(可省),"
    "\"line\":\"solid|dashed|dash_dot|dotted\","
    "\"arrow\":\"none|start|end|both\","
    "\"flow\":\"none|forward|backward\"(液压管路流向),"
    "\"joints\":[[x,y]](T型连接点),"
    "\"crossings\":[[x,y]](不相连的交叉),"
    "\"label\":{\"text\":\"q_{1}\",\"at\":[x,y]}}",
    "端口名固定：left/right/top/bottom/center。attach 端点须落在"
    "对应元素端口附近（±30 单位内）。",
    "方向与朝向必须照原图：液压缸用 rotate 表示活塞杆伸出方向"
    "（rotate:0 杆朝右、180 杆朝左、90 杆朝上、270 杆朝下）；"
    "负载 load 的外力箭头方向同样用 rotate"
    "（0=向上、90=向右、180=向下、270=向左）；"
    "换向阀用 rotate=90 表示竖直位。",
    "严禁发明原图不存在的文字（如 in/out/入口/出口/P/T）："
    "只有原图真实可见的字母数字才写入 ports 或 labels。",
    "annotations：图题、独立说明文字 [{\"x\",\"y\",\"text\","
    "\"anchor\":\"start|middle|end\",\"size\":\"normal|small\"}]。",
    "curve 图特别注意：坐标轴只由 axes 字段定义，禁止用 path 元素画坐标轴；"
    "峰值/稳态等辅助虚线用 line=\"dashed\" 的 path 元素表示并配 text 标注；"
    "每条曲线在 curves 中给出至少 6 个数据点（物理坐标）以呈现平滑形状。",
    "",
    "只返回一个 JSON 对象。",
))

_VERIFY_PROMPT = "\n".join((
    "你是工程教材插图审校员。左图是原始照片裁剪（证据），"
    "右图是程序依据结构化数据重绘的复刻图。",
    "先逐一陈述原图中的关键事实再比对：泵/阀/缸各位于图中什么方位、"
    "液压缸活塞杆从哪一侧伸出、外力箭头指向、实线与虚线各连到哪里、"
    "可见字母文字清单。然后逐项核对复刻图：",
    "1) 元件/符号的数量与类型；2) 连线拓扑（谁连谁）、"
    "T型连接与不相连交叉的区分；3) 箭头方向、流向、正负号、"
    "活塞杆伸出方向与外力方向；4) 线型（实线/虚线/点划线）"
    "所表示的油路性质（主油路/先导控制/泄油）；"
    "5) 文字、编号、公式（含上下标）逐字一致，"
    "复刻图不得多出或缺少文字。",
    "结构性/专业性差异（数量、拓扑、方向、线型性质、文字）写入 "
    "missing/extra/wrong/text_errors；仅符号美术画法风格差异"
    "（弹簧圈数、线宽、箭头大小比例）不判失败，写入 style_notes。",
    "只依据两图可见内容判断，不猜测、不解题。只返回 JSON：",
    "{\"verdict\":\"pass|fail\","
    "\"missing\":[\"原图有而复刻缺少的结构\"],"
    "\"extra\":[\"复刻图多画的结构或文字\"],"
    "\"wrong\":[\"画错的结构/方向/线型性质\"],"
    "\"text_errors\":[\"不一致的文字\"],"
    "\"style_notes\":[\"仅风格差异说明\"]}",
    "仅当 missing/extra/wrong/text_errors 四类全部为空才给 pass；"
    "发现任何专业差异必须列全并给 fail。",
))

_CORRECTION_PROMPT_TEMPLATE = (
    "你是工程插图结构化数据修正员。左图是原始照片（证据），"
    "右图是依据结构化数据渲染出的复刻图。"
    "审校发现以下专业差异：\n{diffs}\n"
    "请先在心中描述原图的关键朝向（液压缸位置与活塞杆伸出方向、"
    "外力方向、箭头方向），再对照差异逐项修正。\n"
    "返回修正后的完整结构化 JSON（schema 仍是 nectivon-replica/1，"
    "格式与生成时相同）。只修正与差异相关的部分，其余原样保留；"
    "仍不确定的结构写入 notes，不要编造。只返回这一个 JSON 对象。"
)


def _strip_fences(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def _load_json(text: str) -> dict:
    from src.recognition_json import repair_latex_json_escapes

    return json.loads(repair_latex_json_escapes(_strip_fences(text)))


def replica_store_root(source_path: Path | str) -> Path:
    """Replicas live beside the physical source page, hash-addressed."""

    from src.question_image_store import QuestionImageStore

    return QuestionImageStore(source_path).root / "replicas"


def replica_key(crop_bytes: bytes) -> str:
    """Content-address replicas: same crop pixels → same replica."""

    return hashlib.sha256(crop_bytes).hexdigest()[:32]


def load_replica(source_path: Path | str, crop_bytes: bytes) -> dict | None:
    """Return the stored replica record for these exact crop pixels, if any."""

    record = replica_store_root(source_path) / replica_key(crop_bytes) / "status.json"
    try:
        value = json.loads(record.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("status") not in LIVE_STATUSES:
            return None
        return value
    except (OSError, ValueError):
        return None


def replica_png(source_path: Path | str, crop_bytes: bytes) -> bytes | None:
    path = replica_store_root(source_path) / replica_key(crop_bytes) / "replica.png"
    try:
        return path.read_bytes()
    except OSError:
        return None


def accept_replica(source_path: Path | str, crop_bytes: bytes) -> dict | None:
    """Record an explicit human acceptance of a reviewed replica.

    Only a user action may promote a ``failed_visual`` replica; the automated
    verifier's findings stay in the record for traceability.
    """

    root = replica_store_root(source_path) / replica_key(crop_bytes)
    record_path = root / "status.json"
    try:
        value = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("status") not in LIVE_STATUSES:
        return None
    value["status"] = STATUS_ACCEPTED
    value["accepted_at"] = datetime.now(UTC).isoformat()
    value["acceptance"] = "USER CONFIRMED REPLICA（人工核验通过）"
    record_path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return value


def _composite_for_verification(original: bytes, replica: bytes, *, panel_height: int = 640,
                                ) -> bytes:
    """Place original and replica side by side with panel captions."""

    from PIL import ImageDraw, ImageFont

    panels: list[tuple[Image.Image, str]] = []
    for data, caption in ((original, "原图（证据）"), (replica, "复刻图（重绘）")):
        with Image.open(io.BytesIO(data)) as image:
            ratio = panel_height / image.height
            panel = image.convert("RGB").resize(
                (max(1, round(image.width * ratio)), panel_height), Image.LANCZOS)
        panels.append((panel, caption))
    gap, margin, caption_h = 24, 16, 40
    width = margin * 2 + panels[0][0].width + gap + panels[1][0].width
    board = Image.new("RGB", (width, margin * 2 + caption_h + panel_height), "#ffffff")
    draw = ImageDraw.Draw(board)
    font = None
    for candidate in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if Path(candidate).is_file():
            try:
                font = ImageFont.truetype(candidate, 26)
                break
            except OSError:
                continue
    cursor = margin
    for panel, caption in panels:
        board.paste(panel, (cursor, margin + caption_h))
        draw.text((cursor + panel.width / 2, margin + caption_h / 2), caption,
                  fill="#1a1a1a", font=font, anchor="mm")
        cursor += panel.width + gap
    output = io.BytesIO()
    board.save(output, format="PNG")
    return output.getvalue()


def _verify_visually(provider, original: bytes, replica_png_bytes: bytes,
                     *, page_ref: str, response_dir: Path) -> dict:
    from src.question_image_store import QuestionImageError
    from src.visual_input_budget import prepare_page_image

    composite = _composite_for_verification(original, replica_png_bytes)
    completion = provider.complete_vision(
        _VERIFY_PROMPT, prepare_page_image(composite).data_url,
        model=provider.default_model, max_completion_tokens=1536,
        json_output=True, source_feature="figure_replica_verify",
        target_refs=(page_ref,),
    )
    response_dir.mkdir(parents=True, exist_ok=True)
    (response_dir / f"verify-response-{uuid4().hex}.json").write_text(json.dumps({
        "model": completion.model, "resolved_model": completion.resolved_model,
        "raw_response": completion.text, "at": datetime.now(UTC).isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        verdict = _load_json(completion.text)
        if (not isinstance(verdict, dict) or verdict.get("verdict") not in ("pass", "fail")
                or any(not isinstance(verdict.get(key, []), list) for key in
                       ("missing", "extra", "wrong", "text_errors", "style_notes"))):
            raise ValueError("Invalid verdict")
    except (ValueError, TypeError) as exc:
        raise QuestionImageError("复刻核验结果无效，本次复刻未发布。") from exc
    return {"verdict": verdict["verdict"],
            "missing": [str(x) for x in verdict.get("missing", [])],
            "extra": [str(x) for x in verdict.get("extra", [])],
            "wrong": [str(x) for x in verdict.get("wrong", [])],
            "text_errors": [str(x) for x in verdict.get("text_errors", [])],
            "style_notes": [str(x) for x in verdict.get("style_notes", [])],
            "model": completion.model, "resolved_model": completion.resolved_model}


def _request_spec(provider, prompt: str, crop_bytes: bytes, *, page_ref: str,
                  root: Path) -> tuple[dict | None, dict]:
    """One audited spec/correction call; returns (parsed payload, call meta)."""

    from src.visual_input_budget import prepare_page_image

    completion = provider.complete_vision(
        prompt, prepare_page_image(crop_bytes).data_url,
        model=provider.default_model, max_completion_tokens=8192,
        json_output=True, source_feature="figure_replica_spec",
        target_refs=(page_ref,),
    )
    (root / f"spec-response-{uuid4().hex}.json").write_text(json.dumps({
        "model": completion.model, "resolved_model": completion.resolved_model,
        "raw_response": completion.text, "at": datetime.now(UTC).isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        payload = _load_json(completion.text)
    except (ValueError, TypeError):
        return None, {"model": completion.model}
    if not isinstance(payload, dict):
        return None, {"model": completion.model}
    return payload, {"model": completion.model,
                     "resolved_model": completion.resolved_model}


def generate_figure_replica(
    provider, crop_bytes: bytes, *, source_path: Path | str, page_ref: str,
    provenance: dict | None = None, allow_correction: bool = True,
) -> dict:
    """Generate, verify and persist one replica for these crop pixels.

    Exactly two cloud calls when the first verification passes; at most one
    bounded correction round (spec fix + re-verify, +2 calls) when it does
    not.  Any failure records a status instead of falling back to the raw
    photo.
    """

    from src.question_image_store import QuestionImageError
    from src.visual_input_budget import prepare_page_image

    if provider is None:
        raise QuestionImageError("未配置云端读图模型，无法生成教材插图复刻。")
    root = replica_store_root(source_path) / replica_key(crop_bytes)
    root.mkdir(parents=True, exist_ok=True)
    meta = {
        "replica_key": root.name, "render_version": REPLICA_VERSION,
        "source_provenance": provenance or {}, "page_ref": page_ref,
        "generated_at": datetime.now(UTC).isoformat(),
    }

    def publish(status: str, **fields) -> dict:
        value = {**meta, "status": status, **fields}
        (root / "status.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return value

    def build_spec(payload: dict, call_meta: dict):
        """Validate + render + persist one spec attempt (no cloud calls)."""

        if payload.get("unsupported"):
            return "unsupported", None, None, None, str(payload["unsupported"])[:240]
        try:
            spec = parse_replica_spec(payload)
        except ReplicaSpecError as exc:
            return "failed_parse", None, None, None, str(exc)
        check = verify_replica_spec(spec)
        (root / "spec.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        svg, png = render_replica(spec)
        (root / "replica.svg").write_bytes(svg)
        (root / "replica.png").write_bytes(png)
        details = {
            "figure_type": spec["figure_type"], **call_meta,
            "elements": check.element_count, "connections": check.connection_count,
            "labels": check.label_count, "spec_warnings": check.warnings,
            "unverifiable": check.unverifiable, "notes": spec.get("notes", []),
        }
        return "ok", spec, png, details, None

    payload, call_meta = _request_spec(provider, _SPEC_PROMPT, crop_bytes,
                                       page_ref=page_ref, root=root)
    if payload is None:
        return publish(STATUS_FAILED_PARSE, reason="模型输出不是有效 JSON。", **call_meta)
    state, spec, png, details, failure = build_spec(payload, call_meta)
    if state == "unsupported":
        return publish(STATUS_UNSUPPORTED, reason=failure, **call_meta)
    if state == "failed_parse":
        return publish(STATUS_FAILED_PARSE, reason=failure, **call_meta)

    def run_verification() -> dict | None:
        try:
            return _verify_visually(provider, crop_bytes, png, page_ref=page_ref,
                                    response_dir=root)
        except Exception as exc:  # noqa: BLE001 - a failed check must not lose the render
            LOGGER.exception("复刻视觉核验调用失败")
            return {"verdict": "fail", "missing": [], "extra": [], "wrong": [],
                    "text_errors": [], "style_notes": [],
                    "reason": f"核验调用未完成：{exc}"}

    def blocking_issues(verdict: dict) -> list[str]:
        return (verdict.get("missing", []) + verdict.get("extra", [])
                + verdict.get("wrong", []) + verdict.get("text_errors", []))

    verdict = run_verification()
    corrected = False
    if verdict and blocking_issues(verdict) and allow_correction:
        # One bounded correction round: the model sees its own rendered
        # replica plus the diff list and returns a fixed spec.
        diffs = "\n".join(
            [f"缺失: {x}" for x in verdict.get("missing", [])]
            + [f"多余: {x}" for x in verdict.get("extra", [])]
            + [f"错误: {x}" for x in verdict.get("wrong", [])]
            + [f"文字差异: {x}" for x in verdict.get("text_errors", [])]
        )
        composite = _composite_for_verification(crop_bytes, png)
        correction_prompt = _CORRECTION_PROMPT_TEMPLATE.replace("{diffs}", diffs)
        correction = provider.complete_vision(
            correction_prompt, prepare_page_image(composite).data_url,
            model=provider.default_model, max_completion_tokens=8192,
            json_output=True, source_feature="figure_replica_correct",
            target_refs=(page_ref,),
        )
        (root / f"correct-response-{uuid4().hex}.json").write_text(json.dumps({
            "model": correction.model, "resolved_model": correction.resolved_model,
            "raw_response": correction.text, "diffs": diffs,
            "at": datetime.now(UTC).isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            corrected_payload = _load_json(correction.text)
        except (ValueError, TypeError):
            corrected_payload = None
        if isinstance(corrected_payload, dict):
            state2, spec2, png2, details2, failure2 = build_spec(
                corrected_payload, {"model": correction.model,
                                    "resolved_model": correction.resolved_model})
            if state2 == "ok":
                png, details = png2, details2
                verdict = run_verification()
                corrected = True
            elif state2 == "unsupported":
                return publish(STATUS_UNSUPPORTED, reason=failure2,
                               model=correction.model, corrected=corrected)

    if verdict and verdict.get("verdict") == "pass" and not blocking_issues(verdict):
        return publish(STATUS_VERIFIED, verification=verdict, corrected=corrected,
                       **details)
    reason = (verdict or {}).get("reason") or "复刻与原图存在结构或文字差异，待人工核验。"
    return publish(STATUS_FAILED_VISUAL, verification=verdict, reason=reason,
                   corrected=corrected, **details)
