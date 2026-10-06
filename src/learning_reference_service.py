"""One bounded reference generation when an atomic question joins learning."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from src.learning_ai_draft_service import LearningAIDraftError, LearningAIDraftService
from src.question_content_ui import question_regions
from src.question_visual_regions import crop_region
from src.visual_input_budget import prepare_page_image


def question_reference_image(question, database) -> str | None:
    """Read only this question's current figure crops; never supply OCR text."""

    material = (question.ai_draft or {}).get("visual_material", {})
    if not isinstance(material, dict) or material.get("dependency") == "none":
        return None
    page = database.get_page(question.page_id)
    source = Path(page.image_path) if page is not None else None
    regions = question_regions(
        source, question.question_number, material.get("regions", []), page_id=question.page_id,
    )
    if not regions:
        if material.get("dependency") == "required":
            raise LearningAIDraftError("本题需要图表，但关联截图尚待核对；请先修改截图方框。")
        return None
    figures = []
    for index, region in enumerate(regions):
        data = crop_region(source, region) if source is not None else None
        if data is None:
            raise LearningAIDraftError("本题关联截图不可用，请先核对原图和截图方框。")
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((1400, 1400))
            label = f"Figure {index + 1}: {region['role']} {region.get('option_label', '')}"
            figures.append((label, image.copy()))
    sheet = Image.new("RGB", (max(img.width for _, img in figures) + 32,
                             sum(img.height + 40 for _, img in figures) + 16), "white")
    draw, y = ImageDraw.Draw(sheet), 8
    for label, figure in figures:
        draw.text((16, y), label, fill="black")
        sheet.paste(figure, (16, y + 24))
        y += figure.height + 40
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    return prepare_page_image(buffer.getvalue(), max_long_edge=3000,
                              jpeg_quality=94, max_base64_chars=4_000_000).data_url


def generate_join_reference(
    question, database, *, provider, vision_provider=None, learner_profile=None,
) -> dict:
    """Generate once with the appropriate model; never silently lose required figures."""

    image = question_reference_image(question, database)
    selected = vision_provider if image else provider
    if selected is None:
        raise LearningAIDraftError(
            "读图模型未配置，参考版暂未生成；请在设置中配置读图模型。"
            if image else "AI 模型未配置，参考版暂未生成；可继续人工整理。"
        )
    return LearningAIDraftService(selected).generate_question_reference(
        question, learner_profile=learner_profile, image_data=image,
    )
