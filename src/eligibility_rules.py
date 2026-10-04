"""Eligibility Rule Engine for Targeted Training Boundary Control (Phase 4).

Controls the precise boundary of allowed question sources based on learner profile:
- Qiangji Plan: restricted strictly to target school + target major;
- Academic Olympiads: restricted strictly to competition subject + authentic stage/tier;
- Regular Question Protection: standard Gaokao/exam questions never upgraded by user identity;
- Higher Education: restricted strictly by authentic school + official major domains;
- Defense against fake schools, fake competitions, and fake stages.
"""

from __future__ import annotations

import logging
from typing import Final

from src.eligibility_models import AllowedSourceScope, CompetitionTier, EligibilityDecision
from src.question_source_models import QuestionSourceCandidate
from src.targeted_training_models import TrainingTask
from src.training_profile_data import (
    COMPETITION_STAGES_BY_SUBJECT,
    QIANGJI_UNIVERSITIES_AND_MAJORS,
    get_official_school_names_set,
)
from src.training_profile_models import EducationType, LearnerProfile

LOGGER = logging.getLogger(__name__)

# Keywords identifying standard high school papers that can never be inflated
STANDARD_EXAM_PATTERNS: Final[tuple[str, ...]] = (
    "高考",
    "期中",
    "期末",
    "一模",
    "二模",
    "三模",
    "调研",
    "联考",
    "校考",
    "学业水平",
    "会考",
    "练习卷",
    "专练",
    "同步",
    "选拔考",
    "质量检测",
)

# Standard domain mapping for typical undergraduate majors
MAJOR_TO_ALLOWED_DOMAINS: Final[dict[str, tuple[str, ...]]] = {
    "机器人工程": (
        "自动控制原理",
        "机器人学",
        "机械原理",
        "控制工程",
        "信号与系统",
        "传感技术",
        "微机原理",
        "电子技术",
        "控制科学与工程",
        "机械电子",
    ),
    "计算机科学与技术": (
        "数据结构",
        "算法",
        "操作系统",
        "计算机组成原理",
        "计算机网络",
        "数据库",
        "编译原理",
        "软件工程",
        "计算机科学与技术",
    ),
    "软件工程": (
        "数据结构",
        "软件工程",
        "操作系统",
        "计算机网络",
        "数据库系统",
        "系统分析与设计",
    ),
    "物理学": (
        "力学",
        "热学",
        "电磁学",
        "光学",
        "原子物理学",
        "理论力学",
        "热力学与统计物理",
        "电动力学",
        "量子力学",
        "固体物理",
        "物理学",
    ),
    "地理科学": (
        "自然地理学",
        "人文地理学",
        "地貌学",
        "水文学",
        "气象学与气候学",
        "土壤地理学",
        "遥感概论",
        "地理信息系统",
        "区域分析",
    ),
}

# Unrelated discipline cross-contamination markers (e.g. for engineering vs medical/law)
UNRELATED_DISCIPLINE_CONFLICTS: Final[dict[str, tuple[str, ...]]] = {
    "机器人工程": ("医学", "临床医学", "内科学", "外科学", "法学", "法理学", "刑法", "民法"),
    "计算机科学与技术": ("医学", "临床医学", "护理学", "法理学", "法学"),
    "物理学": ("医学", "临床医学", "法理学", "民商法"),
}


