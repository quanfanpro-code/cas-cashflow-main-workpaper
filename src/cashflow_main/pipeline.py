"""固定编排现金流量表主表工作底稿的各个已验证步骤。"""

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .adjustment_bridge import (
    AdjustmentBridgeResult,
    AdjustmentBridgeRow,
    AdjustmentRecord,
    CandidateAdjustment,
    build_adjustment_bridge,
)
from .ai_contract import DecisionCase, build_decision_case
from .audit_trace import build_audit_trace
from .completeness_check import ValidationIssue, ValidationReport, validate_completeness
from .contracts import EnterpriseType, InputManifest, NormalizedAdjustment, RunStatus
from .fact_extraction import cash_and_equivalent_control, extract_facts
from .industry_detection import detect_enterprise_type, detect_insurance_format
from .input_adapter import normalize_inputs
from .item_calculators import (
    CalculationResult,
    CashflowItemResult,
    ComponentFactResult,
    ItemComponentResult,
    calculate_items,
)
from .ledger_reconciliation import LedgerDifference, LedgerReconciliationResult, reconcile_journal_to_trial_balance
from .rule_loader import RulePack, load_rule_pack
from .statement_mapping import (
    MappingRule,
    StatementMappingError,
    StatementMapping,
    build_book_statements,
    is_control_statement_item,
    with_exact_statement_names,
)
from .storage import (
    assert_inputs_unchanged,
    atomic_write_json,
    manifest_from_dict,
    manifest_to_dict,
    snapshot_inputs,
)

ROOT = Path(__file__).parents[2]


@dataclass(frozen=True)
class RunConfig:
    manifest: InputManifest
    enterprise_type: EnterpriseType | None = None
    entity_name: str = ""
    period: str = ""


@dataclass(frozen=True)
class PipelineResult:
    run_dir: Path
    status: RunStatus
    statement_kind: str
    enterprise_type: EnterpriseType
    calculation: CalculationResult | None = None
    validation_report: ValidationReport | None = None
    ledger_reconciliation: LedgerReconciliationResult | None = None
    adjustment_bridge: AdjustmentBridgeResult | None = None
    decision_cases: tuple[DecisionCase, ...] = ()
    review_context: dict[str, object] | None = None


@dataclass(frozen=True)
class RunStatusSummary:
    run_dir: Path
    status: RunStatus
    statement_kind: str
    enterprise_type: EnterpriseType


def _registry_ids(industry: EnterpriseType, rule_name: str | None = None) -> dict[str, dict[str, object]]:
    name = rule_name or ("general_enterprise" if industry is EnterpriseType.GENERAL else industry.value)
    path = ROOT / f"references/公式核验/{name}_v1.jsonl"
    records = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            record = json.loads(line)
            records[str(record["verification_id"])] = record
    return records


def _pack(industry: EnterpriseType, rule_name: str | None = None) -> tuple[RulePack, dict]:
    name = rule_name or ("general_enterprise" if industry is EnterpriseType.GENERAL else industry.value)
    path = ROOT / f"rules/{name}_v1.json"
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    return load_rule_pack(path, _registry_ids(industry, name)), raw


def _mapping(raw: dict) -> StatementMapping:
    return StatementMapping(tuple(MappingRule(
        report_item=item["report_item"], statement=item["statement"], amount_mode=item["amount_mode"],
        account_code_prefixes=tuple(item.get("account_code_prefixes", [])),
        account_name_contains=tuple(item.get("account_name_contains", [])),
        account_name_equals=tuple(item.get("account_name_equals", [])),
    ) for item in raw.get("statement_mapping", [])))


def _adjustment(value: NormalizedAdjustment) -> AdjustmentRecord:
    return AdjustmentRecord(
        adjustment_id=value.adjustment_id,
        report_item=value.report_item,
        amount_minor=value.amount_minor,
        adjustment_type=value.adjustment_type,
        nature=value.nature,
        source_ids=value.source_ids,
    )


