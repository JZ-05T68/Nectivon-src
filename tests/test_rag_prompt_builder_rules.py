

def test_prompt_carries_colloquial_mapping_rule_20() -> None:
    """M1R-A second residual (HY4 S1-T3/S3-T7, 2026-09-10): the correct page
    entered the context (缸径50mm 推力表 / 熔断器选型规则) but the model judged
    「圆桶一样的缸，直径五厘米」/「闸的保险丝」 as un-bindable objects and
    answered 信息不足. Content-bearing colloquial descriptions must be mapped
    to the professional wording (including unit conversion) before an
    insufficiency verdict; pure anaphora (rule 18) stays clarification-only."""
    from src.ai.rag_prompt_builder import _RAG_EXTRA_RULES

    assert "口语表述映射纪律" in _RAG_EXTRA_RULES
    assert "等价换算" in _RAG_EXTRA_RULES
    assert "不得仅因用户没有使用资料原词" in _RAG_EXTRA_RULES
    assert "本条不放松规则 17/18" in _RAG_EXTRA_RULES


def _rules() -> str:
    from src.ai.rag_prompt_builder import _RAG_EXTRA_RULES

    return _RAG_EXTRA_RULES


def test_prompt_carries_minimal_sufficient_evidence_rule_21() -> None:
    """v0.8.4 small fix: 总览表/分档表/专项表 repeat one conclusion. The
    answer must cite the single most direct source instead of flattening all
    semantically duplicate hits, while genuine multi-condition proofs keep
    every independent source."""
    rules = _rules()
    assert "最小充分证据" in rules
    assert "另有相关内容可交叉印证" in rules
    # The rule must stay domain-neutral: no traffic-law or exam vocabulary.
    assert "重点车辆" not in rules
    assert "校车" not in rules
    # And it must never cap independent evidence count.
    assert "不限制独立证据数量" in rules


def test_prompt_carries_source_term_attribution_rule_22() -> None:
    """v0.8.4 small fix: a PDF author's summarizing label is not a statutory
    term. Without reliable source-type metadata the phrasing must stay
    conservative rather than borrowing official authority."""
    rules = _rules()
    assert "来源用词归属" in rules
    assert "在这份资料的归类中" in rules
    assert "资料中表述为" in rules
    assert "法规规定" in rules  # named as a forbidden phrasing


def test_prompt_carries_condition_mapping_rule_23() -> None:
    """v0.8.4 small fix: the numeric-to-band mapping (10% → 未达到20%) is the
    key logic a user can verify, so it must be shown — briefly."""
    rules = _rules()
    assert "条件到结论的映射" in rules
    assert "未达到 20%" in rules
    assert "不要铺开完整推理过程" in rules


def test_prompt_carries_leading_question_rule_24() -> None:
    """v0.8.4 small fix (诱导错误题): a user-supplied wrong premise must be
    corrected, not confirmed."""
    rules = _rules()
    assert "指认倾向性问法" in rules
    assert "必须先明确否定" in rules
    assert "既不能先说“是 X”，也不能先说“不是 X”" in rules


def test_prompt_requires_conflict_answer_to_start_with_uncertainty() -> None:
    rules = _rules()

    assert "回答的结论首句必须从“无法确认”开始" in rules
    assert "不得先写“是该项”或“不是该项”后再改口" in rules


def test_prompt_carries_multi_act_separation_rule_25() -> None:
    """v0.8.4 small fix: multiple separately-established acts are reported
    individually and never summed unless the source says so."""
    rules = _rules()
    assert "多行为分别成立" in rules
    assert "不得自行相加" in rules