class EligibilityRuleEngine:
    """Rule engine computing and enforcing strict eligibility boundaries."""

    def __init__(self) -> None:
        self._official_schools = get_official_school_names_set()

    @staticmethod
    def classify_competition_stage(stage_name: str) -> CompetitionTier | None:
        """Classify competition stage into Provincial, National, or International tier.

        Returns None if the stage is abnormal, fake, or unrecognized.
        """
        if not stage_name or not stage_name.strip():
            return None

        clean = stage_name.strip()
        if "不存在" in clean or "虚构" in clean or "测试比赛" in clean:
            return None

        # Check if stage exists in authentic official stages list
        is_known_stage = False
        for stages_list in COMPETITION_STAGES_BY_SUBJECT.values():
            if any(clean in st or st in clean for st in stages_list):
                is_known_stage = True
                break

        general_stages = (
            "初赛",
            "复赛",
            "决赛",
            "联赛",
            "国决",
            "集训队",
            "预赛",
            "冬令营",
            "奥林匹克",
            "CSP",
            "NOIP",
            "NOI",
            "IMO",
            "IPhO",
            "CPhO",
            "CMO",
            "CChO",
            "CBO",
        )
        if not is_known_stage and not any(kw in clean for kw in general_stages):
            return None

        # International tier
        intl_markers = ("集训队", "国际", "IMO", "IPhO", "IChO", "IBO", "IOI")
        if any(kw in clean for kw in intl_markers):
            return CompetitionTier.INTERNATIONAL

        # National tier
        nat_markers = ("决赛", "国决", "冬令营", "CMO", "CPhO", "CChO", "CBO", "NOI")
        if any(kw in clean for kw in nat_markers):
            return CompetitionTier.NATIONAL

        # Provincial tier
        prov_markers = ("预赛", "初赛", "复赛", "联赛", "省赛", "省队", "省一", "CSP-J/S", "NOIP")
        if any(kw in clean for kw in prov_markers):
            return CompetitionTier.PROVINCIAL

        return None

    @staticmethod
    def validate_strong_base_target(school: str, subject: str) -> tuple[bool, str | None]:
        """Validate Qiangji pilot school and officially published major direction."""
        if not school or not school.strip():
            return False, "强基计划目标高校未填写"
        if not subject or not subject.strip():
            return False, "强基计划目标学科未填写"

        clean_school = school.strip()
        clean_subject = subject.strip()

        if clean_school not in QIANGJI_UNIVERSITIES_AND_MAJORS:
            return False, f"高校「{clean_school}」非教育部官方批准的39所强基计划试点高校"

        allowed_majors = QIANGJI_UNIVERSITIES_AND_MAJORS[clean_school]
        # Match exact or clean substring (e.g. '物理学' in '物理学类')
        matched = any(
            clean_subject == m or clean_subject in m or m in clean_subject for m in allowed_majors
        )
        if not matched:
            return False, (
                f"专业方向「{clean_subject}」不属于「{clean_school}」官方公布的强基计划招生范围"
            )

        return True, None

    def compute_allowed_scope(
        self,
        profile: LearnerProfile | None,
        task: TrainingTask | None = None,
        include_strong_base: bool | None = None,
        include_competition: bool | None = None,
    ) -> AllowedSourceScope:
        """Compute the authoritative allowed source scope for a learner and task."""
        if profile is None or profile.is_empty:
            return AllowedSourceScope(
                education_stage="基础教育",
                allow_regular=True,
                scope_description="默认基础教育普通题源范围",
            )

        # --------------------------------------------------------------
        # 1. 基础教育路径
        # --------------------------------------------------------------
        if profile.education_type == EducationType.BASIC:
            basic = profile.basic
            if basic is None:
                return AllowedSourceScope(
                    education_stage="基础教育",
                    allow_regular=True,
                    scope_description="基础教育普通题源",
                )

            # 强基计划与学科竞赛仅限高中阶段配置有效
            is_sb_eligible = False
            sb_school = None
            sb_subj = None
            is_comp_eligible = False
            comp_subj = None
            comp_stage = None
            comp_tier = None

            if basic.stage == "高中":
                # 用户主动勾选约束：若显式传入 False，则即使画像中开启也不允许查询
                allow_sb = (
                    basic.in_strong_base
                    if include_strong_base is None
                    else (basic.in_strong_base and include_strong_base)
                )
                if allow_sb and basic.strong_base_school and basic.strong_base_subject:
                    is_valid, _ = self.validate_strong_base_target(
                        basic.strong_base_school, basic.strong_base_subject
                    )
                    if is_valid:
                        is_sb_eligible = True
                        sb_school = basic.strong_base_school.strip()
                        sb_subj = basic.strong_base_subject.strip()

                allow_comp = (
                    basic.in_competition
                    if include_competition is None
                    else (basic.in_competition and include_competition)
                )
                # 学科竞赛资格评估
                if allow_comp and basic.competition_subjects:
                    # 确定当前任务学科或主竞赛学科
                    active_subject = (task.subject if task and task.subject else None) or next(
                        iter(basic.competition_subjects.keys()), None
                    )
                    if active_subject and active_subject in basic.competition_subjects:
                        stage_str = basic.competition_subjects[active_subject]
                        tier = self.classify_competition_stage(stage_str)
                        if tier is not None:
                            is_comp_eligible = True
                            comp_subj = active_subject
                            comp_stage = stage_str
                            comp_tier = tier

            desc = "基础教育 · 普通题源"
            if is_sb_eligible:
                desc += f" + 强基题源({sb_school}·{sb_subj})"
            if is_comp_eligible:
                desc += f" + 竞赛题源({comp_subj}·{comp_tier})"

            return AllowedSourceScope(
                education_stage="基础教育",
                allow_regular=True,
                is_strong_base_eligible=is_sb_eligible,
                strong_base_school=sb_school,
                strong_base_subject=sb_subj,
                is_competition_eligible=is_comp_eligible,
                competition_subject=comp_subj,
                competition_stage=comp_stage,
                competition_tier=comp_tier,
                scope_description=desc,
            )

        # --------------------------------------------------------------
        # 2. 高等教育路径
        # --------------------------------------------------------------
        if profile.education_type == EducationType.HIGHER:
            higher = profile.higher
            if higher is None:
                return AllowedSourceScope(
                    education_stage="高等教育",
                    allow_regular=False,
                    scope_description="高等教育未设定",
                )

            school_name = getattr(higher, "school", "") or getattr(higher, "school_name", "")
            school_clean = school_name.strip()

            # 校验高校真实性
            if school_clean not in self._official_schools:
                return AllowedSourceScope(
                    education_stage="高等教育",
                    allow_regular=False,
                    scope_description=f"虚构高校「{school_clean}」，拒绝建立高等教育题源范围",
                )

            edu_level = higher.education_level or "本科"
            major_clean = (higher.major or "").strip()
            domains = (
                MAJOR_TO_ALLOWED_DOMAINS.get(major_clean, (major_clean,)) if major_clean else ()
            )
            subj_desc = major_clean or higher.discipline_category or "专业方向"

            return AllowedSourceScope(
                education_stage="高等教育",
                allow_regular=True,
                higher_school=school_clean,
                higher_education_level=edu_level,
                higher_major=major_clean or None,
                allowed_major_domains=domains,
                graduate_category=higher.discipline_category,
                graduate_first_level=higher.discipline_first_level,
                scope_description=f"高等教育 · {school_clean} · {edu_level} · {subj_desc}",
            )

        return AllowedSourceScope()

    def evaluate_candidate_eligibility(
        self,
        candidate: QuestionSourceCandidate,
        scope: AllowedSourceScope,
        task: TrainingTask | None = None,
    ) -> EligibilityDecision:
        """Strictly evaluate candidate question against the computed AllowedSourceScope."""
        # ==============================================================
        # 1. 跨学科隔离检查
        # ==============================================================
        if task and task.subject:
            if candidate.subject and candidate.subject != task.subject:
                return EligibilityDecision(
                    is_eligible=False,
                    reason=f"学科不匹配：任务学科为「{task.subject}」，题源学科为「{candidate.subject}」",
                )

        # ==============================================================
        # 2. 普通题保护机制（普通题绝不因用户身份自动升级为强基/竞赛）
        # ==============================================================
        has_genuine_qiangji_in_exam = "强基" in candidate.exam_or_contest_name
        has_genuine_contest_in_exam = any(
            kw in candidate.exam_or_contest_name
            for kw in (
                "全国高中数学联赛",
                "中国数学奥林匹克",
                "CMO",
                "IMO",
                "全国中学生物理竞赛",
                "CPhO",
                "IPhO",
                "全国物理竞赛",
                "全国高中学生化学竞赛",
                "CChO",
                "IChO",
                "全国化学竞赛",
                "全国中学生生物学联赛",
                "全国中学生生物学竞赛",
                "CBO",
                "IBO",
                "全国青少年信息学奥林匹克",
                "NOIP",
                "NOI",
                "CSP-J/S",
                "IOI",
                "奥赛",
                "奥林匹克竞赛",
                "冬令营",
                "集训队",
            )
        )
        is_higher_education = (
            candidate.applicable_scope.startswith("高等教育") or scope.education_stage == "高等教育"
        )

        is_standard_paper = (
            not has_genuine_qiangji_in_exam
            and not has_genuine_contest_in_exam
            and not is_higher_education
            and (
                candidate.is_regular_exam
                or any(kw in candidate.exam_or_contest_name for kw in STANDARD_EXAM_PATTERNS)
            )
        )

        if is_standard_paper:
            # 来源为常规高考试卷、期中期末或联考卷，无论用户是国决竞赛生还是普通生，强制视为普通题
            LOGGER.info(
                "普通题保护机制生效：试卷「%s」属于正规常规试卷，规范界定为普通高中题源，禁止升级",
                candidate.exam_or_contest_name,
            )
            return EligibilityDecision(
                is_eligible=True,
                normalized_scope="基础教育 · 普通",
            )

        # ==============================================================
        # 3. 强基计划题源边界核验
        # ==============================================================
        is_candidate_strong_base = not candidate.applicable_scope.startswith("高等教育") and (
            "强基" in candidate.applicable_scope or "强基" in candidate.exam_or_contest_name
        )

        if is_candidate_strong_base:
            if not scope.is_strong_base_eligible:
                return EligibilityDecision(
                    is_eligible=False,
                    reason="普通学生题源范围不包含强基计划校测试卷，严禁越界升级题源",
                )

            # 强基高校一致性校验
            cand_school = (candidate.school_name or "").strip()
            if not cand_school:
                # 尝试从试卷名称提取高校
                for univ in QIANGJI_UNIVERSITIES_AND_MAJORS:
                    if univ in candidate.exam_or_contest_name:
                        cand_school = univ
                        break

            if not cand_school:
                return EligibilityDecision(
                    is_eligible=False,
                    reason="强基试题无法确认所属高校试点单位，核验未通过，拒绝展示",
                )

            if cand_school != scope.strong_base_school:
                return EligibilityDecision(
                    is_eligible=False,
                    reason=(
                        f"强基高校不匹配：用户已设目标高校为「{scope.strong_base_school}」，"
                        f"题源来自「{cand_school}」，严禁跨校越界推荐"
                    ),
                )

            # 强基专业方向一致性校验
            cand_dir = (candidate.major_direction or candidate.subject or "").strip()
            user_subj = (scope.strong_base_subject or "").strip()
            if user_subj and cand_dir:
                is_subj_match = (
                    cand_dir in user_subj or user_subj in cand_dir or candidate.subject == user_subj
                )
                if not is_subj_match:
                    return EligibilityDecision(
                        is_eligible=False,
                        reason=(
                            f"强基专业方向不匹配：用户目标方向为「{user_subj}」，"
                            f"题源所属方向为「{cand_dir}」，严禁跨学科越界推荐"
                        ),
                    )

            return EligibilityDecision(
                is_eligible=True,
                normalized_scope="基础教育 · 强基",
            )

        # ==============================================================
        # 4. 学科竞赛题源边界核验
        # ==============================================================
        is_candidate_competition = (
            "竞赛" in candidate.applicable_scope
            or "竞赛" in candidate.exam_or_contest_name
            or candidate.contest_name is not None
            or candidate.contest_tier is not None
        )

        if is_candidate_competition:
            # 优先检查虚构/异常赛事（例如：不存在比赛）
            c_name = candidate.contest_name or candidate.exam_or_contest_name
            if "不存在" in c_name or "虚构" in c_name:
                return EligibilityDecision(
                    is_eligible=False,
                    reason=f"赛事「{c_name}」不属于全国五大学科奥赛规范赛事或官方认证体系，核验拒绝",
                )

            if not scope.is_competition_eligible:
                return EligibilityDecision(
                    is_eligible=False,
                    reason="普通学生题源范围不包含学科竞赛试题，严禁越界提供竞赛难度题目",
                )

            # 竞赛学科核验
            if candidate.subject != scope.competition_subject:
                return EligibilityDecision(
                    is_eligible=False,
                    reason=(
                        f"竞赛学科不匹配：用户备赛学科为「{scope.competition_subject}」，"
                        f"题源属于「{candidate.subject}」竞赛"
                    ),
                )

            # 竞赛层级/阶段边界核验（省级 vs 国家级）
            cand_tier = candidate.contest_tier
            if not cand_tier:
                # 从考试或试卷名称中推断层级
                comb = f"{candidate.exam_or_contest_name} {candidate.source_name}"
                nat_kw = ("决赛", "国决", "冬令营", "CMO", "CPhO", "CChO", "CBO", "NOI")
                prov_kw = ("预赛", "初赛", "复赛", "联赛", "省赛", "省一")
                intl_kw = ("集训队", "国际", "IMO", "IPhO", "IChO", "IBO", "IOI")
                if any(kw in comb for kw in nat_kw):
                    cand_tier = str(CompetitionTier.NATIONAL)
                elif any(kw in comb for kw in prov_kw):
                    cand_tier = str(CompetitionTier.PROVINCIAL)
                elif any(kw in comb for kw in intl_kw):
                    cand_tier = str(CompetitionTier.INTERNATIONAL)

            # 如果用户处于省级备赛阶段，严禁直接越级下发国家级或国际级真题
            if scope.competition_tier == CompetitionTier.PROVINCIAL:
                if cand_tier in (str(CompetitionTier.NATIONAL), str(CompetitionTier.INTERNATIONAL)):
                    return EligibilityDecision(
                        is_eligible=False,
                        reason="竞赛阶段不匹配：当前为「省级」备赛阶段，严禁直接越级检索「国家级/国际级」试题",
                    )

            return EligibilityDecision(
                is_eligible=True,
                normalized_scope="基础教育 · 竞赛",
            )

        # ==============================================================
        # 5. 高等教育专业方向边界核验
        # ==============================================================
        if scope.education_stage == "高等教育":
            user_major = scope.higher_major or ""
            # 检查是否有显式无关学科冲突（例如机器人专业推荐医学或法学）
            conflicts = UNRELATED_DISCIPLINE_CONFLICTS.get(user_major, ())
            cand_full = (
                f"{candidate.subject} {candidate.exam_or_contest_name} {candidate.question_text}"
            )
            for bad_kw in conflicts:
                if bad_kw in cand_full:
                    return EligibilityDecision(
                        is_eligible=False,
                        reason=(
                            f"专业方向不匹配：用户专业为「{user_major}」，"
                            f"题源涉及无关学科「{bad_kw}」，严禁无依据跨门类推荐"
                        ),
                    )

            # 检查专业相关性
            if scope.allowed_major_domains:
                is_domain_match = (
                    any(dm in cand_full for dm in scope.allowed_major_domains)
                    or candidate.subject in scope.allowed_major_domains
                )
                base_subjects = ("力学", "高等数学", "通用")
                if not is_domain_match and not any(kw in cand_full for kw in base_subjects):
                    # 若完全不匹配且并非基础公共课，予以隔离
                    return EligibilityDecision(
                        is_eligible=False,
                        reason=f"专业方向不匹配：试题与用户就读专业「{user_major}」的核心专业领域不相关",
                    )

            level_str = scope.higher_education_level or "本科"
            return EligibilityDecision(
                is_eligible=True,
                normalized_scope=f"高等教育 · {level_str}",
            )

        # 默认基础教育普通题源
        return EligibilityDecision(
            is_eligible=True,
            normalized_scope="基础教育 · 普通",
        )


classify_competition_stage = EligibilityRuleEngine.classify_competition_stage
validate_strong_base_target = EligibilityRuleEngine.validate_strong_base_target