def _resolve_immaterial_adjustments(
    bridge: AdjustmentBridgeResult,
    book_values: dict[str, int],
    audited_values: dict[str, int],
    book_to_report: tuple[AdjustmentRecord, ...],
    audit_adjustments: tuple[AdjustmentRecord, ...],
    performance_materiality_minor: int,
) -> tuple[AdjustmentBridgeResult, tuple[DecisionCase, ...]]:
    """低于门槛的差异采用首选性质；重大现金性质冲突保留待确认。"""
    cases = []
    automatic = []
    for row in bridge.rows:
        if not row.unexplained_minor:
            continue
        candidate_ids = tuple(item.candidate_id for item in row.candidate_adjustments)
        strong_conflict = len({item.cash_nature for item in row.candidate_adjustments}) > 1
        case = build_decision_case(
            decision_id=f"UNEXPLAINED:{row.report_item}",
            amount_minor=row.unexplained_minor,
            candidate_item_ids=candidate_ids,
            performance_materiality_minor=performance_materiality_minor,
            strong_conflict=strong_conflict,
            supporting_evidence=(
                f"审定数与账面数及已提供调整之间仍差{row.unexplained_minor}分",
            ),
            contrary_evidence=(
                "现有资料不能唯一证明该差异是否改变现金类科目",
            ) if strong_conflict else (),
        )
        cases.append(case)
        if not case.human_review_required:
            preferred = row.candidate_adjustments[0]
            automatic.append(AdjustmentRecord(
                adjustment_id=f"AUTO:{row.report_item}",
                report_item=row.report_item,
                amount_minor=row.unexplained_minor,
                adjustment_type="auto_inferred",
                nature=preferred.cash_nature,
                source_ids=(case.decision_id,),
            ))
    if automatic:
        bridge = build_adjustment_bridge(
            book_values,
            audited_values,
            book_to_report + tuple(automatic),
            audit_adjustments,
        )
    return bridge, tuple(cases)


def _write_state(
    run_dir: Path,
    config: RunConfig,
    status: RunStatus,
    industry: EnterpriseType,
    statement_kind: str,
    hashes: dict[str, str],
    pending_decisions: tuple[str, ...] = (),
) -> None:
    atomic_write_json(run_dir / "state.json", {
        "run_id": run_dir.name or str(uuid.uuid4()), "status": status.value, "enterprise_type": industry.value,
        "statement_kind": statement_kind, "input_hashes": hashes, "manifest": manifest_to_dict(config.manifest),
        "entity_name": config.entity_name, "period": config.period,
        "pending_decisions": list(pending_decisions),
    })


def _component_from_dict(raw: dict[str, object]) -> ItemComponentResult:
    facts = tuple(
        ComponentFactResult(
            fact_id=str(fact["fact_id"]),
            fact_label=str(fact["fact_label"]),
            raw_amount_minor=int(fact["raw_amount_minor"]),
            applied_amount_minor=int(fact["applied_amount_minor"]),
            source_ids=tuple(str(value) for value in fact.get("source_ids", [])),
            occupancy_key=str(fact["occupancy_key"]),
            classification_evidence=tuple(str(value) for value in fact.get("classification_evidence", [])),
            supplied_tags=tuple(str(value) for value in fact.get("supplied_tags", [])),
            tag_conflicts=tuple(str(value) for value in fact.get("tag_conflicts", [])),
        )
        for fact in raw.get("fact_details", [])
    )
    return ItemComponentResult(
        rule_component_id=str(raw["rule_component_id"]),
        amount_minor=int(raw["amount_minor"]),
        fact_ids=tuple(str(value) for value in raw.get("fact_ids", [])),
        source_ids=tuple(str(value) for value in raw.get("source_ids", [])),
        occupancy_keys=tuple(str(value) for value in raw.get("occupancy_keys", [])),
        fact_details=facts,
        operation=str(raw.get("operation", "")),
        sign=int(raw.get("sign", 1)),
        source_scope=str(raw.get("source_scope", "")),
        selector=dict(raw.get("selector", {})),
        gross_or_net=str(raw.get("gross_or_net", "not_applicable")),
        noncash_exclusions=tuple(str(value) for value in raw.get("noncash_exclusions", [])),
        special_adjustments=tuple(str(value) for value in raw.get("special_adjustments", [])),
        restricted_cash_treatment=tuple(str(value) for value in raw.get("restricted_cash_treatment", [])),
        selector_label=str(raw.get("selector_label", "")),
    )


