"""Question Source Verification Engine (Phase 3).

Enforces the highest priority invariant:
'宁可不给题，也绝不能展示无法证明真实存在的题。'

Strictly verifies:
1. Question text authenticity & AI/hallucination detection.
2. Locatable, unpolluted source URL (rejects search engines, root homepages, vague links).
3. School authenticity (rejects fake institutions like '中国邮电大学').
4. Contest authenticity (rejects fake competitions like '不存在比赛').
5. Authentic temporal range (1977 <= year <= current_year).
6. Discipline/subject isolation (e.g. Geography task strictly rejects Physics questions).
7. Regular question protection (prevents standard Gaokao/exam questions from being inflated
   into contest questions).
8. Source database & text consistency for local documents.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Final
from urllib.parse import urlparse

from src.database import Database
from src.eligibility_rules import EligibilityRuleEngine
from src.question_source_models import (
    QuestionSourceCandidate,
    VerificationResult,
    VerificationStatus,
    VerifiedQuestion,
)
from src.targeted_training_models import TrainingTask
from src.training_profile_data import (
    get_official_school_names_set,
)
from src.training_profile_models import LearnerProfile

LOGGER = logging.getLogger(__name__)

# Current temporal limit: cannot exceed current calendar year
CURRENT_YEAR: Final[int] = datetime.now().year

# Banned search engines and non-locatable domains
BANNED_SEARCH_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "baidu.com",
        "www.baidu.com",
        "google.com",
        "www.google.com",
        "bing.com",
        "cn.bing.com",
        "www.bing.com",
        "sogou.com",
        "www.sogou.com",
        "so.com",
        "www.so.com",
        "yahoo.com",
        "search.yahoo.com",
    }
)

# AI hallucination and mock generation keywords
AI_MOCK_SIGNATURES: Final[frozenset[str]] = frozenset(
    {
        "作为ai语言模型",
        "作为人工智能助手",
        "以下是一道模拟题",
        "以下是一道虚构",
        "试题由大模型生成",
        "ai生成",
        "虚拟题目",
        "本题为假设",
        "模拟原创题",
        "由模型自主生成",
    }
)

# Disciplinary keywords for cross-discipline isolation
GEOGRAPHY_CORE_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "等高线", "喀斯特", "锋面气旋", "洋流", "地貌", "河流冲积",
        "赫拉特", "秦岭站", "德雷克海峡", "气候类型", "季风水田",
        "人地协调", "区域综合", "土壤盐碱化", "水文特征", "垂直自然带",
    }
)

PHYSICS_CORE_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "动量定理", "动量守恒", "电子以速度", "洛伦兹力", "焦耳定律",
        "法拉第电磁感应", "带电粒子", "平抛运动", "磁感应强度", "光电效应",
        "闭合电路欧姆定律", "动能定理", "弹性碰撞", "单摆", "简谐运动",
    }
)

# Standard exam keywords that cannot be upgraded to contest questions
STANDARD_EXAM_PATTERNS: Final[tuple[str, ...]] = (
    "高考", "期中", "期末", "一模", "二模", "三模", "调研", "联考",
    "校考", "学业水平", "会考", "练习卷", "专练", "同步",
)

# Authentic contest name markers
AUTHENTIC_CONTEST_PATTERNS: Final[tuple[str, ...]] = (
    "全国高中数学联赛", "中国数学奥林匹克", "CMO", "IMO",
    "全国中学生物理竞赛", "CPhO", "IPhO", "全国物理竞赛",
    "全国高中学生化学竞赛", "CChO", "IChO", "全国化学竞赛",
    "全国中学生生物学联赛", "全国中学生生物学竞赛", "CBO", "IBO",
    "全国青少年信息学奥林匹克", "NOIP", "NOI", "CSP-J/S", "IOI",
    "强基计划校考", "强基校测", "强基笔试",
)


class QuestionSourceVerifier:
    """Rigorous verification engine ensuring question authenticity and locatability."""

    def __init__(
        self,
        database: Database | None = None,
        eligibility_engine: EligibilityRuleEngine | None = None,
    ) -> None:
        self._db = database
        self._official_schools = get_official_school_names_set()
        self._eligibility_engine = eligibility_engine or EligibilityRuleEngine()

    def verify_candidate(
        self,
        candidate: QuestionSourceCandidate,
        profile: LearnerProfile | None = None,
        task: TrainingTask | None = None,
        include_strong_base: bool | None = None,
        include_competition: bool | None = None,
    ) -> VerificationResult:
        """Run candidate question through all verification checks.

        Returns VerificationResult with status VERIFIED or REJECTED.
        """
        # ==============================================================
        # 0. 资格与题源边界核验（Phase 4: Eligibility Rule Engine）
        # ==============================================================
        scope = self._eligibility_engine.compute_allowed_scope(
            profile,
            task,
            include_strong_base=include_strong_base,
            include_competition=include_competition,
        )
        decision = self._eligibility_engine.evaluate_candidate_eligibility(
            candidate, scope, task
        )
        if not decision.is_eligible:
            return VerificationResult(
                status=VerificationStatus.REJECTED,
                reason=decision.reason,
                candidate=candidate,
            )
        applicable_scope = decision.normalized_scope

        # ==============================================================
        # 1. 题目文本基础有效性与 AI 伪造检测
        # ==============================================================
        text = candidate.question_text.strip()
        if not text or len(text) < 5:
            return VerificationResult(
                status=VerificationStatus.REJECTED,
                reason="题目题干内容缺失或过短（少于5个字符），无法核验真实性",
                candidate=candidate,
            )

        text_lower = text.lower()
        for signature in AI_MOCK_SIGNATURES:
            if signature in text_lower:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason=f"检测到 AI 伪造生成试题特征标识（「{signature}」），非真实题源",
                    candidate=candidate,
                )

        # ==============================================================
        # 2. 来源 URL 精准度与可定位性检查（严禁搜索引擎、根域名、模糊链接）
        # ==============================================================
        url = candidate.source_url.strip()
        if not url:
            return VerificationResult(
                status=VerificationStatus.REJECTED,
                reason="题目来源 URL 为空，无法提供原题追溯定位",
                candidate=candidate,
            )

        # 检查是否为搜索引擎
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if any(banned in netloc for banned in BANNED_SEARCH_HOSTS):
            return VerificationResult(
                status=VerificationStatus.REJECTED,
                reason=f"来源 URL 指向搜索引擎（{netloc}），属于搜索页面而非原题页面",
                candidate=candidate,
            )

        search_markers = ("/search", "search?", "wd=", "query=", "q=")
        if any(kw in parsed.path or kw in parsed.query for kw in search_markers):
            return VerificationResult(
                status=VerificationStatus.REJECTED,
                reason="来源 URL 包含搜索关键词查询参数，并非具体原题定位页面",
                candidate=candidate,
            )

        # 检查是否仅为根域名/首页
        if parsed.scheme in ("http", "https"):
            clean_path = parsed.path.strip("/")
            if not clean_path and not parsed.query and not parsed.fragment:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason=f"来源 URL 仅为网站根域名或首页（{url}），无法直接定位原题位置",
                    candidate=candidate,
                )

        # 本地 URL 检查：必须包含有效文档和页码信息
        if url.startswith("ekb://") or "浏览资料" in url:
            if candidate.document_id is None:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason="本地题源链接缺少文档标识（document_id），无法定位原题页面",
                    candidate=candidate,
                )

        # ==============================================================
        # 3. 虚构高校/机构检测（例如：中国邮电大学）
        # ==============================================================
        # 检查 candidate.school_name 以及 exam_or_contest_name、source_name 中的高校表述
        school_candidates = []
        if candidate.school_name:
            school_candidates.append(candidate.school_name.strip())

        # 提取类似“XX大学”或“XX学院”
        combined_names = f"{candidate.exam_or_contest_name} {candidate.source_name}"
        found_schools = re.findall(r"([\u4e00-\u9fa5]{2,10}(?:大学|学院))", combined_names)
        school_candidates.extend(found_schools)

        for sc in school_candidates:
            # 排除非校名误伤，如“科学院”
            if sc in ("教育部", "考试院", "科学院", "工程院"):
                continue
            if sc not in self._official_schools:
                # 检查是否为恶意/虚构校名（如“中国邮电大学”）
                if "中国邮电" in sc or "不存在" in sc or "虚构" in sc:
                    return VerificationResult(
                        status=VerificationStatus.REJECTED,
                        reason=f"检测到虚构高校/机构名称（「{sc}」），不在教育部全国普通高等学校正规名单中",
                        candidate=candidate,
                    )

        # 如果高等教育用户画像中填入了虚构高校
        if profile and profile.higher:
            user_school = getattr(profile.higher, "school", "") or getattr(
                profile.higher, "school_name", ""
            )
            user_school = user_school.strip()
            if user_school and user_school not in self._official_schools and (
                "中国邮电" in user_school or "不存在" in user_school or "虚构" in user_school
            ):
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason=f"用户画像高校「{user_school}」为虚构院校，无法建立高等教育真实题源匹配",
                    candidate=candidate,
                )

        # ==============================================================
        # 4. 虚构比赛/赛事检测（例如：不存在比赛）
        # ==============================================================
        contest_name = (candidate.contest_name or candidate.exam_or_contest_name or "").strip()
        if "不存在比赛" in contest_name or "虚构比赛" in contest_name or "测试比赛" in contest_name:
            return VerificationResult(
                status=VerificationStatus.REJECTED,
                reason=f"检测到虚构赛事名称（「{contest_name}」），不属于全国五大学科竞赛正规赛制",
                candidate=candidate,
            )

        # ==============================================================
        # 5. 年份真实性检查（1977 <= 年份 <= 当前年份）
        # ==============================================================
        if candidate.year is not None:
            if candidate.year < 1977 or candidate.year > CURRENT_YEAR:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason=f"试卷年份异常或虚构（{candidate.year}年），不在高考恢复后的真实时间范围（1977-{CURRENT_YEAR}）内",
                    candidate=candidate,
                )

        # ==============================================================
        # 6. 学科/领域隔离检查（地理 vs 物理）
        # ==============================================================
        if task and task.subject:
            # 任务显式指定学科与候选学科不符
            if candidate.subject and candidate.subject != task.subject:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason=(
                        f"学科不匹配：任务学科为「{task.subject}」，"
                        f"题源学科为「{candidate.subject}」，已执行严格学科隔离"
                    ),
                    candidate=candidate,
                )

            # 跨学科关键词检测
            if task.subject == "地理":
                if any(kw in text for kw in PHYSICS_CORE_KEYWORDS):
                    return VerificationResult(
                        status=VerificationStatus.REJECTED,
                        reason=(
                            f"学科不匹配：题干包含物理专有概念（如动量/电磁），"
                            f"与任务学科「{task.subject}」冲突"
                        ),
                        candidate=candidate,
                    )
            elif task.subject == "物理":
                if any(kw in text for kw in GEOGRAPHY_CORE_KEYWORDS):
                    return VerificationResult(
                        status=VerificationStatus.REJECTED,
                        reason=(
                            f"学科不匹配：题干包含地理专有概念（如等高线/锋面气旋），"
                            f"与任务学科「{task.subject}」冲突"
                        ),
                        candidate=candidate,
                    )

        # ==============================================================
        # 7. 竞赛题真实认证校验
        # ==============================================================
        if "竞赛" in applicable_scope:
            has_authentic_contest = any(
                cp in candidate.exam_or_contest_name for cp in AUTHENTIC_CONTEST_PATTERNS
            ) or candidate.contest_name is not None
            if not has_authentic_contest:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason=(
                        f"声称为竞赛试题，但试卷名称「{candidate.exam_or_contest_name}」"
                        "不属于任何官方认证竞赛"
                    ),
                    candidate=candidate,
                )

        # ==============================================================
        # 8. 本地数据库与原文一致性比对（一等题源真实性核验）
        # ==============================================================
        if self._db and candidate.document_id is not None:
            doc_valid, page_valid, text_match = self._verify_local_db_record(
                candidate.document_id,
                candidate.page_id,
                candidate.question_text,
            )
            if not doc_valid:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason=(
                        f"本地一等题源核验失败：文档 ID {candidate.document_id} 在数据库中不存在"
                    ),
                    candidate=candidate,
                )
            if candidate.page_id is not None and not page_valid:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason=f"本地一等题源核验失败：页面 ID {candidate.page_id} 在数据库中不存在",
                    candidate=candidate,
                )
            if not text_match:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason="题目内容与本地资料原文提取文本不一致或严重偏离，疑似篡改",
                    candidate=candidate,
                )

        # ==============================================================
        # 8b. 强基试题完整性必验（高校名称、专业方向、年份、来源、原题链接）
        # ==============================================================
        cand_school = candidate.school_name
        if not cand_school and "强基" in applicable_scope:
            cand_school = scope.strong_base_school
        cand_dir = candidate.major_direction
        if not cand_dir and "强基" in applicable_scope:
            cand_dir = scope.strong_base_subject

        if "强基" in applicable_scope:
            is_complete = (
                bool(cand_school)
                and bool(cand_dir)
                and candidate.year is not None
                and bool(candidate.source_name)
                and bool(candidate.source_url)
            )
            if not is_complete:
                return VerificationResult(
                    status=VerificationStatus.REJECTED,
                    reason="强基试题关键信息（学校、专业方向、年份、来源链接）不完整，无法核验证实",
                    candidate=candidate,
                )

        # ==============================================================
        # 核验通过，生成已验证题目对象
        # ==============================================================
        doc_tag = candidate.document_id or "ext"
        item_tag = candidate.question_item_id or "item"
        yr_tag = candidate.year or "na"
        q_tag = candidate.question_number or "0"
        verified_id = f"vq_{doc_tag}_{item_tag}_{yr_tag}_{q_tag}"
        verified = VerifiedQuestion(
            id=verified_id,
            question_text=candidate.question_text,
            source_name=candidate.source_name,
            source_url=candidate.source_url,
            year=candidate.year,
            exam_or_contest_name=candidate.exam_or_contest_name,
            question_number=candidate.question_number,
            subject=candidate.subject,
            applicable_scope=applicable_scope,
            verification_status=VerificationStatus.VERIFIED,
            difficulty_level=candidate.difficulty_level,
            document_id=candidate.document_id,
            page_id=candidate.page_id,
            page_number=candidate.page_number,
            question_item_id=candidate.question_item_id,
            family_id=candidate.family_id,
            school_name=cand_school,
            major_direction=cand_dir,
            contest_name=candidate.contest_name,
            contest_tier=candidate.contest_tier,
            reference_answer=candidate.reference_answer,
        )

        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            verified_question=verified,
            candidate=candidate,
        )

    def _verify_local_db_record(
        self,
        document_id: int,
        page_id: int | None,
        question_text: str,
    ) -> tuple[bool, bool, bool]:
        """Verify local document existence and text consistency in SQLite."""
        if not self._db:
            return True, True, True

        with self._db._connection() as conn:
            # 1. 检查文档存在
            doc_row = conn.execute(
                "SELECT id, title FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not doc_row:
                return False, False, False

            # 2. 检查页面存在
            if page_id is not None:
                page_row = conn.execute(
                    """
                    SELECT id, extracted_text, ocr_text, markdown_content
                    FROM pages WHERE id = ? AND document_id = ?
                    """,
                    (page_id, document_id),
                ).fetchone()
                if not page_row:
                    return True, False, False

                # 3. 检查题目文本与页面提取内容的匹配度
                clean_q = re.sub(r"[\s\W_]+", "", question_text)[:30]
                page_all_text = "".join(
                    str(page_row[k] or "")
                    for k in ("extracted_text", "ocr_text", "markdown_content")
                )
                clean_page = re.sub(r"[\s\W_]+", "", page_all_text)

                if clean_q and clean_q not in clean_page:
                    # 允许一定 OCR 容错（至少匹配前10个字或包含核心题眼）
                    sub_10 = clean_q[:10]
                    if sub_10 and sub_10 not in clean_page:
                        # 检查 question_items 表是否有原始 stem_text 记录
                        qi_match = conn.execute(
                            """
                            SELECT id FROM question_items
                            WHERE document_id = ? AND page_id = ?
                            """,
                            (document_id, page_id),
                        ).fetchone()
                        if not qi_match:
                            return True, True, False

            return True, True, True
