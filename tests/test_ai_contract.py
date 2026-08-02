import pytest

from cashflow_main.ai_contract import (
    build_decision_case,
    should_escalate,
    validate_ai_decision,
)


@pytest.mark.parametrize(
    ("amount", "materiality", "strong_conflict", "expected"),
    [(99, 100, False, False), (99, 100, True, False), (100, 100, False, False), (100, 100, True, True)],
)
def test_human_escalation_requires_both_conditions(amount, materiality, strong_conflict, expected):
    assert should_escalate(amount, materiality, strong_conflict) is expected


def test_ai_decision_requires_full_contract_and_amount_limit():
    case = build_decision_case("D1", 100, ("CFO-03", "CFI-05"), 100, True, ("证据A",), ("证据B",))
    decision = {
        "preferred_item_id": "CFO-03", "include_or_exclude": "include", "preferred_amount_minor": 100,
        "reason": "现金性质证据更支持经营活动", "supporting_evidence": ["证据A"], "contrary_evidence": ["证据B"],
        "rejected_alternatives": [{"item_id": "CFI-05", "reason": "证据较弱"}], "confidence": 0.8, "amount_impact": 100,
    }
    assert validate_ai_decision(decision, case)["preferred_item_id"] == "CFO-03"
    missing = dict(decision); missing.pop("rejected_alternatives")
    with pytest.raises(ValueError, match="rejected_alternatives"):
        validate_ai_decision(missing, case)
    too_large = dict(decision); too_large["preferred_amount_minor"] = 101
    with pytest.raises(ValueError, match="超出可用金额"):
        validate_ai_decision(too_large, case)


def test_escalated_case_still_has_provisional_preference():
    case = build_decision_case("D1", 100, ("CFO-03", "CFI-05"), 100, True, ("证据A",), ("证据B",))
    assert case.human_review_required
    assert case.preferred_item_id == "CFO-03"
    assert case.preferred_amount_minor == 100