def _bridge_from_dict(raw: dict[str, object]) -> AdjustmentBridgeResult:
    def adjustment(value: dict[str, object]) -> AdjustmentRecord:
        return AdjustmentRecord(
            adjustment_id=str(value["adjustment_id"]),
            report_item=str(value["report_item"]),
            amount_minor=int(value["amount_minor"]),
            adjustment_type=str(value["adjustment_type"]),
            nature=str(value["nature"]) if value.get("nature") is not None else None,
            source_ids=tuple(str(item) for item in value.get("source_ids", [])),
        )

    def candidate(value: dict[str, object]) -> CandidateAdjustment:
        return CandidateAdjustment(
            candidate_id=str(value["candidate_id"]),
            report_item=str(value["report_item"]),
            amount_minor=int(value["amount_minor"]),
            cash_nature=str(value["cash_nature"]),
            reason=str(value["reason"]),
        )

    rows = tuple(
        AdjustmentBridgeRow(
            report_item=str(row["report_item"]),
            book_minor=int(row["book_minor"]),
            book_to_report_minor=int(row["book_to_report_minor"]),
            audit_adjustment_minor=int(row["audit_adjustment_minor"]),
            audited_minor=int(row["audited_minor"]),
            total_difference_minor=int(row["total_difference_minor"]),
            unexplained_minor=int(row["unexplained_minor"]),
            matched_adjustments=tuple(adjustment(value) for value in row.get("matched_adjustments", [])),
            candidate_adjustments=tuple(candidate(value) for value in row.get("candidate_adjustments", [])),
        )
        for row in raw.get("rows", [])
    )
    return AdjustmentBridgeResult(
        rows=rows,
        orphan_adjustments=tuple(adjustment(value) for value in raw.get("orphan_adjustments", [])),
        is_amount_reconciled=bool(raw.get("is_amount_reconciled", False)),
    )


def load_run_artifacts(run_dir: Path) -> tuple[CalculationResult, AdjustmentBridgeResult, LedgerReconciliationResult]:
    """读取准备阶段冻结的计算、账表桥和账务核对结果，供最终输出原样复用。"""
    raw = json.loads((Path(run_dir) / "calculation.json").read_text(encoding="utf-8-sig"))
    calculation_raw = raw["calculation"]
    items = tuple(
        CashflowItemResult(
            item_id=str(item["item_id"]),
            name=str(item["name"]),
            section=str(item["section"]),
            display_order=int(item["display_order"]),
            amount_minor=int(item["amount_minor"]),
            components=tuple(_component_from_dict(value) for value in item.get("components", [])),
            verification_record_id=str(item.get("verification_record_id", "")),
        )
        for item in calculation_raw.get("items", [])
    )
    enterprise = calculation_raw.get("enterprise_type")
    calculation = CalculationResult(
        items=items,
        allocated={key: tuple(str(value) for value in values) for key, values in calculation_raw.get("allocated", {}).items()},
        enterprise_type=EnterpriseType(enterprise) if enterprise else None,
    )
    bridge = _bridge_from_dict(raw["adjustment_bridge"])
    ledger_raw = raw["ledger_reconciliation"]
    ledger = LedgerReconciliationResult(
        is_reconciled=bool(ledger_raw["is_reconciled"]),
        differences=tuple(LedgerDifference(**value) for value in ledger_raw.get("differences", [])),
    )
    return calculation, bridge, ledger


def load_validation_report(run_dir: Path) -> ValidationReport:
    raw = json.loads((Path(run_dir) / "calculation.json").read_text(encoding="utf-8-sig"))["validation_report"]

    def issues(name: str) -> tuple[ValidationIssue, ...]:
        return tuple(ValidationIssue(**value) for value in raw.get(name, []))

    return ValidationReport(
        unallocated=issues("unallocated"),
        duplicate_allocations=issues("duplicate_allocations"),
        subtotal_errors=issues("subtotal_errors"),
        cash_change_difference_minor=int(raw.get("cash_change_difference_minor", 0)),
        human_review_cases=issues("human_review_cases"),
    )


