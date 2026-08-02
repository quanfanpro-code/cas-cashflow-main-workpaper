from cashflow_main.adjustment_bridge import (
    AdjustmentBridgeResult,
    AdjustmentBridgeRow,
    AdjustmentRecord,
)
from cashflow_main.contracts import (
    AccountBalance,
    JournalPair,
    NormalizedInputBundle,
    StatementLine,
    EnterpriseType,
)
from cashflow_main.fact_extraction import extract_facts


def test_fact_ledger_keeps_sources_and_adjustment_tags():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(StatementLine("应收账款", 800, 1000),),
        audited_income_statement=(StatementLine("营业收入", 5000),),
        trial_balance=(AccountBalance("1122", "应收账款", 1000, 300, 500, 800),),
        journal_pairs=(JournalPair("银行存款", "应收账款", 500),),
        prior_cashflow=(),
    )
    adjustment = AdjustmentRecord("ADJ:001", "应收账款", 20, "audit", "noncash")
    bridge = AdjustmentBridgeResult(
        rows=(AdjustmentBridgeRow("应收账款", 780, 0, 20, 800, 20, 0, (adjustment,), ()),),
        orphan_adjustments=(),
        is_amount_reconciled=True,
    )
    ledger = extract_facts(bundle, bridge, EnterpriseType.GENERAL, {})
    assert ledger.by_id["TB:1122:closing_change"].source_ids == ("trial_balance:1122",)
    assert ledger.by_id["TB:1122:closing_change"].occupancy_key == "TB:1122:closing_change"
    assert "noncash" in ledger.by_id["ADJ:001"].tags
    assert ledger.by_id["JP:1"].amount_minor == 500


def test_fact_ledger_uses_derived_tags_and_keeps_conflict_evidence():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(),
        journal_pairs=(JournalPair(
            "银行存款",
            "短期借款",
            500,
            {"现金流标签": "investment_acquisition_cash"},
        ),),
        prior_cashflow=(),
    )
    bridge = AdjustmentBridgeResult((), (), True)

    fact = extract_facts(bundle, bridge, EnterpriseType.GENERAL, {}).by_id["JP:1"]

    assert "short_term_borrowing_cash_received" in fact.tags
    assert "investment_acquisition_cash" not in fact.tags
    assert fact.metadata["tag_conflicts"]


def test_one_journal_pair_can_supply_distinct_workpaper_adjustments_without_shared_occupancy():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(),
        journal_pairs=(JournalPair(
            "在建工程",
            "应付职工薪酬",
            500,
            {"摘要": "计提工程人员工资"},
        ),),
        prior_cashflow=(),
    )

    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        {},
    )
    employee_expense = next(
        fact for fact in facts.values()
        if "employee_compensation_expense" in fact.tags
    )
    capex_adjustment = next(
        fact for fact in facts.values()
        if "capex_employee_cash" in fact.tags
    )

    assert employee_expense.amount_minor == 500
    assert capex_adjustment.amount_minor == 500
    assert employee_expense.occupancy_key != capex_adjustment.occupancy_key
