"""约束不确定事项的自动判断，并把人工升级压缩到双门槛情形。"""

from dataclasses import dataclass


REQUIRED_AI_FIELDS = {
    "preferred_item_id", "include_or_exclude", "preferred_amount_minor", "reason",
    "supporting_evidence", "contrary_evidence", "rejected_alternatives", "confidence", "amount_impact",
}


@dataclass(frozen=True)
class DecisionCase:
    decision_id: str
    available_amount_minor: int
    candidate_item_ids: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    contrary_evidence: tuple[str, ...]
    strong_conflict: bool
    human_review_required: bool
    preferred_item_id: str
    preferred_amount_minor: int


def should_escalate(amount_minor: int, performance_materiality_minor: int, strong_conflict: bool) -> bool:
    if performance_materiality_minor <= 0:
        raise ValueError("实际执行重要性水平必须大于0")
    return abs(amount_minor) >= performance_materiality_minor and strong_conflict


def build_decision_case(
    decision_id: str,
    amount_minor: int,
    candidate_item_ids: tuple[str, ...],
    performance_materiality_minor: int,
    strong_conflict: bool,
    supporting_evidence: tuple[str, ...] = (),
    contrary_evidence: tuple[str, ...] = (),
) -> DecisionCase:
    if not candidate_item_ids:
        raise ValueError("至少需要一个合法候选项目")
    return DecisionCase(
        decision_id, abs(amount_minor), candidate_item_ids, supporting_evidence, contrary_evidence,
        strong_conflict, should_escalate(amount_minor, performance_materiality_minor, strong_conflict),
        candidate_item_ids[0], amount_minor,
    )


def validate_ai_decision(decision: dict, case: DecisionCase) -> dict:
    missing = REQUIRED_AI_FIELDS - decision.keys()
    if missing:
        raise ValueError("AI判断缺少字段：" + "、".join(sorted(missing)))
    if decision["preferred_item_id"] not in case.candidate_item_ids:
        raise ValueError("首选项目不在候选范围")
    if decision["include_or_exclude"] not in {"include", "exclude"}:
        raise ValueError("include_or_exclude只能为include或exclude")
    if abs(int(decision["preferred_amount_minor"])) > case.available_amount_minor:
        raise ValueError("首选金额超出可用金额")
    if not str(decision["reason"]).strip() or not decision["supporting_evidence"]:
        raise ValueError("必须给出理由和支持证据")
    rejected = {item.get("item_id") for item in decision["rejected_alternatives"]}
    expected_rejected = set(case.candidate_item_ids) - {decision["preferred_item_id"]}
    if not expected_rejected <= rejected:
        raise ValueError("必须逐一解释未采用的候选项目")
    confidence = float(decision["confidence"])
    if not 0 <= confidence <= 1:
        raise ValueError("confidence必须位于0至1")
    if case.strong_conflict and not decision["contrary_evidence"]:
        raise ValueError("强冲突必须列示相反证据")
    return decision