def load_review_context(run_dir: Path) -> dict[str, object]:
    raw = json.loads((Path(run_dir) / "calculation.json").read_text(encoding="utf-8-sig"))
    return dict(raw.get("review_context", {}))


def _prepare_run(config: RunConfig, run_dir: Path) -> PipelineResult:
    run_dir = Path(run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    hashes = snapshot_inputs(config.manifest)
    bundle = normalize_inputs(config.manifest)
    ledger = reconcile_journal_to_trial_balance(bundle.journal_pairs, bundle.trial_balance)
    detected = detect_enterprise_type(bundle)
    industry = config.enterprise_type or detected.preferred
    if not ledger.is_reconciled:
        _write_state(run_dir, config, RunStatus.BLOCKED, industry, "无", hashes)
        atomic_write_json(run_dir / "ledger_reconciliation.json", asdict(ledger))
        return PipelineResult(run_dir, RunStatus.BLOCKED, "无", industry, ledger_reconciliation=ledger)
    insurance_format = (
        detect_insurance_format(bundle, config.period)
        if industry is EnterpriseType.INSURANCE
        else None
    )
    needs_insurance_format_confirmation = (
        industry is EnterpriseType.INSURANCE and insurance_format is None
    )
    rule_name = None
    if industry is EnterpriseType.INSURANCE:
        rule_name = "insurance_2023" if insurance_format in {None, "insurance_2023"} else "insurance"
    pack, raw = _pack(industry, rule_name)
    mapping = with_exact_statement_names(
        _mapping(raw),
        [line.item_name for line in bundle.audited_balance_sheet],
        [line.item_name for line in bundle.audited_income_statement],
    )
    book = build_book_statements(bundle.trial_balance, mapping)
    book_values = {**{k: v.current_minor for k, v in book.balance_sheet.items()}, **{k: v.current_minor for k, v in book.income_statement.items()}}
    audited_values = {
        line.item_name: line.current_minor
        for line in bundle.audited_balance_sheet + bundle.audited_income_statement
        if not is_control_statement_item(line.item_name)
    }
    book_to_report = tuple(_adjustment(value) for value in bundle.book_to_report_adjustments)
    audit_adjustments = tuple(_adjustment(value) for value in bundle.audit_adjustments)
    bridge = build_adjustment_bridge(
        book_values,
        audited_values,
        book_to_report,
        audit_adjustments,
    )
    bridge, decision_cases = _resolve_immaterial_adjustments(
        bridge,
        book_values,
        audited_values,
        book_to_report,
        audit_adjustments,
        config.manifest.performance_materiality_minor,
    )
    facts = extract_facts(bundle, bridge, industry, pack.account_groups)
    label_conflict_cases = []
    for fact in facts.values():
        conflicts = tuple(str(value) for value in fact.metadata.get("tag_conflicts", ()))
        if not conflicts:
            continue
        independent_tags = tuple(
            tag for tag in fact.tags
            if tag not in {"journal_pair", *fact.metadata.get("supplied_tags", ())}
        )
        label_conflict_cases.append(build_decision_case(
            decision_id=f"TAG_CONFLICT:{fact.fact_id}",
            amount_minor=fact.amount_minor,
            candidate_item_ids=tuple(
                [*(f"ACCOUNT_RELATION:{tag}" for tag in independent_tags),
                 *(f"SUPPLIED_TAG:{tag}" for tag in conflicts)]
            ),
            performance_materiality_minor=config.manifest.performance_materiality_minor,
            strong_conflict=True,
            supporting_evidence=tuple(
                str(value) for value in fact.metadata.get("classification_evidence", ())
            ) or ("借贷科目关系形成了独立现金流分类",),
            contrary_evidence=tuple(f"外部现流标签：{tag}" for tag in conflicts),
        ))
    issue_cost_cases = tuple(
        build_decision_case(
            decision_id=f"FINANCING_ISSUE_COST:{fact.fact_id}",
            amount_minor=fact.amount_minor,
            candidate_item_ids=("CFF-01", "CFF-02", "CFF-06"),
            performance_materiality_minor=config.manifest.performance_materiality_minor,
            strong_conflict=True,
            supporting_evidence=tuple(
                str(value) for value in fact.metadata.get("classification_evidence", ())
            ) or ("现金支付的发行费用未注明股权或债券融资性质",),
            contrary_evidence=("股权发行费用、债券发行费用和其他筹资费用的正表列示项目不同",),
        )
        for fact in facts.values()
        if "financing_issue_cost_cash_paid" in fact.tags
    )
    classification_cases = tuple(
        build_decision_case(
            decision_id=(
                f"CAPITAL_PAYMENT:{fact.fact_id}"
                if "capital_payment_ambiguous" in fact.tags
                else f"CASH_CLASSIFICATION:{fact.fact_id}"
            ),
            amount_minor=fact.amount_minor,
            candidate_item_ids=tuple(
                str(value)
                for value in fact.metadata.get("classification_candidates", ())
            ),
            performance_materiality_minor=config.manifest.performance_materiality_minor,
            strong_conflict=bool(fact.metadata.get("classification_strong_conflict")),
            supporting_evidence=tuple(
                str(value)
                for value in fact.metadata.get("classification_evidence", ())
            ),
            contrary_evidence=("现有借贷科目和摘要不能排除其他活动类别",),
        )
        for fact in facts.values()
        if len(tuple(fact.metadata.get("classification_candidates", ()))) > 1
    )
    decision_cases = (
        decision_cases
        + tuple(label_conflict_cases)
        + issue_cost_cases
        + classification_cases
    )
    calculation = calculate_items(pack, facts)
    opening, closing, restricted_cash = cash_and_equivalent_control(facts)
    uncertain_cash: dict[str, list] = {}
    for fact in facts.values():
        if not {"restricted_cash_uncertain", "cash_equivalent_uncertain"}.intersection(fact.tags):
            continue
        account_code = str(fact.metadata.get("account_code", ""))
        uncertain_cash.setdefault(account_code, []).append(fact)
    cash_decisions = []
    for account_code, account_facts in sorted(uncertain_cash.items()):
        amount = max(
            (abs(fact.amount_minor) for fact in account_facts if fact.metadata.get("kind") in {"opening", "closing"}),
            default=0,
        )
        if not amount:
            continue
        account_name = str(account_facts[0].metadata.get("account_name", "其他货币资金"))
        is_term_deposit = any("cash_equivalent_uncertain" in fact.tags for fact in account_facts)
        cash_decisions.append(build_decision_case(
            decision_id=(
                f"TERM_DEPOSIT:{account_code or account_name}"
                if is_term_deposit
                else f"RESTRICTED_CASH:{account_code or account_name}"
            ),
            amount_minor=amount,
            candidate_item_ids=("CASH_EQUIVALENT_EXCLUDE", "CASH_EQUIVALENT_INCLUDE") if is_term_deposit else ("CASH_EQUIVALENT_INCLUDE", "CASH_EQUIVALENT_EXCLUDE"),
            performance_materiality_minor=config.manifest.performance_materiality_minor,
            strong_conflict=True,
            supporting_evidence=(
                f"{account_name}未提供期限或可随时支取条件"
                if is_term_deposit
                else f"{account_name}未提供可随时支取或受限性质明细"
            ,),
            contrary_evidence=(
                "定期存款只有在符合期限短、流动性强及可随时使用等条件时才可纳入"
                if is_term_deposit
                else "其他货币资金可能包含保证金等受限用途"
            ,),
        ))
    decision_cases = decision_cases + tuple(cash_decisions)
    validation = validate_completeness(facts, calculation, pack, opening, closing)
    needs_confirmation = config.enterprise_type is None and detected.requires_confirmation
    decision_rows = [
        {
            **asdict(case),
            "confirmation_status": "待确认" if case.human_review_required else "无需确认",
        }
        for case in decision_cases
    ]
    decision_rows.extend(
        {
            "decision_id": f"ORPHAN_ADJUSTMENT:{item.adjustment_id}",
            "available_amount_minor": abs(item.amount_minor),
            "candidate_item_ids": ("更正调整对应报表项目", "补充缺失报表项目"),
            "supporting_evidence": tuple(item.source_ids) or (f"孤立调整：{item.adjustment_id}",),
            "contrary_evidence": (f"当前报表中不存在项目：{item.report_item}",),
            "strong_conflict": True,
            "human_review_required": True,
            "preferred_item_id": "更正调整对应报表项目",
            "preferred_amount_minor": item.amount_minor,
            "confirmation_status": "待确认",
        }
        for item in bridge.orphan_adjustments
    )
    if needs_confirmation:
        decision_rows.append({
            "decision_id": "INDUSTRY_CONFIRMATION",
            "available_amount_minor": 0,
            "candidate_item_ids": tuple(item.enterprise_type.value for item in detected.candidates),
            "supporting_evidence": tuple(
                f"{item.enterprise_type.value}：{item.score}分（{'、'.join(item.evidence) or '无特征'}）"
                for item in detected.candidates
            ),
            "contrary_evidence": ("企业类型识别分值或领先幅度未达到自动确认门槛",),
            "strong_conflict": True,
            "human_review_required": True,
            "preferred_item_id": industry.value,
            "preferred_amount_minor": 0,
            "confirmation_status": "待确认",
        })
    if needs_insurance_format_confirmation:
        decision_rows.append({
            "decision_id": "INSURANCE_FORMAT_CONFIRMATION",
            "available_amount_minor": 0,
            "candidate_item_ids": ("insurance_2023", "insurance_2018"),
            "supporting_evidence": ("现有报表、科目及期间不能唯一识别保险报表格式",),
            "contrary_evidence": ("两套保险现金流项目名称和适用准则不同",),
            "strong_conflict": True,
            "human_review_required": True,
            "preferred_item_id": rule_name or "insurance_2023",
            "preferred_amount_minor": 0,
            "confirmation_status": "待确认",
        })
    review_context = {
        "unmapped_accounts": [
            {
                "account_code": row.account_code,
                "account_name": row.account_name,
                "closing_minor": row.closing_balance_minor,
            }
            for row in book.unmapped_accounts
        ],
        "decision_cases": decision_rows,
        "restricted_cash": [
            {
                "period": "期初" if fact.metadata.get("kind") == "opening" else "期末",
                "account_name": str(fact.metadata.get("account_name", "")),
                "amount_minor": fact.amount_minor,
                "source_ids": list(fact.source_ids),
            }
            for fact in restricted_cash
        ],
        "unallocated_cash": [
            {
                "fact_id": fact.fact_id,
                "debit_account_name": str(fact.metadata.get("debit_account_name", "")),
                "credit_account_name": str(fact.metadata.get("credit_account_name", "")),
                "amount_minor": fact.amount_minor,
                "source_ids": list(fact.source_ids),
                "evidence": list(fact.metadata.get("classification_evidence", ())),
            }
            for issue in validation.unallocated
            if issue.fact_id
            for fact in (facts.by_id.get(issue.fact_id),)
            if fact is not None and "unclassified_cash" in fact.tags
        ],
        "ledger_differences": [asdict(item) for item in ledger.differences],
    }
    unresolved_adjustment = any(row.unexplained_minor for row in bridge.rows) or bool(bridge.orphan_adjustments)
    unresolved_decision = any(case.human_review_required for case in decision_cases)
    pending_decisions = tuple(
        [case.decision_id for case in decision_cases if case.human_review_required]
        + [f"ORPHAN_ADJUSTMENT:{item.adjustment_id}" for item in bridge.orphan_adjustments]
        + (["INDUSTRY_CONFIRMATION"] if needs_confirmation else [])
        + (["INSURANCE_FORMAT_CONFIRMATION"] if needs_insurance_format_confirmation else [])
    )
    if validation.is_blocking:
        status, kind = RunStatus.BLOCKED, "无"
    elif needs_confirmation or needs_insurance_format_confirmation or unresolved_adjustment or unresolved_decision:
        status, kind = RunStatus.PROVISIONAL, "暂编"
    else:
        status, kind = RunStatus.VALIDATED, "最终"
    _write_state(run_dir, config, status, industry, kind, hashes, pending_decisions)
    atomic_write_json(run_dir / "audit_trace.json", build_audit_trace(calculation, pack))
    atomic_write_json(run_dir / "calculation.json", {
        "calculation": asdict(calculation),
        "adjustment_bridge": asdict(bridge),
        "ledger_reconciliation": asdict(ledger),
        "decision_cases": [asdict(case) for case in decision_cases],
        "validation_report": asdict(validation),
        "review_context": review_context,
    })
    return PipelineResult(
        run_dir,
        status,
        kind,
        industry,
        calculation,
        validation,
        ledger,
        bridge,
        decision_cases,
        review_context,
    )


def prepare_run(config: RunConfig, run_dir: Path) -> PipelineResult:
    """执行准备阶段；任何异常都先保存失败现场再原样抛出。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        return _prepare_run(config, run_dir)
    except StatementMappingError as exc:
        industry = config.enterprise_type or EnterpriseType.GENERAL
        atomic_write_json(run_dir / "state.json", {
            "run_id": run_dir.name or str(uuid.uuid4()),
            "status": RunStatus.BLOCKED.value,
            "statement_kind": "无",
            "enterprise_type": industry.value,
            "manifest": manifest_to_dict(config.manifest),
            "entity_name": config.entity_name,
            "period": config.period,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "pending_decisions": ["STATEMENT_MAPPING_CONFIRMATION"],
        })
        return PipelineResult(run_dir, RunStatus.BLOCKED, "无", industry)
    except Exception as exc:
        atomic_write_json(run_dir / "state.json", {
            "run_id": run_dir.name or str(uuid.uuid4()),
            "status": RunStatus.FAILED.value,
            "statement_kind": "无",
            "enterprise_type": (config.enterprise_type or EnterpriseType.GENERAL).value,
            "manifest": manifest_to_dict(config.manifest),
            "entity_name": config.entity_name,
            "period": config.period,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        })
        raise


def get_status(run_dir: Path) -> RunStatusSummary:
    raw = json.loads((Path(run_dir) / "state.json").read_text(encoding="utf-8-sig"))
    return RunStatusSummary(Path(run_dir), RunStatus(raw["status"]), raw["statement_kind"], EnterpriseType(raw["enterprise_type"]))


def finalize_run(run_dir: Path, decisions: list[dict]) -> RunStatusSummary:
    path = Path(run_dir) / "state.json"
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    assert_inputs_unchanged(raw["input_hashes"], manifest_from_dict(raw["manifest"]))
    pending = set(raw.get("pending_decisions", []))
    confirmed = {
        str(decision.get("decision_id"))
        for decision in decisions
        if decision.get("confirmed") is True
    }
    if raw["status"] in {RunStatus.BLOCKED.value, RunStatus.FAILED.value}:
        raise ValueError(f"当前运行状态为{raw['status']}，不得输出最终现金流量表")
    if raw["status"] == RunStatus.PROVISIONAL.value:
        missing = pending - confirmed
        if missing:
            raise ValueError("仍有未确认事项：" + "、".join(sorted(missing)))
        raw["status"] = RunStatus.VALIDATED.value
        raw["statement_kind"] = "最终"
        raw["pending_decisions"] = []
        raw["decisions"] = decisions
        atomic_write_json(path, raw)
        calculation_path = Path(run_dir) / "calculation.json"
        calculation_raw = json.loads(calculation_path.read_text(encoding="utf-8-sig"))
        review_context = calculation_raw.get("review_context", {})
        for row in review_context.get("decision_cases", []):
            decision_id = str(row.get("decision_id", ""))
            if not row.get("human_review_required"):
                row["confirmation_status"] = "无需确认"
            elif decision_id in confirmed:
                row["confirmation_status"] = "已确认"
        calculation_raw["review_context"] = review_context
        atomic_write_json(calculation_path, calculation_raw)
    return get_status(run_dir)
